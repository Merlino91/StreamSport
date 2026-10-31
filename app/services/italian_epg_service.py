import asyncio
import datetime
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo
import httpx

from app.config import (
    ENABLE_ITALIAN_EPG,
    ITALIAN_EPG_CACHE_INTERVAL,
)
from app.services.tvvoo_service import tvvoo_service
from app.services.mandrakodi_service import mandrakodi_service
from app.services.team_matching import extract_team_name
from app.services.canonical_competition_service import (
    is_simulcast_event,
    get_simulcast_info,
)

logger = logging.getLogger("streamsport.italian_epg_service")


class ItalianEPGService:
    """
    Ingests official broadcast TV schedules directly from:
    1. Sky Italia REST API (https://apid.sky.it/gtv/v1/events?env=DTH)
    2. Tivùsat Guida TV REST API (https://services.tivulaguida.it/api/epg/channels/{id}/date/{date})

    Features:
    - Queries all sports and event channels concurrently with instant response (< 1s).
    - Filters out 24/7 rolling news bulletins and generic filler/magazine loops.
    - Resolves official broadcaster IDs directly to TvVoo/Vavoo live streams (No-Freeze).
    - Strict ghost event filter: excludes any event with 0 playable sources.
    - Normalizes titles, extracts structured teams, and translates national team names
      for seamless deduplication and reconciliation with TheSportsDB and ESPN.
    - Maintains a rolling cache to keep evening matches active post-midnight.
    """

    # 1. Sky Italia Channel Definitions (DTH Environment)
    SKY_SPORTS_CHANNELS: Dict[str, Dict[str, Any]] = {
        # Linear Dedicated Sports Channels
        "9097": {"name": "Sky Sport Uno", "lcn": 201, "default_silo": None},
        "9113": {"name": "Sky Sport Calcio", "lcn": 202, "default_silo": "football", "default_cat": "football"},
        "11237": {"name": "Sky Sport Tennis", "lcn": 203, "default_silo": "tennis", "default_cat": "tennis"},
        "9093": {"name": "Sky Sport Arena", "lcn": 204, "default_silo": None},
        "9116": {"name": "Sky Sport Basket", "lcn": 205, "default_silo": "basketball", "default_cat": "basket"},
        "9103": {"name": "Sky Sport Max", "lcn": 206, "default_silo": None},
        "9096": {"name": "Sky Sport F1", "lcn": 207, "default_silo": "motor-sports", "default_cat": "motori"},
        "9102": {"name": "Sky Sport MotoGP", "lcn": 208, "default_silo": "motor-sports", "default_cat": "motori"},
        "10254": {"name": "Sky Sport Golf", "lcn": 209, "default_silo": "golf", "default_cat": "altri_sport"},
        "11910": {"name": "Sky Sport Legend", "lcn": 210, "default_silo": None},
        "11909": {"name": "Sky Sport Mix", "lcn": 211, "default_silo": None},
        "6000": {"name": "SuperTennis HD", "lcn": 212, "default_silo": "tennis", "default_cat": "tennis"},
        "10013": {"name": "Sky Sport 4K", "lcn": 213, "default_silo": None},
        "11402": {"name": "DAZN 1", "lcn": 214, "default_silo": "football", "default_cat": "football"},
        "11401": {"name": "DAZN 2", "lcn": 215, "default_silo": "football", "default_cat": "football"},
        "11849": {"name": "DAZN 3", "lcn": 216, "default_silo": "football", "default_cat": "football"},
        "11850": {"name": "DAZN 4", "lcn": 217, "default_silo": "football", "default_cat": "football"},
        "807": {"name": "Rai Sport", "lcn": 227, "default_silo": None},
        "7587": {"name": "Sport TV", "lcn": 228, "default_silo": "motor-sports", "default_cat": "motori"},
        "9513": {"name": "Milan TV", "lcn": 230, "default_silo": "football", "default_cat": "football"},
        "9893": {"name": "Inter TV", "lcn": 232, "default_silo": "football", "default_cat": "football"},
        # Sky Sport Calcio Multi-Match Channels (Defensive mapping)
        "11497": {"name": "Sky Sport 251", "lcn": 251, "default_silo": "football", "default_cat": "football"},
        "11496": {"name": "Sky Sport 252", "lcn": 252, "default_silo": "football", "default_cat": "football"},
        "11494": {"name": "Sky Sport 253", "lcn": 253, "default_silo": "football", "default_cat": "football"},
        "11490": {"name": "Sky Sport 254", "lcn": 254, "default_silo": "football", "default_cat": "football"},
        "11491": {"name": "Sky Sport 255", "lcn": 255, "default_silo": "football", "default_cat": "football"},
        "11492": {"name": "Sky Sport 256", "lcn": 256, "default_silo": "football", "default_cat": "football"},
        "11386": {"name": "Sky Sport 257", "lcn": 257, "default_silo": "football", "default_cat": "football"},
        "9046": {"name": "Sky Sport 258", "lcn": 258, "default_silo": "football", "default_cat": "football"},
        "8613": {"name": "Sky Sport 259", "lcn": 259, "default_silo": "football", "default_cat": "football"},
    }

    # Sky Generalist Channels (Ingest ONLY if content is tagged as Sport)
    SKY_GENERALIST_CHANNELS: Dict[str, Dict[str, Any]] = {
        "899": {"name": "Rai 1 HD", "lcn": 101},
        "898": {"name": "Rai 2 HD", "lcn": 102},
        "897": {"name": "Rai 3 HD", "lcn": 103},
    }

    # 2. Tivùsat Channel Definitions
    TIVUSAT_SPORTS_CHANNELS: Dict[str, Dict[str, Any]] = {
        "88": {"name": "Rai Sport HD", "default_silo": None},
        "295": {"name": "SOLOCALCIO", "default_silo": "football", "default_cat": "football"},
        "277": {"name": "EQUtv", "default_silo": "altri_sport", "default_cat": "altri_sport"},
    }

    # Tivùsat Generalist Channels (Ingest ONLY if category/genre is Sport)
    TIVUSAT_GENERALIST_CHANNELS: Dict[str, Dict[str, Any]] = {
        "249": {"name": "20 Mediaset HD"},
        "279": {"name": "TV8 HD"},
        "280": {"name": "Cielo HD"},
        "32": {"name": "DMAX HD"},
    }

    # Italian to English country name translations for International National Teams
    COUNTRY_MAP: Dict[str, str] = {
        "norvegia": "Norway",
        "portogallo": "Portugal",
        "lituania": "Lithuania",
        "azerbaigian": "Azerbaijan",
        "spagna": "Spain",
        "francia": "France",
        "germania": "Germany",
        "inghilterra": "England",
        "italia": "Italy",
        "olanda": "Netherlands",
        "paesi bassi": "Netherlands",
        "croazia": "Croatia",
        "serbia": "Serbia",
        "belgio": "Belgium",
        "svizzera": "Switzerland",
        "austria": "Austria",
        "svezia": "Sweden",
        "danimarca": "Denmark",
        "polonia": "Poland",
        "repubblica ceca": "Czechia",
        "cechia": "Czechia",
        "turchia": "Turkey",
        "grecia": "Greece",
        "brasile": "Brazil",
        "argentina": "Argentina",
        "stati uniti": "USA",
        "ucraina": "Ukraine",
        "irlanda": "Ireland",
        "scozia": "Scotland",
        "galles": "Wales",
        "romania": "Romania",
        "bulgaria": "Bulgaria",
        "ungheria": "Hungary",
        "slovacchia": "Slovakia",
        "slovenia": "Slovenia",
        "finlandia": "Finland",
        "islanda": "Iceland",
        "israele": "Israel",
        "albania": "Albania",
        "macedonia del nord": "North Macedonia",
        "macedonia": "North Macedonia",
        "bosnia": "Bosnia and Herzegovina",
        "bosnia ed erzegovina": "Bosnia and Herzegovina",
        "montenegro": "Montenegro",
        "georgia": "Georgia",
        "cipro": "Cyprus",
        "malta": "Malta",
        "armenia": "Armenia",
        "moldavia": "Moldova",
        "kazakistan": "Kazakhstan",
        "lussemburgo": "Luxembourg",
        "bielorussia": "Belarus",
        "kosovo": "Kosovo",
        "estonia": "Estonia",
        "lettonia": "Latvia",
        "far oer": "Faroe Islands",
        "isole far oer": "Faroe Islands",
        "gibilterra": "Gibraltar",
        "liechtenstein": "Liechtenstein",
        "san marino": "San Marino",
        "andorra": "Andorra",
    }

    # Noise titles / Filler shows that must be filtered out
    IGNORED_TITLE_PATTERNS = [
        re.compile(r"^\s*news\s*line\b", re.I),
        re.compile(r"^\s*notiziario\b", re.I),
        re.compile(r"^\s*tg24\b", re.I),
        re.compile(r"^\s*tg\s*sport\b", re.I),
        re.compile(r"^\s*rassegna\s*stampa\b", re.I),
        re.compile(r"^\s*edizioni\s*tg\b", re.I),
        re.compile(r"^\s*supertennis\s*tg\b", re.I),
        re.compile(r"^\s*colpo\s*da\s*campione\b", re.I),
        re.compile(r"^\s*campo\s*aperto\b", re.I),
        re.compile(r"^\s*boat\s*show\b", re.I),
        re.compile(r"^\s*la\s*giovane\s*italia\b", re.I),
        re.compile(r"^\s*circolando\b", re.I),
        re.compile(r"^\s*fuoco\b", re.I),
        re.compile(r"^\s*tennis\s*atp\s*tour\s*this\s*week\b", re.I),
        re.compile(r"^\s*ep\.\s*\d+\s*$", re.I),
        re.compile(r"^\s*speciale\b", re.I),
    ]

    REPLAY_PATTERN = re.compile(
        r"(\b(?:solo\s+repl(?:ica)?|in\s+replica|replica)\b|\[(?:solo repl|replica|repl|r)\]|\((?:solo repl|replica|repl|r)\))",
        re.I,
    )

    EVENT_KEYWORDS = re.compile(
        r"\b(vs|v|\-|\bep\b|gp|gara|qualifiche|sprint|fp[1-3]|warm\s*up|practice|atp|wta|padel|masters|slam|challenger|giro|tour|maratona|mondiali|europei|giornata|sessione|finale|semifinale|quarti|round|match|open|cup|coppa|league|trophy|championship|serie\s*a\b|serie\s*b\b|serie\s*c\b|calcio|pallavolo|volley|basket|pallacanestro|rugby|judo|scherma|boxe|ufc|motogp|formula\s*1)\b",
        re.I,
    )

    MAGAZINE_NOISE = re.compile(
        r"\b(magazine|speciale|rubrica|storia|monografia|film|doc|documentario|reportage|highlights|rassegna|studio|anteprima|tg|news|debriefing|goleador|bordocam|visione di)\b",
        re.I,
    )

    SKY_SOCCER_MAP = {
        "serie-a": "Serie A",
        "serie-b": "Serie B",
        "serie-c": "Serie C",
        "premier-league": "Premier League",
        "bundesliga": "Bundesliga",
        "bundesliga-2": "2. Bundesliga",
        "2-bundesliga": "2. Bundesliga",
        "uefa-champions-league": "UEFA Champions League",
        "champions-league": "UEFA Champions League",
        "uefa-europa-league": "UEFA Europa League",
        "europa-league": "UEFA Europa League",
        "uefa-conference-league": "UEFA Conference League",
        "conference-league": "UEFA Conference League",
        "uefa-nations-league": "UEFA Nations League",
        "nations-league": "UEFA Nations League",
        "coppa-italia": "Coppa Italia",
        "supercoppa-italiana": "Supercoppa Italiana",
        "fa-cup": "FA Cup",
        "carabao-cup": "Carabao Cup",
        "efl-cup": "Carabao Cup",
        "la-liga": "LaLiga",
        "laliga": "LaLiga",
        "ligue-1": "Ligue 1",
        "copa-libertadores": "Copa Libertadores",
        "copa-sudamericana": "Copa Sudamericana",
        "fifa-world-cup": "FIFA World Cup",
    }

    SKY_TENNIS_SPECIAL_MAP = {
        "tennis-wimbledon": "Wimbledon",
        "tennis-roland-garros": "Roland Garros",
        "tennis-us-open": "US Open",
        "tennis-australian-open": "Australian Open",
        "tennis-atp-finals": "ATP Finals",
        "tennis-wta-finals": "WTA Finals",
        "tennis-coppa-davis": "Coppa Davis",
        "tennis-davis-cup": "Coppa Davis",
        "tennis-billie-jean-king-cup": "BJK Cup",
        "tennis-bjk-cup": "BJK Cup",
        "tennis-laver-cup": "Laver Cup",
        "tennis-united-cup": "United Cup",
    }

    SKY_MOTOR_MAP = {
        "f1": "Formula 1",
        "formula-1": "Formula 1",
        "formula1": "Formula 1",
        "f2": "Formula 2",
        "formula-2": "Formula 2",
        "f3": "Formula 3",
        "formula-3": "Formula 3",
        "motogp": "MotoGP",
        "moto-gp": "MotoGP",
        "moto2": "Moto2",
        "moto3": "Moto3",
        "superbike": "Superbike",
        "worldsbk": "Superbike",
        "sbk": "Superbike",
        "indycar": "IndyCar",
        "nascar": "NASCAR",
        "wec": "WEC",
        "wrc": "WRC",
    }

    SKY_BASKET_MAP = {
        "eurolega": "Eurolega",
        "euroleague": "Eurolega",
        "eurocup": "Eurocup",
        "nba": "NBA",
        "wnba": "WNBA",
        "serie-a-basket": "LBA Serie A",
        "basket-serie-a": "LBA Serie A",
        "lba": "LBA Serie A",
        "serie-a2-basket": "Serie A2 Basket",
        "bcl": "BCL",
        "fiba": "FIBA",
    }

    SKY_VOLLEY_MAP = {
        "superlega": "Superlega",
        "superlega-credembanca": "Superlega",
        "volley-superlega": "Superlega",
        "serie-a1-femminile": "Serie A1 Femminile",
        "volley-femminile": "Serie A1 Femminile",
        "cev-champions-league": "CEV Champions League",
    }

    SKY_OTHER_MAP = {
        "six-nations": "Sei Nazioni",
        "sei-nazioni": "Sei Nazioni",
        "rugby-serie-a-elite": "Serie A Elite Rugby",
        "rugby-championship": "Rugby Championship",
        "pga-tour": "PGA Tour",
        "dp-world-tour": "DP World Tour",
        "ryder-cup": "Ryder Cup",
    }

    MAGAZINE_SLUG_PATTERNS = [
        re.compile(r"-remix\b", re.I), re.compile(r"\bremix\b", re.I),
        re.compile(r"-magazine\b", re.I), re.compile(r"\bmagazine\b", re.I),
        re.compile(r"-special\b", re.I), re.compile(r"-speciale\b", re.I), re.compile(r"\bspeciale\b", re.I),
        re.compile(r"-preview\b", re.I), re.compile(r"\bpreview\b", re.I), re.compile(r"\banteprima\b", re.I),
        re.compile(r"-talk\b", re.I), re.compile(r"\btalk\b", re.I),
        re.compile(r"-stories\b", re.I), re.compile(r"-story\b", re.I),
        re.compile(r"boat-show", re.I),
        re.compile(r"this-is-padel", re.I),
        re.compile(r"atp-tour-this-week", re.I),
        re.compile(r"paddock-live", re.I),
        re.compile(r"conf-stampa", re.I), re.compile(r"conferenza-stampa", re.I),
        re.compile(r"talent-time", re.I),
        re.compile(r"\b90-10\b", re.I),
        re.compile(r"la-giovane-italia", re.I),
        re.compile(r"\brubrica\b", re.I),
        re.compile(r"matchplan", re.I),
        re.compile(r"mondo-gol", re.I),
        re.compile(r"calcio-show", re.I),
        re.compile(r"calciomercato", re.I),
    ]

    def __init__(self):
        self._cached_matches: List[Dict[str, Any]] = []
        self._last_fetch_time: float = 0.0
        self._fetch_lock = asyncio.Lock()

    @classmethod
    async def _get_active_tennis_cities(cls) -> Set[str]:
        """
        Retrieves the set of lowercase city names of active tennis tournaments today/tomorrow
        from Diretta.it / Flashscore official calendar.
        """
        cities: Set[str] = set()
        try:
            from app.services.diretta_service import diretta_service
            events = getattr(diretta_service, "_cached_events", None)
            if not events:
                try:
                    events = await asyncio.wait_for(diretta_service.get_events(), timeout=3.0)
                except Exception:
                    events = []
            for e in (events or []):
                if (e.get("_silo") or e.get("category")) == "tennis":
                    comp = e.get("competition") or e.get("canonical_competition") or ""
                    # e.g. "ATP - SINGOLARE: Vienna (Austria), cemento"
                    m = re.search(r":\s*([A-Za-z\s]+?)(?:\s*\(|,|$)", comp)
                    if m:
                        c = m.group(1).strip().lower()
                        if c and len(c) >= 3:
                            cities.add(c)
                            for sub_word in c.split():
                                if len(sub_word) >= 4:
                                    cities.add(sub_word)
                    _, c2 = cls._extract_tennis_circuit_and_city(comp)
                    if c2:
                        cities.add(c2.lower())
        except Exception as exc:
            logger.debug("Italian EPG: could not retrieve active tennis cities from Diretta: %s", exc)
        return cities

    @classmethod
    def _parse_sky_semantic_url(cls, content_url: str) -> Tuple[Optional[str], bool]:
        """
        Parses Sky Italia semantic URL hierarchy:
          /(sport|programmi-tv)/{comp_slug}/{event_slug}/{uuid}
        Returns:
          (competition_name, is_magazine_or_show)
        """
        if not content_url or not isinstance(content_url, str):
            return None, False

        m = re.search(r"/(sport|programmi-tv)/([^/]+)/([^/]+)", content_url)
        if not m:
            return None, False

        section = m.group(1).lower()
        comp_slug = m.group(2).lower()
        event_slug = m.group(3).lower()

        is_magazine = False
        slug_combined = f"{comp_slug} {event_slug}"
        if any(pat.search(slug_combined) for pat in cls.MAGAZINE_SLUG_PATTERNS):
            is_magazine = True

        if section == "programmi-tv" and not any(k in comp_slug for k in ("sport", "calcio", "tennis", "f1", "motogp", "basket", "volley")):
            is_magazine = True

        if comp_slug in cls.SKY_TENNIS_SPECIAL_MAP:
            return cls.SKY_TENNIS_SPECIAL_MAP[comp_slug], is_magazine
        if comp_slug in cls.SKY_SOCCER_MAP:
            return cls.SKY_SOCCER_MAP[comp_slug], is_magazine
        if comp_slug in cls.SKY_MOTOR_MAP:
            return cls.SKY_MOTOR_MAP[comp_slug], is_magazine
        if comp_slug in cls.SKY_BASKET_MAP:
            return cls.SKY_BASKET_MAP[comp_slug], is_magazine
        if comp_slug in cls.SKY_VOLLEY_MAP:
            return cls.SKY_VOLLEY_MAP[comp_slug], is_magazine
        if comp_slug in cls.SKY_OTHER_MAP:
            return cls.SKY_OTHER_MAP[comp_slug], is_magazine

        if comp_slug == "tennis-wta":
            return "WTA", is_magazine
        if comp_slug == "tennis-atp":
            return "ATP", is_magazine
        if comp_slug == "tennis-challenger":
            return "Challenger", is_magazine

        m_tennis = re.match(r"^tennis-(wta|atp|challenger)(?:-masters)?(?:-(\d{3,4}))?-([a-z0-9-]+)$", comp_slug)
        if m_tennis:
            circuit = m_tennis.group(1).upper()
            tier = m_tennis.group(2)
            raw_city = m_tennis.group(3)
            has_masters = "masters" in comp_slug
            city = " ".join(w.capitalize() for w in raw_city.split("-") if w not in ("atp", "wta", "men", "women"))
            if circuit == "ATP" and (has_masters or tier == "1000"):
                return f"ATP Masters 1000 {city}".strip(), is_magazine
            elif tier:
                return f"{circuit} {tier} {city}".strip(), is_magazine
            else:
                return f"{circuit} {city}".strip(), is_magazine

        if "padel" in comp_slug:
            clean_p = " ".join(w.capitalize() for w in comp_slug.replace("padel-", "").split("-"))
            return f"Padel {clean_p}".strip(), is_magazine

        clean_name = " ".join(w.capitalize() for w in comp_slug.split("-") if w not in ("live", "diretta", "sport"))
        if clean_name and len(clean_name) >= 3:
            return clean_name, is_magazine

        return None, is_magazine

    @classmethod
    def _extract_competition_from_synopsis(cls, synopsis: str) -> Optional[str]:
        """
        Fallback heuristic: extracts official competition name from Guida TV event synopsis
        when URL hierarchy or title is sparse.
        """
        if not synopsis or not isinstance(synopsis, str):
            return None
        s = synopsis.strip()

        # 1. Tennis circuit + tier + city (e.g. 'Dai campi di Tokyo, Giappone, il torneo di tennis WTA 500...')
        m_ten = re.search(r"\b(WTA|ATP)\s*(\d{3,4})?\b", s, re.I)
        if m_ten:
            circ = m_ten.group(1).upper()
            tier = m_ten.group(2)
            all_cities = re.findall(r"\b(?:di|da)\s+([A-Z][a-z]+)", s)
            city = ""
            for c in all_cities:
                if c not in ("Tennis", "Diretta", "Giappone", "Austria", "Francia", "Spagna", "Italia", "Cina", "Germania"):
                    city = c
                    break
            if tier and city:
                return f"{circ} {tier} {city}"
            elif tier:
                return f"{circ} {tier}"
            elif city:
                return f"{circ} {city}"
            return circ

        # 2. Grand Slam or special tennis tournaments
        m_slam = re.search(r"\b(Wimbledon|Roland Garros|US Open|Australian Open|Coppa Davis|BJK Cup)\b", s, re.I)
        if m_slam:
            return m_slam.group(1)

        # 3. Motorsports Grand Prix
        m_gp = re.search(r"\b(?:Gran Premio|GP)\s+(?:del\s+|della\s+|di\s+)?([A-Za-z\s]+?)(?:\s+di\s+(?:F1|Formula\s*1|MotoGP)|\s*[,.]|$)", s, re.I)
        if m_gp and any(k in s.lower() for k in ("f1", "formula 1", "motogp")):
            sport = "Formula 1" if ("f1" in s.lower() or "formula" in s.lower()) else "MotoGP"
            gp_name = m_gp.group(1).strip()
            return f"{sport}: GP {gp_name}"

        # 4. Standard Football & Basketball & Volley Leagues
        m_league = re.search(r"\b(Serie A|Serie B|Serie C|Premier League|Bundesliga|UEFA Champions League|Champions League|UEFA Europa League|Europa League|Conference League|Coppa Italia|Eurolega|Eurocup|Superlega)\b", s, re.I)
        if m_league:
            l_name = m_league.group(1).title()
            if "Champions League" in l_name:
                return "UEFA Champions League"
            if "Europa League" in l_name:
                return "UEFA Europa League"
            return l_name

        return None

    @classmethod
    def _is_replay_event(
        cls,
        raw_title: str,
        synopsis: str = "",
        content_url: str = "",
        is_live: bool = False,
        active_tennis_cities: Optional[Set[str]] = None,
    ) -> bool:
        """
        Returns True if the event is an archival replay (replica d'archivio) that
        must be discarded rather than creating a catalog card.
        """
        if is_live:
            return False

        t = (raw_title or "").strip()
        t_lower = t.lower()
        s = (synopsis or "").strip()
        s_lower = s.lower()
        url_lower = (content_url or "").lower()

        # 1. Explicit replay tags in title, synopsis, or content URL
        if cls.REPLAY_PATTERN.search(t):
            return True
        if any(k in url_lower for k in ("/replica", "-replica", "-rerun")):
            return True
        if s and re.search(r"\b(?:in\s+replica|replica\s+delle?|offre\s+in\s+replica|andat[ao]\s+in\s+onda)\b", s, re.I):
            return True

        # 2. Previous year detection (e.g. 'ATP 1000 Parigi Bercy 2025' when current year is 2026)
        now_year = datetime.datetime.now().year
        m_year = re.search(r"\b(20[12][0-9])\b", t)
        if m_year and int(m_year.group(1)) < now_year:
            return True
        m_year_syn = re.search(r"\b(20[12][0-9])\b", s)
        if m_year_syn and int(m_year_syn.group(1)) < now_year:
            return True

        # 3. Tennis tournaments: check against active tournaments on Diretta.it today
        is_tennis = any(k in t_lower for k in ("wta", "atp", "masters", "slam", "tennis", "padel"))
        if is_tennis and active_tennis_cities:
            search_text = f"{t_lower} {s_lower} {url_lower}"
            _, city = cls._extract_tennis_circuit_and_city(t)
            if not city:
                _, city = cls._extract_tennis_circuit_and_city(search_text)

            # If a specific city/venue was extracted and is NOT in active cities today:
            if city and city not in active_tennis_cities:
                return True

            # If title or URL mentions an episode of a tournament and NO active city matches:
            if re.search(r"\b(?:ep\.\s*\d+|stagione\s*\d+)\b", t, re.I):
                if not any(ac in search_text for ac in active_tennis_cities):
                    return True

        return False

    @classmethod
    def _is_differita_or_sintesi(
        cls,
        raw_title: str,
        synopsis: str = "",
        teams_dict: Optional[Dict[str, Any]] = None,
        is_live: bool = False,
        active_tennis_cities: Optional[Set[str]] = None,
        content_url: str = "",
    ) -> bool:
        """
        Determines whether an EPG sports broadcast is a genuine delayed broadcast or summary
        of an ACTIVE current tournament/competition (rather than a live broadcast or an archival replay).
        """
        if is_live:
            return False

        # If it's an archival replay, it is NOT a differita
        if cls._is_replay_event(
            raw_title,
            synopsis=synopsis,
            content_url=content_url,
            is_live=is_live,
            active_tennis_cities=active_tennis_cities,
        ):
            return False

        t_lower = (raw_title or "").lower()
        s_lower = (synopsis or "").lower()
        url_lower = (content_url or "").lower()

        # 1. Explicit differita/sintesi tags in title, synopsis, or content URL
        if any(k in t_lower for k in ("differita", "sintesi", "highlights")):
            return True
        if any(k in url_lower for k in ("/differita", "/sintesi", "-differita", "-sintesi")):
            return True
        if any(k in s_lower for k in ("in differita", "differita", "sintesi")):
            return True

        # 2. Multicourt tournament sessions without 1v1 teams (e.g. 'WTA 500 Tokyo 2026', 'WTA 250 Guangzhou 2026')
        is_session = any(k in t_lower for k in ("wta", "atp", "masters", "slam", "tennis"))
        if is_session and not is_live:
            if active_tennis_cities:
                search_text = f"{t_lower} {s_lower} {url_lower}"
                _, city = cls._extract_tennis_circuit_and_city(raw_title)
                if not city:
                    _, city = cls._extract_tennis_circuit_and_city(search_text)
                if city and city in active_tennis_cities:
                    return True
                if any(ac in search_text for ac in active_tennis_cities):
                    return True
            else:
                return True

        return False

    @classmethod
    def _is_noise_event(cls, title: str) -> bool:
        """Returns True if the title matches generic channel loops or news bulletins."""
        t = title.strip()
        if len(t) < 3:
            return True
        for pat in cls.IGNORED_TITLE_PATTERNS:
            if pat.search(t):
                return True
        return False

    @classmethod
    def _is_real_sport_event(cls, title: str, teams_dict: Optional[Dict[str, Any]]) -> bool:
        """
        Validates that an event is a genuine sporting match, race, tournament, or session,
        filtering out single-word monografie (e.g. 'Vialli', 'Cressoni'), talk shows, and magazines.
        """
        if is_simulcast_event(title):
            return True
        if teams_dict:
            return True
        if cls.MAGAZINE_NOISE.search(title):
            return False
        return bool(cls.EVENT_KEYWORDS.search(title))

    @classmethod
    def _clean_event_title(cls, title: str) -> str:
        """
        Cleans TV metadata noise:
        Strips (Solo Repl), (Replica), (Diretta), [Live], Stag. XX Ep. YY, dates, etc.
        """
        t = title.strip()
        # Clean quotes
        t = t.replace("’", "'").replace("‘", "'")
        # Strip replay / live tags
        t = re.sub(r"\s*\((?:solo repl|replica|repl|diretta|live|r)\)", "", t, flags=re.I)
        t = re.sub(r"\s*\[(?:solo repl|replica|repl|diretta|live|r)\]", "", t, flags=re.I)
        # Strip date suffixes like 12/10/2026 or del 12/10/2026
        t = re.sub(r"\s*(?:del\s+)?\d{1,2}/\d{1,2}/\d{2,4}", "", t)
        # Strip season & episode markers
        t = re.sub(r"-\s*Stag\.\s*\d+\s*Ep\.\s*\d+\s*-", "-", t, flags=re.I)
        t = re.sub(r"\bStag\.\s*\d+\b", "", t, flags=re.I)
        t = re.sub(r"\bEp\.\s*\d+\b", "", t, flags=re.I)
        # Normalize whitespace and trailing punctuation
        t = re.sub(r"\s+-\s+-", " -", t)
        t = re.sub(r"\s+", " ", t).strip(" -:")
        return t

    @classmethod
    def _extract_competition_and_details(cls, title: str) -> Tuple[str, str]:
        """
        Extracts competition header and detail body if formatted as:
        'Competition: Details' or 'Sport - Competition: Details'
        """
        t = cls._clean_event_title(title)
        comp = ""
        details = t

        if ":" in t:
            parts = t.split(":", 1)
            p0 = parts[0].strip()
            p1 = parts[1].strip()

            # Ignore pure time prefixes like '15:00'
            if not re.match(r"^\d{1,2}:\d{2}$", p0):
                # Check if p0 has sport prefix e.g. 'Pallavolo: Serie A1'
                if " - " in p0:
                    subparts = p0.split(" - ", 1)
                    comp = subparts[1].strip()
                else:
                    comp = p0
                details = p1
        elif " - " in t and not cls._is_versus_pairing(t):
            parts = t.split(" - ", 1)
            comp = parts[0].strip()
            details = parts[1].strip()

        return comp, details

    @classmethod
    def _is_versus_pairing(cls, text: str) -> bool:
        """Heuristically checks if string is a Team A - Team B pairing."""
        if " - " not in text and " vs " not in text.lower() and "-" not in text:
            return False
        if " vs " in text.lower():
            parts = re.split(r"\s+vs\s+", text, flags=re.I)
        elif " - " in text:
            parts = text.split(" - ")
        else:
            parts = text.split("-")
        if len(parts) >= 2:
            p0, p1 = parts[-2].strip(), parts[-1].strip()
            if 2 <= len(p0) <= 35 and 2 <= len(p1) <= 35:
                if not any(k in p0.lower() for k in ("gara", "sessione", "gp", "fp1", "fp2", "qualifiche")):
                    return True
        return False

    @classmethod
    def _normalize_teams_and_title(cls, comp: str, details: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Normalizes team pairings (e.g. 'Casertana-Catania' -> 'Casertana vs Catania')
        and extracts structured {home: {name: ...}, away: {name: ...}} object.
        Translates Italian national team names for international deduplication.
        """
        d = details.strip()
        teams_dict: Optional[Dict[str, Any]] = None

        if is_simulcast_event(d) or is_simulcast_event(comp):
            sim_info = get_simulcast_info(d, comp) or get_simulcast_info(comp, d)
            if sim_info:
                return sim_info["title"], None

        # Check if details has a trailing match pairing e.g. '2a giornata: Conegliano - Busto Arsizio'
        if ":" in d:
            sub = d.split(":", 1)
            if cls._is_versus_pairing(sub[1].strip()):
                extra_c = sub[0].strip()
                comp = f"{comp} - {extra_c}" if comp else extra_c
                d = sub[1].strip()

        parts = []
        if " vs " in d.lower():
            parts = [p.strip() for p in re.split(r"\s+vs\s+", d, flags=re.I) if p.strip()]
        elif " - " in d:
            parts = [p.strip() for p in d.split(" - ") if p.strip()]
        elif "-" in d:
            sub = d.split("-")
            if len(sub) == 2 and 2 <= len(sub[0].strip()) <= 35 and 2 <= len(sub[1].strip()) <= 35:
                parts = [sub[0].strip(), sub[1].strip()]

        if len(parts) >= 2:
            h_raw, a_raw = parts[-2], parts[-1]
            if not any(k in h_raw.lower() for k in ("gara", "gp", "fp1", "fp2", "qualifiche", "sessione")):
                h_name = cls.COUNTRY_MAP.get(h_raw.lower(), h_raw)
                a_name = cls.COUNTRY_MAP.get(a_raw.lower(), a_raw)
                teams_dict = {
                    "home": {"name": h_name},
                    "away": {"name": a_name},
                }
                d = f"{h_name} vs {a_name}"
                if len(parts) > 2:
                    extra_c = " - ".join(parts[:-2])
                    comp = f"{comp} - {extra_c}" if comp else extra_c

        formatted_title = f"{comp}: {d}" if comp and d and comp.lower() not in d.lower() else (d or comp)
        return formatted_title, teams_dict

    @classmethod
    def _resolve_category_and_silo(
        cls,
        channel_meta: Dict[str, Any],
        title: str,
        synopsis: str = "",
        genre_name: Optional[str] = None,
        subgenre_name: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Determines canonical category and Silo for the event using:
        1. Broadcaster default (e.g. Sky Sport Calcio -> football)
        2. Sky / Tivùsat subgenre/genre metadata
        3. Title & synopsis keyword analysis
        """
        # 1. Broadcaster default if dedicated
        def_silo = channel_meta.get("default_silo")
        def_cat = channel_meta.get("default_cat")
        if def_silo and def_cat:
            return def_cat, def_silo

        combined = f"{title} {synopsis} {genre_name or ''} {subgenre_name or ''}".lower()

        # 2. Genre / Subgenre metadata
        sg = f"{subgenre_name or ''} {genre_name or ''}".lower()
        if any(k in sg for k in ("volley", "pallavolo")):
            return "volley", "volleyball"
        if any(k in sg for k in ("basket", "pallacanestro")):
            return "basket", "basketball"
        if any(k in sg for k in ("tennis", "padel")):
            return "tennis", "tennis"
        if any(k in sg for k in ("motori", "formula", "moto")):
            return "motori", "motor-sports"
        if any(k in sg for k in ("calcio", "football", "soccer")):
            return "football", "football"
        if "rugby" in sg:
            return "altri_sport", "rugby"
        if "baseball" in sg:
            return "baseball", "baseball"
        if "ciclismo" in sg:
            return "altri_sport", "cycling"

        # 3. Keyword analysis (specific sports precede football)
        if any(k in combined for k in ("volley", "pallavolo", "superlega")):
            return "volley", "volleyball"
        if any(k in combined for k in ("basket", "nba", "eurolega", "eurocup", "lba", "pallacanestro")):
            return "basket", "basketball"
        if any(k in combined for k in ("tennis", "atp", "wta", "padel", "wimbledon", "us open", "roland garros", "australian open")):
            return "tennis", "tennis"
        if any(k in combined for k in ("f1", "motogp", "formula 1", "superbike", "gran premio", "gp indonesia", "gp singapore", "indycar", "nascar", "wec")):
            return "motori", "motor-sports"
        if any(k in combined for k in ("calcio", "champions league", "europa league", "premier league", "la liga", "bundesliga", "coppa italia", "uefa", "fifa")) or re.search(r"\bserie\s*[abc]\b", combined):
            return "football", "football"
        if any(k in combined for k in ("rugby", "sei nazioni", "six nations")):
            return "altri_sport", "rugby"
        if any(k in combined for k in ("baseball", "mlb")):
            return "baseball", "baseball"
        if any(k in combined for k in ("ciclismo", "giro d'italia", "tour de france", "vuelta")):
            return "altri_sport", "cycling"

        return "altri_sport", "altri_sport"

    async def _fetch_sky_events(
        self,
        client: httpx.AsyncClient,
        iso_from: str,
        iso_to: str,
        active_tennis_cities: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Queries Sky Italia API concurrently for each target channel."""
        matches: List[Dict[str, Any]] = []

        all_sky_channels = {**self.SKY_SPORTS_CHANNELS, **self.SKY_GENERALIST_CHANNELS}

        async def fetch_one(ch_id: str, meta: Dict[str, Any]):
            url = f"https://apid.sky.it/gtv/v1/events?from={iso_from}&to={iso_to}&pageSize=999&pageNum=0&env=DTH&channels={ch_id}"
            try:
                r = await client.get(url, timeout=12.0)
                if r.status_code == 200:
                    d = r.json()
                    events = d.get("events", []) if isinstance(d, dict) else []
                    return ch_id, meta, events
            except Exception as e:
                logger.debug("Sky API fetch failed for channel %s (%s): %s", ch_id, meta.get("name"), e)
            return ch_id, meta, []

        results = await asyncio.gather(*(fetch_one(cid, m) for cid, m in all_sky_channels.items()), return_exceptions=True)

        for res in results:
            if not isinstance(res, tuple) or len(res) != 3:
                continue
            ch_id, meta, events = res
            is_generalist = ch_id in self.SKY_GENERALIST_CHANNELS
            ch_name = meta.get("name", "Sky Sport")
            channel_matches: List[Dict[str, Any]] = []

            for ev in events:
                raw_title = ev.get("eventTitle") or ""
                synopsis = ev.get("eventSynopsis") or ""
                if not raw_title or self._is_noise_event(raw_title):
                    continue

                content = ev.get("content", {})
                content_url = content.get("url") or ""
                genre_dict = content.get("genre") or {}
                subgenre_dict = content.get("subgenre") or {}
                genre_name = genre_dict.get("name") if isinstance(genre_dict, dict) else ""
                subgenre_name = subgenre_dict.get("name") if isinstance(subgenre_dict, dict) else ""

                # Live status determination
                prog_hl = ev.get("programHighlights")
                is_live = (
                    prog_hl == "L"
                    or bool(re.search(r"\b(live|diretta)\b", raw_title, re.I))
                    or "/live-" in content_url
                    or "/diretta-" in content_url
                )

                # Semantic URL parsing: extract competition and discard editorial magazine/shows
                url_comp, is_magazine = self._parse_sky_semantic_url(content_url)
                if is_magazine:
                    logger.debug("EPG Filter: scartato magazine/studio show da URL '%s'", content_url)
                    continue

                # Replay Event Filter: discard archival replays, past years, or inactive tournaments
                if self._is_replay_event(
                    raw_title,
                    synopsis=synopsis,
                    content_url=content_url,
                    is_live=is_live,
                    active_tennis_cities=active_tennis_cities,
                ):
                    logger.debug("EPG Filter: scartata replica d'archivio '%s'", raw_title)
                    continue

                # On generalist channels (Rai 1, etc.), strictly require sports genre
                if is_generalist:
                    is_sport = (
                        genre_name.lower() in ("sport", "sport e calcio")
                        or any(k in raw_title.lower() for k in ("calcio", "partita", "coppa italia", "f1", "motogp", "nations league"))
                    )
                    if not is_sport:
                        continue

                # Parse date
                st_str = ev.get("starttime")  # ISO UTC e.g. 2026-10-13T14:30:00Z
                et_str = ev.get("endtime")
                if not st_str:
                    continue
                try:
                    dt = datetime.datetime.fromisoformat(st_str.replace("Z", "+00:00"))
                    date_ms = int(dt.timestamp() * 1000)
                    end_date_ms = (
                        int(datetime.datetime.fromisoformat(et_str.replace("Z", "+00:00")).timestamp() * 1000)
                        if et_str
                        else (date_ms + 120 * 60 * 1000)
                    )
                except Exception:
                    continue

                # Title & Teams
                comp, details = self._extract_competition_and_details(raw_title)

                # If competition is missing or generic, enrich using semantic URL or synopsis
                if not comp or comp.lower() in ("sport", "tennis", "calcio", "basket", "motori", "pallavolo", "diretta", "live") or len(comp) < 3:
                    if url_comp:
                        comp = url_comp
                    else:
                        syn_comp = self._extract_competition_from_synopsis(synopsis)
                        if syn_comp:
                            comp = syn_comp

                title, teams_dict = self._normalize_teams_and_title(comp, details)
                if not title or not self._is_real_sport_event(title, teams_dict):
                    continue

                # Differita / Sintesi detection
                is_diff = self._is_differita_or_sintesi(
                    raw_title,
                    synopsis=synopsis,
                    teams_dict=teams_dict,
                    is_live=is_live,
                    active_tennis_cities=active_tennis_cities,
                    content_url=content_url,
                )
                if is_diff and not title.startswith("[Differita / Sintesi]"):
                    title = f"[Differita / Sintesi] {title}"

                # Category & Silo
                sim_info = get_simulcast_info(title, raw_title)
                if sim_info:
                    cat, silo = sim_info["silo"], sim_info["silo"]
                else:
                    cat, silo = self._resolve_category_and_silo(meta, title, synopsis, genre_name, subgenre_name)

                # Stream Target Resolution: Check both TvVoo and Mandrakodi in-memory registries
                tvvoo_streams = tvvoo_service.get_channel_streams(sky_id=ch_id, country="Italy")
                canonical = tvvoo_service.get_canonical_from_sky_id(ch_id)
                mandra_streams = mandrakodi_service.get_channel_streams(canonical) if canonical else []
                if not tvvoo_streams and not mandra_streams:
                    # GHOST EVENT FILTER: No playable source on either TvVoo or Mandrakodi
                    continue

                sources = []
                seen_tags = set()
                for st in tvvoo_streams:
                    key = (st.get("canonical"), st.get("tag"))
                    if key not in seen_tags:
                        seen_tags.add(key)
                        sources.append({
                            "source": "tvvoo",
                            "id": st.get("canonical", "tvvoo"),
                            "name": st.get("display_name", ch_name),
                            "url": st.get("url"),
                            "tag": st.get("tag", "c"),
                            "country": "Italy",
                        })

                existing_stream_ids = {str(s.get("id")) for s in sources}
                existing_sky_ids = {str(s.get("sky_channel_id")) for s in sources if s.get("sky_channel_id")}

                for st in mandra_streams:
                    st_id = str(st.get("id") or "")
                    sky_ch = str(st.get("sky_channel_id") or "")
                    if (st_id and st_id in existing_stream_ids) or (sky_ch and sky_ch in existing_sky_ids):
                        continue
                    sources.append(dict(st))
                    if st_id:
                        existing_stream_ids.add(st_id)
                    if sky_ch:
                        existing_sky_ids.add(sky_ch)

                # Contiguous session block merging on the same channel (e.g. 4 consecutive blocks of LIVE WTA 500 Tokyo)
                if channel_matches and not teams_dict:
                    prev = channel_matches[-1]
                    if not prev.get("teams"):
                        prev_title = prev.get("title", "").strip().lower()
                        curr_title = title.strip().lower()
                        prev_end = prev.get("end_date") or (prev.get("date", 0) + 120 * 60 * 1000)
                        if prev_title == curr_title and abs(date_ms - prev_end) <= 25 * 60 * 1000 and date_ms >= prev.get("date", 0):
                            prev["end_date"] = max(prev.get("end_date", 0), end_date_ms)
                            exist_src_ids = {str(s.get("id")) for s in prev.get("sources", [])}
                            for s in sources:
                                if str(s.get("id")) not in exist_src_ids:
                                    prev["sources"].append(s)
                                    exist_src_ids.add(str(s.get("id")))
                            logger.debug("Merged contiguous EPG session block '%s' on channel %s (%s)", title, ch_id, ch_name)
                            continue

                slug_title = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                match_id = f"sky_{ch_id}_{slug_title}_{int(date_ms / 1000)}"

                # Extract official Sky 16:9 poster image from imagesMap
                sky_poster = None
                images_map = ev.get("content", {}).get("imagesMap", [])
                if isinstance(images_map, list):
                    img_dict = {
                        item.get("key"): item.get("img", {}).get("url")
                        for item in images_map if isinstance(item, dict)
                    }
                    # Filter out dead placeholder images that return HTTP 403 (e.g. SportCalcio_Scene, Generic_Scene)
                    valid_imgs = {
                        k: v for k, v in img_dict.items()
                        if v and re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}", v)
                    }
                    chosen_img = (
                        valid_imgs.get("scene_key_art")
                        or valid_imgs.get("scene")
                        or valid_imgs.get("cover_clean")
                        or valid_imgs.get("background")
                        or valid_imgs.get("cover")
                    )
                    if chosen_img:
                        sky_poster = f"https://guidatv.sky.it{chosen_img}" if chosen_img.startswith("/") else chosen_img

                match_obj: Dict[str, Any] = {
                    "id": match_id,
                    "_provider": "italian_epg",
                    "title": title,
                    "is_live": is_live,
                    "_is_live": is_live,
                    "is_differita": is_diff,
                    "_is_differita": is_diff,
                    "category": cat,
                    "_silo": silo,
                    "competition": comp,
                    "_competition": comp,
                    "date": date_ms,
                    "end_date": end_date_ms,
                    "sources": sources,
                    "popular": False,
                    "poster": sky_poster,
                    "background": sky_poster,
                    "eventSynopsis": synopsis,
                }
                if teams_dict:
                    match_obj["teams"] = teams_dict

                channel_matches.append(match_obj)

            # Sequential Channel Handover: on linear broadcast channels, clamp each event's end_date
            # to the kickoff of the next scheduled event on this channel to avoid overlapping active cards
            channel_matches.sort(key=lambda x: x.get("date", 0))
            for i in range(len(channel_matches) - 1):
                curr_ev = channel_matches[i]
                next_ev = channel_matches[i + 1]
                if curr_ev.get("end_date") and next_ev.get("date"):
                    if curr_ev["end_date"] > next_ev["date"]:
                        curr_ev["end_date"] = next_ev["date"]

            matches.extend(channel_matches)

        return matches

    async def _fetch_tivusat_events(
        self,
        client: httpx.AsyncClient,
        target_dates: List[str],
        active_tennis_cities: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Queries Tivùsat Guida TV API for sport channels across today and tomorrow."""
        matches: List[Dict[str, Any]] = []
        all_tivu_channels = {**self.TIVUSAT_SPORTS_CHANNELS, **self.TIVUSAT_GENERALIST_CHANNELS}
        try:
            tz_rome = ZoneInfo("Europe/Rome")
        except Exception:
            tz_rome = datetime.timezone(datetime.timedelta(hours=2))

        async def fetch_one(ch_id: str, meta: Dict[str, Any], date_str: str):
            url = f"https://services.tivulaguida.it/api/epg/channels/{ch_id}/date/{date_str}"
            try:
                r = await client.get(url, timeout=12.0)
                if r.status_code == 200:
                    d = r.json()
                    events = d.get("events", []) if isinstance(d, dict) else []
                    return ch_id, meta, events
            except Exception as e:
                logger.debug("Tivùsat API fetch failed for %s on %s: %s", meta.get("name"), date_str, e)
            return ch_id, meta, []

        queries = []
        for d in target_dates:
            for cid, meta in all_tivu_channels.items():
                queries.append((cid, meta, d))

        results = await asyncio.gather(*(fetch_one(c, m, d) for c, m, d in queries), return_exceptions=True)

        for res in results:
            if not isinstance(res, tuple) or len(res) != 3:
                continue
            ch_id, meta, events = res
            is_generalist = ch_id in self.TIVUSAT_GENERALIST_CHANNELS
            ch_name = meta.get("name", "Tivusat")
            channel_matches: List[Dict[str, Any]] = []

            for ev in events:
                prog = ev.get("program", {})
                raw_title = prog.get("title") or ""
                synopsis = prog.get("description") or ""
                if not raw_title or self._is_noise_event(raw_title):
                    continue

                categories = prog.get("categories") or []
                genres = prog.get("genres") or []
                cat_names = [c.get("name", "").lower() for c in categories if isinstance(c, dict)]
                genre_names = [g.get("name", "").lower() for g in genres if isinstance(g, dict)]

                is_sport = "sport" in cat_names or "sport" in genre_names or "calcio" in genre_names
                if is_generalist and not is_sport:
                    # On generalist channels, must be marked as sport
                    continue

                # Live status determination
                is_live = bool(re.search(r"\b(live|diretta)\b", raw_title, re.I))

                # Replay Event Filter: discard archival replays, past years, or inactive tournaments
                if self._is_replay_event(
                    raw_title,
                    synopsis=synopsis,
                    content_url="",
                    is_live=is_live,
                    active_tennis_cities=active_tennis_cities,
                ):
                    logger.debug("Tivusat EPG Filter: scartata replica d'archivio '%s'", raw_title)
                    continue

                # Parse date_start (Rome local string: 'DD-MM-YYYY HH:MM')
                date_str = ev.get("date_start")
                date_end_str = ev.get("date_end")
                if not date_str:
                    continue
                try:
                    dt = datetime.datetime.strptime(date_str, "%d-%m-%Y %H:%M").replace(tzinfo=tz_rome)
                    date_ms = int(dt.timestamp() * 1000)
                    end_date_ms = None
                    if date_end_str:
                        try:
                            dt_end = datetime.datetime.strptime(date_end_str, "%d-%m-%Y %H:%M").replace(tzinfo=tz_rome)
                            end_date_ms = int(dt_end.timestamp() * 1000)
                        except Exception:
                            pass
                    if not end_date_ms:
                        end_date_ms = date_ms + 120 * 60 * 1000
                except Exception:
                    continue

                # Title & Teams
                comp, details = self._extract_competition_and_details(raw_title)

                # If competition is missing or generic, enrich using synopsis
                if not comp or comp.lower() in ("sport", "tennis", "calcio", "basket", "motori", "pallavolo", "diretta", "live") or len(comp) < 3:
                    syn_comp = self._extract_competition_from_synopsis(synopsis)
                    if syn_comp:
                        comp = syn_comp

                title, teams_dict = self._normalize_teams_and_title(comp, details)
                if not title or not self._is_real_sport_event(title, teams_dict):
                    continue

                # Differita / Sintesi detection
                is_diff = self._is_differita_or_sintesi(
                    raw_title,
                    synopsis=synopsis,
                    teams_dict=teams_dict,
                    is_live=is_live,
                    active_tennis_cities=active_tennis_cities,
                )
                if is_diff and not title.startswith("[Differita / Sintesi]"):
                    title = f"[Differita / Sintesi] {title}"

                # Category & Silo
                sim_info = get_simulcast_info(title, raw_title)
                if sim_info:
                    cat, silo = sim_info["silo"], sim_info["silo"]
                else:
                    cat, silo = self._resolve_category_and_silo(
                        meta, title, synopsis,
                        genre_name=" ".join(genre_names),
                        subgenre_name=" ".join(cat_names),
                    )

                # Stream Target Resolution: Check both TvVoo and Mandrakodi in-memory registries
                tvvoo_streams = tvvoo_service.get_channel_streams(tivusat_id=ch_id, country="Italy")
                canonical = tvvoo_service.get_canonical_from_tivusat_id(ch_id)
                mandra_streams = mandrakodi_service.get_channel_streams(canonical) if canonical else []
                if not tvvoo_streams and not mandra_streams:
                    # GHOST EVENT FILTER: No playable source on either TvVoo or Mandrakodi
                    continue

                sources = []
                seen_tags = set()
                for st in tvvoo_streams:
                    key = (st.get("canonical"), st.get("tag"))
                    if key not in seen_tags:
                        seen_tags.add(key)
                        sources.append({
                            "source": "tvvoo",
                            "id": st.get("canonical", "tvvoo"),
                            "name": st.get("display_name", ch_name),
                            "url": st.get("url"),
                            "tag": st.get("tag", "c"),
                            "country": "Italy",
                        })

                existing_stream_ids = {str(s.get("id")) for s in sources}
                existing_sky_ids = {str(s.get("sky_channel_id")) for s in sources if s.get("sky_channel_id")}

                for st in mandra_streams:
                    st_id = str(st.get("id") or "")
                    sky_ch = str(st.get("sky_channel_id") or "")
                    if (st_id and st_id in existing_stream_ids) or (sky_ch and sky_ch in existing_sky_ids):
                        continue
                    sources.append(dict(st))
                    if st_id:
                        existing_stream_ids.add(st_id)
                    if sky_ch:
                        existing_sky_ids.add(sky_ch)

                # Contiguous session block merging on the same channel
                if channel_matches and not teams_dict:
                    prev = channel_matches[-1]
                    if not prev.get("teams"):
                        prev_title = prev.get("title", "").strip().lower()
                        curr_title = title.strip().lower()
                        prev_end = prev.get("end_date") or (prev.get("date", 0) + 120 * 60 * 1000)
                        if prev_title == curr_title and abs(date_ms - prev_end) <= 25 * 60 * 1000 and date_ms >= prev.get("date", 0):
                            prev["end_date"] = max(prev.get("end_date", 0), end_date_ms)
                            exist_src_ids = {str(s.get("id")) for s in prev.get("sources", [])}
                            for s in sources:
                                if str(s.get("id")) not in exist_src_ids:
                                    prev["sources"].append(s)
                                    exist_src_ids.add(str(s.get("id")))
                            logger.debug("Merged contiguous Tivusat EPG session block '%s' on channel %s (%s)", title, ch_id, ch_name)
                            continue

                slug_title = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                match_id = f"tivusat_{ch_id}_{slug_title}_{int(date_ms / 1000)}"

                # Extract Tivùsat official image if available
                tivu_poster = None
                img = prog.get("image") or (prog.get("images", {}).get("url") if isinstance(prog.get("images"), dict) else None)
                if img and str(img).startswith("http"):
                    tivu_poster = str(img)

                match_obj: Dict[str, Any] = {
                    "id": match_id,
                    "_provider": "italian_epg",
                    "title": title,
                    "is_live": is_live,
                    "_is_live": is_live,
                    "is_differita": is_diff,
                    "_is_differita": is_diff,
                    "category": cat,
                    "_silo": silo,
                    "competition": comp,
                    "_competition": comp,
                    "date": date_ms,
                    "end_date": end_date_ms,
                    "sources": sources,
                    "popular": False,
                    "poster": tivu_poster,
                    "background": tivu_poster,
                    "eventSynopsis": synopsis,
                }
                if teams_dict:
                    match_obj["teams"] = teams_dict

                channel_matches.append(match_obj)

            # Sequential Channel Handover: on linear broadcast channels, clamp each event's end_date
            # to the kickoff of the next scheduled event on this channel to avoid overlapping active cards
            channel_matches.sort(key=lambda x: x.get("date", 0))
            for i in range(len(channel_matches) - 1):
                curr_ev = channel_matches[i]
                next_ev = channel_matches[i + 1]
                if curr_ev.get("end_date") and next_ev.get("date"):
                    if curr_ev["end_date"] > next_ev["date"]:
                        curr_ev["end_date"] = next_ev["date"]

            matches.extend(channel_matches)

        return matches

    async def get_matches(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Fetches today and tomorrow's sports TV schedule from Sky Italia and Tivùsat REST APIs.
        Maps official channels directly to TvVoo No-Freeze streams.
        Returns only verified events with playable TvVoo sources (no ghost events).
        Merges with ongoing evening events from cache to prevent post-midnight dropouts.
        """
        if not ENABLE_ITALIAN_EPG:
            return []

        now = time.time()
        now_ms = now * 1000
        if not force and self._cached_matches and (now - self._last_fetch_time) < ITALIAN_EPG_CACHE_INTERVAL:
            return self._cached_matches

        async with self._fetch_lock:
            if not force and self._cached_matches and (now - self._last_fetch_time) < ITALIAN_EPG_CACHE_INTERVAL:
                return self._cached_matches

            # Ensure TvVoo channel directory is synced
            await tvvoo_service.ensure_synced()

            # Date calculation: now - 2h to tomorrow 23:59:59Z
            # Sky API strictly enforces a max 3h backward window and returns 422 if 'from' is > 3h in past.
            tz_utc = datetime.timezone.utc
            dt_now = datetime.datetime.now(tz_utc)
            dt_from = dt_now - datetime.timedelta(hours=2)
            dt_tomorrow = dt_now + datetime.timedelta(days=1)

            iso_from = dt_from.strftime("%Y-%m-%dT%H:%M:%SZ")
            iso_to = dt_tomorrow.strftime("%Y-%m-%dT23:59:59Z")

            target_dates = [
                dt_now.strftime("%Y-%m-%d"),
                dt_tomorrow.strftime("%Y-%m-%d"),
            ]

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Accept": "application/json",
            }

            active_tennis_cities = await self._get_active_tennis_cities()
            try:
                async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
                    sky_task = self._fetch_sky_events(client, iso_from, iso_to, active_tennis_cities)
                    tivu_task = self._fetch_tivusat_events(client, target_dates, active_tennis_cities)
                    sky_res, tivu_res = await asyncio.gather(sky_task, tivu_task, return_exceptions=True)

                    fetched_sky = sky_res if isinstance(sky_res, list) else []
                    fetched_tivu = tivu_res if isinstance(tivu_res, list) else []

            except Exception as e:
                logger.error("Failed to fetch Italian EPG schedules: %s", e)
                return self._cached_matches

            combined_fetched = fetched_sky + fetched_tivu

            # Internal Deduplication within Italian EPG (e.g. event simulcast on Sky Sport Uno and Tivùsat TV8)
            merged_epg: List[Dict[str, Any]] = []
            for m in combined_fetched:
                m_date = m.get("date", 0)
                m_title = m.get("title", "")
                m_silo = m.get("_silo", "")

                is_m_tv = not bool(m.get("teams"))
                duplicate = None
                for existing in merged_epg:
                    if existing.get("_silo") != m_silo:
                        continue
                    is_ex_tv = not bool(existing.get("teams"))

                    # 1. Standard concurrent simulcast (within 30m window)
                    if abs(existing.get("date", 0) - m_date) < (30 * 60 * 1000):
                        if existing.get("title") == m_title:
                            duplicate = existing
                            break
                        if m.get("teams") and existing.get("teams") and m.get("teams") == existing.get("teams"):
                            duplicate = existing
                            break

                    # 2. Non-1v1 TV program / session rerun (identical title within 24h, e.g. Abu Dhabi Judo 01:20 vs 22:15)
                    elif is_m_tv and is_ex_tv and existing.get("title") == m_title:
                        time_diff = abs(existing.get("date", 0) - m_date)
                        if time_diff <= 24 * 3600 * 1000:
                            duplicate = existing
                            if m_date < existing.get("date", 0):
                                existing["date"] = m_date
                                existing["id"] = m.get("id")
                            break

                if duplicate:
                    # Merge streams from both channels (e.g. Sky Sport Uno + TV8)
                    exist_keys = {(s.get("canonical"), s.get("tag")) for s in duplicate.get("sources", [])}
                    for s in m.get("sources", []):
                        k = (s.get("canonical"), s.get("tag"))
                        if k not in exist_keys:
                            exist_keys.add(k)
                            duplicate["sources"].append(s)
                    if not duplicate.get("poster") and m.get("poster"):
                        duplicate["poster"] = m["poster"]
                        duplicate["background"] = m.get("background") or m["poster"]
                else:
                    merged_epg.append(m)

            # Rolling Cache / Anti-Midnight Dropout
            retained_old: List[Dict[str, Any]] = []
            for prev_m in self._cached_matches:
                p_date = prev_m.get("date", 0)
                if p_date and (now_ms - p_date) < (3.5 * 3600 * 1000):
                    if not any(curr.get("id") == prev_m.get("id") for curr in merged_epg):
                        retained_old.append(prev_m)

            final_matches = retained_old + merged_epg
            self._cached_matches = final_matches
            self._last_fetch_time = now
            logger.info(
                "Italian EPG sync complete. Ingested %d official events (%d Sky, %d Tivùsat, %d merged simulcasts, %d rolling cache).",
                len(final_matches),
                len(fetched_sky),
                len(fetched_tivu),
                len(combined_fetched) - len(merged_epg),
                len(retained_old),
            )
            return self._cached_matches

    @staticmethod
    def _clean_team_str(s: str) -> str:
        import unicodedata
        s = (s or "").lower().strip()
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("utf-8")
        s = re.sub(r"\b(pallacanestro|pallacan|baloncesto|basquet|basket|bc|fc|ac|cf|sc|as|calcio|volley|pallavolo|club)\b", " ", s)
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()

    @classmethod
    def _teams_match_fuzzy(cls, t1: Optional[Dict[str, Any]], t2: Optional[Dict[str, Any]]) -> bool:
        from difflib import SequenceMatcher
        if not isinstance(t1, dict) or not isinstance(t2, dict):
            return False
        h1 = cls._clean_team_str(extract_team_name(t1.get("home")))
        a1 = cls._clean_team_str(extract_team_name(t1.get("away")))
        h2 = cls._clean_team_str(extract_team_name(t2.get("home")))
        a2 = cls._clean_team_str(extract_team_name(t2.get("away")))
        if not h1 or not a1 or not h2 or not a2:
            return False

        d1 = SequenceMatcher(None, h1, h2).ratio()
        d2 = SequenceMatcher(None, a1, a2).ratio()
        if d1 >= 0.75 and d2 >= 0.75:
            return True

        i1 = SequenceMatcher(None, h1, a2).ratio()
        i2 = SequenceMatcher(None, a1, h2).ratio()
        if i1 >= 0.75 and i2 >= 0.75:
            return True

        return False

    @staticmethod
    def _clean_tokens(title: str) -> str:
        import unicodedata
        t = (title or "").lower().strip()
        t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("utf-8")
        t = re.sub(r"\b(vs|v|\-|\bep\b|gp|gara|qualifiche|sprint|fp[1-3]|warm\s*up|practice|atp|wta|padel|masters|slam|round|match)\b", " ", t)
        t = re.sub(r"\b(fc|ac|cf|sc|as|bc|united|city|club|de|la|el|del|di)\b", " ", t)
        t = re.sub(r"[^a-z0-9]+", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    @staticmethod
    def _extract_tennis_circuit_and_city(text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Dynamically extracts tennis circuit (atp, wta, challenger, slam) and city/venue
        from a tennis match or broadcast session title/synopsis without hardcoded city dictionaries.
        """
        import unicodedata
        t = unicodedata.normalize("NFKD", (text or "").lower()).encode("ascii", "ignore").decode("utf-8")

        circuit = None
        if re.search(r"\b(wta|wta\s*1000|wta\s*500|wta\s*250|wta\s*125)\b", t):
            circuit = "wta"
        elif re.search(r"\b(challenger)\b", t):
            circuit = "challenger"
        elif re.search(r"\b(wimbledon|roland\s*garros|us\s*open|australian\s*open)\b", t):
            circuit = "slam"
        elif re.search(r"\b(atp|masters\s*1000|atp\s*500|atp\s*250|atp\s*finals|masters)\b", t):
            circuit = "atp"

        excluded = {
            "men", "women", "singles", "doubles", "singolare", "doppio", "live", "tour",
            "2025", "2026", "masters", "finals", "open", "quarti", "semifinali", "semifinal",
            "finale", "finals", "giornata", "sessione", "day", "night", "court", "campo",
            "round", "turno", "match", "diretta", "tennis"
        }
        city = None
        # Pattern 1: e.g. "ATP - Singles: Shanghai (China)" or "Tennis: ATP - Singles: Shanghai"
        m_city = re.search(r"(?:atp|wta|challenger)[^:]*?:\s*([a-z]+)", t)
        if m_city and m_city.group(1).strip() not in excluded:
            city = m_city.group(1).strip()

        # Pattern 2: e.g. "ATP Masters 1000 Shanghai - 8a giornata", "WTA 1000 Wuhan", "ATP 500 Vienna"
        if not city:
            m_city2 = re.search(
                r"\b(?:atp|wta|challenger)(?:\s+(?:masters|finals|\d{3,4}|series|tour|singles|men|women|uomini|donne))*\s+([a-z]+)",
                t,
            )
            if m_city2 and m_city2.group(1).strip() not in excluded:
                city = m_city2.group(1).strip()

        # Pattern 3: e.g. "di Shanghai, Cina" or "da Wuhan, Cina" (standard Sky EPG synopsis syntax)
        if not city:
            m_city3 = re.search(r"\b(?:di|da)\s+([a-z]+),\s*[a-z]+", t)
            if m_city3 and m_city3.group(1).strip() not in excluded:
                city = m_city3.group(1).strip()

        return circuit, city

    async def enrich_matches_with_epg_channels(self, matches: List[Dict[str, Any]]) -> int:
        """
        Uses the Italian EPG (Sky & Tivùsat) strictly as a channel resolver / mapper.
        Matches confirmed real events against live broadcast schedules and attaches
        official Italian TvVoo streams (Sky Sport, DAZN, 20 Mediaset, TV8, Rai Sport).
        NEVER creates new event cards, completely eliminating ghost and replay cards.
        """
        if not ENABLE_ITALIAN_EPG or not matches:
            return 0

        epg_events = await self.get_matches()
        if not epg_events:
            return 0

        enriched_count = 0
        from difflib import SequenceMatcher

        for m in matches:
            m_silo = (m.get("_silo") or m.get("category") or "").lower()
            m_date = m.get("date", 0)
            m_teams = m.get("teams")
            m_title = m.get("title", "")
            m_tokens = self._clean_tokens(m_title)
            m_is_1v1 = bool(isinstance(m_teams, dict) and m_teams.get("home") and m_teams.get("away"))
            m_is_sim = is_simulcast_event(m_title) or is_simulcast_event(str(m.get("id", ""))) or (m_silo == "tennis" and not m_is_1v1)

            matched_epg = None
            for ep in epg_events:
                ep_title = ep.get("title", "")
                ep_teams = ep.get("teams")
                ep_is_1v1 = bool(isinstance(ep_teams, dict) and ep_teams.get("home") and ep_teams.get("away"))
                ep_is_sim = is_simulcast_event(ep_title) or is_simulcast_event(str(ep.get("id", ""))) or ((ep.get("_silo") or ep.get("category") or "").lower() == "tennis" and not ep_is_1v1)

                # CRITICAL: A simulcast/session show and a regular 1v1 match MUST NEVER match!
                # (Unless non-SuperTennis tennis bridge matches a tournament session to 1v1 court match)
                if m_is_sim != ep_is_sim and not (m_silo == "tennis" and (ep.get("_silo") or ep.get("category") or "").lower() == "tennis"):
                    continue

                ep_silo = (ep.get("_silo") or ep.get("category") or "").lower()
                # Silo compatibility
                if m_silo and ep_silo and m_silo != ep_silo:
                    # Allow football/soccer interchangeability
                    if not (m_silo in ("football", "soccer") and ep_silo in ("football", "soccer")):
                        continue

                ep_date = ep.get("date", 0)
                if m_date and ep_date:
                    if m_is_sim and ep_is_sim:
                        if abs(m_date - ep_date) > 240 * 60 * 1000:
                            continue
                    else:
                        # Asymmetric TV window:
                        # Pre-match broadcast/studio can start up to 90 min before kickoff (m_date - ep_date up to +90m).
                        # Broadcast start after kickoff allowed only up to 20 min late (ep_date - m_date up to +20m).
                        # For motorsports, coverage can start up to 120 min before session.
                        # For tennis, tournament coverage sessions can span up to 360 min (6 hours) of play.
                        if m_silo == "tennis":
                            pre_max = 360 * 60 * 1000
                            post_max = 60 * 60 * 1000
                        elif m_silo == "motor-sports":
                            pre_max = 120 * 60 * 1000
                            post_max = 20 * 60 * 1000
                        else:
                            pre_max = 90 * 60 * 1000
                            post_max = 20 * 60 * 1000
                        time_diff = m_date - ep_date  # Positive if EPG starts before match kickoff
                        if time_diff > pre_max or time_diff < -post_max:
                            continue

                if m_is_sim and ep_is_sim:
                    if m_silo == "tennis" and ep_silo == "tennis":
                        m_eval = f"{m.get('competition', '')} {m.get('title', '')} {m.get('canonical_competition', '')}"
                        ep_eval = f"{ep.get('competition', '')} {ep.get('title', '')} {ep.get('eventSynopsis', '')}"
                        m_circ, m_city = self._extract_tennis_circuit_and_city(m_eval)
                        ep_circ, ep_city = self._extract_tennis_circuit_and_city(ep_eval)
                        circ_match = (m_circ == ep_circ) if (m_circ and ep_circ) else True
                        city_match = bool(m_city and ep_city and (m_city == ep_city or m_city in ep_eval.lower() or ep_city in m_eval.lower()))
                        if circ_match and city_match:
                            matched_epg = ep
                            break
                        continue
                    m_info = get_simulcast_info(m_title)
                    ep_info = get_simulcast_info(ep_title)
                    if m_info and ep_info and m_info["title"] == ep_info["title"]:
                        matched_epg = ep
                        break
                    continue

                # Guard: Senior broadcast match on Sky/Tivusat MUST NOT match a youth/primavera fixture
                m_is_youth = m.get("_age") == "+" or any(k in m_title.lower() for k in ("u19", "u20", "u21", "u23", "primavera", "youth"))
                ep_is_youth = any(k in ep.get("title", "").lower() for k in ("u19", "u20", "u21", "u23", "primavera", "youth"))
                if m_is_youth != ep_is_youth:
                    continue

                # Guard: Non-tennis events must not cross men's and women's competitions
                if m_silo != "tennis":
                    m_is_women = m.get("_gender") == "+" or any(k in m_title.lower() for k in ("women", "femminile", "femm", "ladies", "donne"))
                    ep_is_women = any(k in ep.get("title", "").lower() for k in ("women", "femminile", "femm", "ladies", "donne"))
                    if m_is_women != ep_is_women:
                        continue

                ep_teams = ep.get("teams")
                ep_tokens = self._clean_tokens(ep.get("title", ""))

                is_same = False
                if m_teams and ep_teams:
                    if self._teams_match_fuzzy(m_teams, ep_teams):
                        is_same = True

                if not is_same and m_tokens and ep_tokens:
                    w1 = set(m_tokens.split())
                    w2 = set(ep_tokens.split())
                    if w1 and w2:
                        common = w1 & w2
                        if len(common) >= 2 or (len(w1) == 1 and len(w2) == 1 and common):
                            is_same = True
                        elif len(w1) >= 2 and len(w2) >= 2:
                            ratio = SequenceMatcher(None, m_tokens, ep_tokens).ratio()
                            if ratio >= 0.70:
                                is_same = True

                # Competition-reinforced matching:
                # If team tokens share at least 1 significant token and competition names match
                # (e.g. 'Serie A', 'Premier League', 'Eurolega', 'Formula 1', 'MotoGP'), confirm match
                if not is_same and m_tokens and ep_tokens:
                    m_c = (m.get("competition") or m.get("canonical_competition") or "").lower()
                    ep_c = (ep.get("competition") or ep.get("_competition") or "").lower()
                    if m_c and ep_c:
                        major_comps = (
                            "serie a", "serie b", "serie c", "premier league", "la liga", "laliga",
                            "bundesliga", "ligue 1", "champions league", "europa league", "conference league",
                            "eurolega", "eurocup", "formula 1", "motogp", "superbike"
                        )
                        if any(k in m_c and k in ep_c for k in major_comps):
                            w1 = set(m_tokens.split())
                            w2 = set(ep_tokens.split())
                            if w1 & w2:
                                is_same = True

                # Tennis Tournament Session Bridge:
                # If ep is a general tournament session / main event (not a specific 1v1 match between different players)
                # and both belong to the same circuit (ATP/WTA/Challenger/Slam) and city/venue,
                # bridge the official TV channel to the match.
                # NOTE: For SuperTennis, generic tournament sessions must NEVER bridge to individual 1v1 matches;
                # individual matches receive SuperTennis strictly if Diretta.it certified it or if ep is a specific 1v1 match.
                if not is_same and m_silo == "tennis" and ep_silo == "tennis":
                    is_ep_specific_match = bool(ep_teams and ep_teams.get("home") and ep_teams.get("away"))
                    is_m_specific_match = bool(m_teams and m_teams.get("home") and m_teams.get("away"))

                    ep_sources = ep.get("sources", [])
                    is_supertennis_session = any(
                        s.get("canonical") == "supertennis" or "supertennis" in (s.get("name") or "").lower()
                        for s in ep_sources if isinstance(s, dict)
                    )

                    if is_supertennis_session and is_m_specific_match:
                        # SuperTennis 24/7 channel rule: do not bridge general tournament sessions to individual court matches
                        pass
                    elif not is_ep_specific_match:
                        m_eval = f"{m.get('competition', '')} {m.get('title', '')} {m.get('canonical_competition', '')}"
                        ep_eval = f"{ep.get('competition', '')} {ep.get('title', '')} {ep.get('eventSynopsis', '')}"
                        m_circ, m_city = self._extract_tennis_circuit_and_city(m_eval)
                        ep_circ, ep_city = self._extract_tennis_circuit_and_city(ep_eval)
                        circ_match = False
                        if m_circ and ep_circ:
                            circ_match = (m_circ == ep_circ)
                        elif m_circ or ep_circ:
                            circ_match = not ((m_circ == "wta" or "wta" in m_eval.lower()) and (ep_circ == "atp" or "atp" in ep_eval.lower())) and \
                                         not ((m_circ == "atp" or "atp" in m_eval.lower()) and (ep_circ == "wta" or "wta" in ep_eval.lower()))
                        else:
                            circ_match = True

                        city_match = False
                        if m_city and ep_city:
                            city_match = (m_city == ep_city or m_city in ep_eval.lower() or ep_city in m_eval.lower())

                        if circ_match and city_match:
                            is_same = True

                # Motorsport Session Bridge:
                # Matches Diretta motorsport session with live Sky Sport F1 / Sky Sport MotoGP broadcast
                if not is_same and m_silo == "motor-sports" and ep_silo == "motor-sports":
                    m_eval = f"{m.get('competition', '')} {m.get('title', '')}".lower()
                    ep_eval = f"{ep.get('competition', '')} {ep.get('title', '')} {ep.get('eventSynopsis', '')}".lower()
                    ep_chans = " ".join([s.get("name", "") for s in ep.get("sources", []) if isinstance(s, dict)]).lower()
                    ep_full = f"{ep_eval} {ep_chans}"

                    from app.services.thesportsdb_service import TheSportsDBService
                    m_ser = TheSportsDBService.get_motorsport_series_key(m_eval)
                    ep_ser = TheSportsDBService.get_motorsport_series_key(ep_full)

                    ser_ok = (m_ser == ep_ser) if (m_ser and ep_ser) else False
                    if not ser_ok and m_ser:
                        if m_ser == "f1" and ("f1" in ep_chans or "formula 1" in ep_full or "formula 1" in ep_eval):
                            ser_ok = True
                        elif m_ser in ("motogp", "moto2", "moto3") and ("motogp" in ep_chans or "motogp" in ep_full):
                            ser_ok = True

                    if ser_ok:
                        m_is_race = "gara" in m_eval or "race" in m_eval
                        ep_is_race = "gara" in ep_eval or "race" in ep_eval
                        m_is_qual = "qualif" in m_eval
                        ep_is_qual = "qualif" in ep_eval
                        m_is_fp = "prove liber" in m_eval or "practice" in m_eval or "fp" in m_eval or "warm up" in m_eval
                        ep_is_fp = "prove liber" in ep_eval or "practice" in ep_eval or "fp" in ep_eval or "warm up" in ep_eval

                        sess_conflict = (
                            (m_is_race and not ep_is_race and (ep_is_qual or ep_is_fp)) or
                            (m_is_qual and not ep_is_qual and (ep_is_race or ep_is_fp)) or
                            (m_is_fp and not ep_is_fp and (ep_is_race or ep_is_qual))
                        )

                        locs = [
                            "messico", "mexico", "sepang", "malesia", "malaysia", "austin", "texas",
                            "interlagos", "brasile", "brazil", "vegas", "qatar", "monza", "imola",
                            "mugello", "misano", "valencia", "barcellona", "silverstone", "spa", "belgio",
                            "zandvoort", "austria", "suzuka", "giappone", "shanghai", "cina", "bahrain",
                            "jeddah", "arabia", "miami", "monaco", "montecarlo", "montreal", "canada",
                            "baku", "azerbaijan", "singapore"
                        ]
                        loc_match = any(loc in m_eval and loc in ep_full for loc in locs)

                        if (loc_match or not any(loc in m_eval for loc in locs)) and not sess_conflict:
                            is_same = True

                if is_same:
                    matched_epg = ep
                    break

            if matched_epg:
                m["_matched_epg_id"] = matched_epg.get("id")

                is_m_1v1 = bool(
                    (isinstance(m_teams, dict) and m_teams.get("home") and m_teams.get("away"))
                    or (re.search(r"\b(vs\.?| v\. )\b", m_title, re.IGNORECASE) and not m_is_sim)
                )

                # Poster priority:
                # 1. MOTOR-SPORTS ONLY: Sky EPG locandina is primary when available
                if m_silo == "motor-sports":
                    ep_poster = matched_epg.get("poster")
                    if ep_poster and str(ep_poster).startswith("http"):
                        m["poster"] = ep_poster
                        m["background"] = matched_epg.get("background") or ep_poster
                        m["_poster_source"] = "sky"
                        if m.get("id"):
                            try:
                                from app.services.db_service import db_service
                                db_service.update_match_poster(m["id"], ep_poster)
                            except Exception:
                                pass
                # 2. ALL OTHER SPORTS: Restore strict non-1v1 rule.
                # 1v1 matches and team sports MUST NEVER inherit EPG artwork (they belong exclusively to TheSportsDB / athlete poster generators).
                elif not is_m_1v1 and matched_epg.get("poster"):
                    if not m.get("poster") or "unknown" in str(m.get("poster")).lower() or "nostream" in str(m.get("poster")).lower():
                        m["poster"] = matched_epg["poster"]
                        if not m.get("background"):
                            m["background"] = matched_epg.get("background") or matched_epg["poster"]

                # If m is an aggregated multicourt event adopting a certified Sky tournament session:
                ep_teams_obj = matched_epg.get("teams")
                is_ep_1v1 = bool(isinstance(ep_teams_obj, dict) and ep_teams_obj.get("home") and ep_teams_obj.get("away"))
                if not is_m_1v1 and not is_ep_1v1:
                    if m_silo != "motor-sports":
                        m["title"] = matched_epg.get("title") or m.get("title")
                    if matched_epg.get("competition") and not m.get("competition"):
                        m["competition"] = matched_epg["competition"]
                    if matched_epg.get("eventSynopsis"):
                        m["eventSynopsis"] = matched_epg["eventSynopsis"]
                    m["_certified_by_sky"] = True
                elif matched_epg.get("eventSynopsis"):
                    m["eventSynopsis"] = matched_epg["eventSynopsis"]

                existing_sources = m.setdefault("sources", [])
                existing_keys = {
                    (s.get("canonical", s.get("id")), s.get("tag", ""))
                    for s in existing_sources if isinstance(s, dict)
                }
                new_sources = []
                for s in matched_epg.get("sources", []):
                    k = (s.get("canonical", s.get("id")), s.get("tag", ""))
                    if k not in existing_keys:
                        existing_keys.add(k)
                        new_sources.append(s)

                if new_sources:
                    # Prepend new EPG sources in their natural order without inverting
                    m["sources"] = new_sources + existing_sources
                    enriched_count += 1

        if enriched_count > 0:
            logger.info("Italian EPG: agganciati flussi FHD ufficiali a %d match confermati.", enriched_count)
        return enriched_count

    def filter_conflicting_italian_channels(self, matches: List[Dict[str, Any]]) -> int:
        """
        Purges palpably incorrect Italian TV channels from matches.
        If an external provider (like DaddyLive) attributes an Italian sports channel
        (e.g. Sky Sport Max) to a match (e.g. Latina vs Atalanta U23), but the official
        Italian EPG shows a completely different, conflicting sporting event on that channel
        (e.g. Villarreal CF vs Napoli) and Diretta.it does not list it, the channel source
        is purged.

        Rules:
        - Only purges when the program is PALPABLY CONFLICTING (palesemente errato).
        - If there is uncertainty, missing EPG data, future date, generic container show
          (e.g. Diretta Gol, Studio, Calcio Show), or Diretta.it confirmation, THE CHANNEL IS KEPT.
        """
        if not self._cached_matches or not matches:
            return 0

        from difflib import SequenceMatcher
        total_purged = 0

        GENERIC_CONTAINERS = (
            "diretta gol", "zona gol", "zona dazn", "zona serie a", "zona serie b",
            "studio", "speciale", "mondo gol", "magazine", "highlights", "tg24",
            "sport24", "rubrica", "calcio show", "sky calcio club", "tutto il calcio",
            "linea notte", "anteprima"
        )

        for m in matches:
            sources = m.get("sources")
            if not isinstance(sources, list) or not sources:
                continue

            m_date = m.get("date") or 0
            if not m_date:
                continue

            m_title = m.get("title") or ""
            m_silo = (m.get("_silo") or m.get("category") or "").lower()
            m_tokens = set(self._clean_tokens(m_title).split())
            m_teams = m.get("teams")

            # Check channels confirmed by Diretta.it
            diretta_tvs = m.get("_diretta_tv_channels") or []
            diretta_canon_keys = set()
            for dtv in diretta_tvs:
                k = tvvoo_service.get_canonical_key(dtv, country="Italy")
                if k:
                    diretta_canon_keys.add(k)

            cleaned_sources = []
            for s in sources:
                if not isinstance(s, dict):
                    cleaned_sources.append(s)
                    continue

                s_name = s.get("name") or ""
                # Resolve Italian canonical key
                canon_ita = tvvoo_service.get_canonical_key(s_name, country="Italy")
                if not canon_ita and (s.get("country") or "").lower() == "italy":
                    canon_ita = s.get("id")

                # If NOT an Italian TV channel -> we don't have EPG for foreign channels (ignoranza) -> KEEP!
                if not canon_ita:
                    cleaned_sources.append(s)
                    continue

                # If confirmed by Diretta.it -> 100% verified -> KEEP!
                if canon_ita in diretta_canon_keys or any(canon_ita in dtv.lower() for dtv in diretta_tvs):
                    cleaned_sources.append(s)
                    continue

                # Search Italian EPG for any broadcast on this channel active during the match
                conflicting_event = None
                has_matching_or_ambiguous = False
                has_any_epg_on_channel = False

                for ep in self._cached_matches:
                    ep_chans = {
                        tvvoo_service.get_canonical_key(st.get("name"), country="Italy") or st.get("id")
                        for st in ep.get("sources", [])
                        if isinstance(st, dict)
                    }
                    if canon_ita not in ep_chans:
                        continue

                    ep_date = ep.get("date") or 0
                    if not (m_date and ep_date):
                        continue
                    # Asymmetric TV window alignment:
                    # Pre-match broadcast/studio up to 90 min before kickoff (120 min for motorsports).
                    # Broadcast starting after kickoff allowed up to 20 min late.
                    pre_max = (120 * 60 * 1000) if m_silo == "motor-sports" else (90 * 60 * 1000)
                    post_max = 20 * 60 * 1000
                    time_diff = m_date - ep_date  # Positive if EPG program starts before match kickoff
                    if time_diff > pre_max or time_diff < -post_max:
                        continue

                    has_any_epg_on_channel = True
                    ep_title = ep.get("title") or ""
                    ep_title_lower = ep_title.lower()

                    # 1. Container / studio / multi-match broadcast?
                    if any(gen in ep_title_lower for gen in GENERIC_CONTAINERS):
                        # DAZN linear channels safeguard: a specific 1v1 match cannot match a generic container like 'zona dazn'
                        if canon_ita in ("dazn 1", "dazn 2", "dazn 3", "dazn 4") and not is_simulcast_event(m_title):
                            conflicting_event = ep
                            continue
                        has_matching_or_ambiguous = True
                        break

                    # 2. Team match
                    ep_teams = ep.get("teams")
                    if m_teams and ep_teams and self._teams_match_fuzzy(m_teams, ep_teams):
                        has_matching_or_ambiguous = True
                        break

                    # 3. Token match
                    ep_tokens = set(self._clean_tokens(ep_title).split())
                    common = m_tokens & ep_tokens
                    if common:
                        has_matching_or_ambiguous = True
                        break

                    # 4. Sequence similarity
                    sim = SequenceMatcher(None, self._clean_tokens(m_title), self._clean_tokens(ep_title)).ratio()
                    if sim >= 0.50:
                        has_matching_or_ambiguous = True
                        break

                    # 5. Tennis on Tennis channel safeguard: if same tournament or both tennis court sessions
                    ep_silo = (ep.get("_silo") or ep.get("category") or "").lower()
                    if m_silo == "tennis" and ep_silo == "tennis":
                        has_matching_or_ambiguous = True
                        break

                    # Distinct conflicting event found!
                    conflicting_event = ep
                    break

                if not has_any_epg_on_channel:
                    # Incertezza / ignoranza: EPG does not have programs for this channel at this time -> KEEP!
                    cleaned_sources.append(s)
                elif has_matching_or_ambiguous:
                    # Matched or ambiguous -> KEEP!
                    cleaned_sources.append(s)
                elif conflicting_event:
                    # PALESEMENTE ERRATO! Conflicting event confirmed on this exact channel!
                    logger.info(
                        "Filtro Palinsesto EPG: Scartato canale '%s' per '%s' (in onda: '%s' alle %s).",
                        s_name, m_title, conflicting_event.get("title"),
                        datetime.datetime.fromtimestamp((conflicting_event.get("date") or 0) / 1000).strftime("%H:%M")
                    )
                    total_purged += 1
                else:
                    cleaned_sources.append(s)

            m["sources"] = cleaned_sources

        if total_purged > 0:
            logger.info("Filtro Palinsesto EPG: bonificate %d sorgenti di canali TV italiani palesemente errate.", total_purged)

        return total_purged


italian_epg_service = ItalianEPGService()
