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
        re.compile(r"^\s*campo\s*aperto\b", re.I),
        re.compile(r"^\s*boat\s*show\b", re.I),
        re.compile(r"^\s*la\s*giovane\s*italia\b", re.I),
        re.compile(r"^\s*ep\.\s*\d+\s*$", re.I),
        re.compile(r"^\s*speciale\b", re.I),
    ]

    EVENT_KEYWORDS = re.compile(
        r"\b(vs|v|\-|\bep\b|gp|gara|qualifiche|sprint|fp[1-3]|warm\s*up|practice|atp|wta|padel|masters|slam|challenger|giro|tour|maratona|mondiali|europei|giornata|sessione|finale|semifinale|quarti|round|match|open|cup|coppa|league|trophy|championship|serie\s*a\b|serie\s*b\b|serie\s*c\b|calcio|pallavolo|volley|basket|pallacanestro|rugby|judo|scherma|boxe|ufc|motogp|formula\s*1)\b",
        re.I,
    )

    MAGAZINE_NOISE = re.compile(
        r"\b(magazine|speciale|rubrica|storia|monografia|film|doc|documentario|reportage|highlights|rassegna|studio|anteprima|tg|news|debriefing|goleador|bordocam|visione di)\b",
        re.I,
    )

    def __init__(self):
        self._cached_matches: List[Dict[str, Any]] = []
        self._last_fetch_time: float = 0.0
        self._fetch_lock = asyncio.Lock()

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

    async def _fetch_sky_events(self, client: httpx.AsyncClient, iso_from: str, iso_to: str) -> List[Dict[str, Any]]:
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

            for ev in events:
                raw_title = ev.get("eventTitle") or ""
                if not raw_title or self._is_noise_event(raw_title):
                    continue

                content = ev.get("content", {})
                genre_dict = content.get("genre") or {}
                subgenre_dict = content.get("subgenre") or {}
                genre_name = genre_dict.get("name") if isinstance(genre_dict, dict) else ""
                subgenre_name = subgenre_dict.get("name") if isinstance(subgenre_dict, dict) else ""

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
                if not st_str:
                    continue
                try:
                    dt = datetime.datetime.fromisoformat(st_str.replace("Z", "+00:00"))
                    date_ms = int(dt.timestamp() * 1000)
                except Exception:
                    continue

                # Title & Teams
                comp, details = self._extract_competition_and_details(raw_title)
                title, teams_dict = self._normalize_teams_and_title(comp, details)
                if not title or not self._is_real_sport_event(title, teams_dict):
                    continue

                # Category & Silo
                synopsis = ev.get("eventSynopsis") or ""
                cat, silo = self._resolve_category_and_silo(meta, title, synopsis, genre_name, subgenre_name)

                # TvVoo Stream Target Resolution
                tvvoo_streams = tvvoo_service.get_channel_streams(sky_id=ch_id, country="Italy")
                if not tvvoo_streams:
                    # GHOST EVENT FILTER: No playable source
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

                slug_title = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                match_id = f"sky_{ch_id}_{slug_title}_{int(date_ms / 1000)}"

                match_obj: Dict[str, Any] = {
                    "id": match_id,
                    "title": title,
                    "category": cat,
                    "_silo": silo,
                    "competition": comp,
                    "_competition": comp,
                    "date": date_ms,
                    "sources": sources,
                    "popular": False,
                    "poster": None,
                }
                if teams_dict:
                    match_obj["teams"] = teams_dict

                matches.append(match_obj)

        return matches

    async def _fetch_tivusat_events(self, client: httpx.AsyncClient, target_dates: List[str]) -> List[Dict[str, Any]]:
        """Queries Tivùsat Guida TV API for sport channels across today and tomorrow."""
        matches: List[Dict[str, Any]] = []
        all_tivu_channels = {**self.TIVUSAT_SPORTS_CHANNELS, **self.TIVUSAT_GENERALIST_CHANNELS}
        tz_rome = ZoneInfo("Europe/Rome")

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

            for ev in events:
                prog = ev.get("program", {})
                raw_title = prog.get("title") or ""
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

                # Parse date_start (Rome local string: 'DD-MM-YYYY HH:MM')
                date_str = ev.get("date_start")
                if not date_str:
                    continue
                try:
                    dt = datetime.datetime.strptime(date_str, "%d-%m-%Y %H:%M").replace(tzinfo=tz_rome)
                    date_ms = int(dt.timestamp() * 1000)
                except Exception:
                    continue

                # Title & Teams
                comp, details = self._extract_competition_and_details(raw_title)
                title, teams_dict = self._normalize_teams_and_title(comp, details)
                if not title or not self._is_real_sport_event(title, teams_dict):
                    continue

                # Category & Silo
                synopsis = prog.get("description") or ""
                cat, silo = self._resolve_category_and_silo(
                    meta, title, synopsis,
                    genre_name=" ".join(genre_names),
                    subgenre_name=" ".join(cat_names),
                )

                # TvVoo Stream Target Resolution
                tvvoo_streams = tvvoo_service.get_channel_streams(tivusat_id=ch_id, country="Italy")
                if not tvvoo_streams:
                    # GHOST EVENT FILTER
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

                slug_title = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                match_id = f"tivusat_{ch_id}_{slug_title}_{int(date_ms / 1000)}"

                match_obj: Dict[str, Any] = {
                    "id": match_id,
                    "title": title,
                    "category": cat,
                    "_silo": silo,
                    "competition": comp,
                    "_competition": comp,
                    "date": date_ms,
                    "sources": sources,
                    "popular": False,
                    "poster": None,
                }
                if teams_dict:
                    match_obj["teams"] = teams_dict

                matches.append(match_obj)

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

            # Date calculation: now - 3.5h to tomorrow 23:59:59Z
            tz_utc = datetime.timezone.utc
            dt_now = datetime.datetime.now(tz_utc)
            dt_from = dt_now - datetime.timedelta(hours=3.5)
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

            try:
                async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
                    sky_task = self._fetch_sky_events(client, iso_from, iso_to)
                    tivu_task = self._fetch_tivusat_events(client, target_dates)
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

                duplicate = None
                for existing in merged_epg:
                    if existing.get("_silo") != m_silo:
                        continue
                    if abs(existing.get("date", 0) - m_date) < (30 * 60 * 1000):
                        # Same time window and matching title/teams
                        if existing.get("title") == m_title:
                            duplicate = existing
                            break
                        # Check structured teams match
                        if m.get("teams") and existing.get("teams") and m.get("teams") == existing.get("teams"):
                            duplicate = existing
                            break

                if duplicate:
                    # Merge streams from both channels (e.g. Sky Sport Uno + TV8)
                    exist_keys = {(s.get("canonical"), s.get("tag")) for s in duplicate.get("sources", [])}
                    for s in m.get("sources", []):
                        k = (s.get("canonical"), s.get("tag"))
                        if k not in exist_keys:
                            exist_keys.add(k)
                            duplicate["sources"].append(s)
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
        h1 = cls._clean_team_str(t1.get("home", {}).get("name") or "")
        a1 = cls._clean_team_str(t1.get("away", {}).get("name") or "")
        h2 = cls._clean_team_str(t2.get("home", {}).get("name") or "")
        a2 = cls._clean_team_str(t2.get("away", {}).get("name") or "")
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

            matched_epg = None
            for ep in epg_events:
                ep_silo = (ep.get("_silo") or ep.get("category") or "").lower()
                # Silo compatibility
                if m_silo and ep_silo and m_silo != ep_silo:
                    # Allow football/soccer interchangeability
                    if not (m_silo in ("football", "soccer") and ep_silo in ("football", "soccer")):
                        continue

                ep_date = ep.get("date", 0)
                # Time window: +-45m (or +-60m for motorsports)
                max_offset = (60 * 60 * 1000) if m_silo == "motor-sports" else (45 * 60 * 1000)
                if m_date and ep_date and abs(m_date - ep_date) > max_offset:
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

                if is_same:
                    matched_epg = ep
                    break

            if matched_epg:
                existing_sources = m.setdefault("sources", [])
                existing_keys = {
                    (s.get("canonical", s.get("id")), s.get("tag", ""))
                    for s in existing_sources if isinstance(s, dict)
                }
                added_for_match = 0
                for s in matched_epg.get("sources", []):
                    k = (s.get("canonical", s.get("id")), s.get("tag", ""))
                    if k not in existing_keys:
                        existing_keys.add(k)
                        existing_sources.insert(0, s)
                        added_for_match += 1

                if added_for_match > 0:
                    enriched_count += 1

        if enriched_count > 0:
            logger.info("Italian EPG: agganciati canali Sky/Tivùsat TvVoo a %d match reali.", enriched_count)
        return enriched_count


italian_epg_service = ItalianEPGService()
