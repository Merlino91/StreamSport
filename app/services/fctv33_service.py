import asyncio
import datetime
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.config import (
    ENABLE_FCTV33,
    FCTV33_API_BASE,
    FCTV33_CACHE_TTL,
)

logger = logging.getLogger("streamsport.fctv33")

# Mapping from FCTV33 sportType integer to canonical StreamSport Silos
FCTV33_SPORT_TYPE_MAP: Dict[int, Dict[str, str]] = {
    1: {"silo": "football", "name": "Calcio"},
    2: {"silo": "basketball", "name": "Basket"},
    3: {"silo": "tennis", "name": "Tennis"},
    4: {"silo": "baseball", "name": "Baseball"},
    6: {"silo": "cricket", "name": "Cricket"},
    7: {"silo": "motor-sports", "name": "Motori"},
    8: {"silo": "rugby", "name": "Rugby"},
    9: {"silo": "american-football", "name": "Football Americano"},
    10: {"silo": "aussie-rules", "name": "Aussie Rules"},
    11: {"silo": "hockey", "name": "Hockey"},
    12: {"silo": "badminton", "name": "Badminton"},
    13: {"silo": "volley", "name": "Volley"},
    14: {"silo": "fight", "name": "Combattimento"},
    15: {"silo": "cycling", "name": "Ciclismo"},
    16: {"silo": "handball", "name": "Pallamano"},
    90: {"silo": "other", "name": "Altri Sport"},
}

DEFAULT_FETCH_SPORTS = [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 90]

# Protobuf Varint and Envelope Decoders
def _read_varint(buffer: bytes, offset: int = 0) -> Tuple[int, int]:
    value, shift, index = 0, 0, offset
    buflen = len(buffer)
    while index < buflen:
        byte = buffer[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            break
        shift += 7
    return value, index


def _read_fields(buffer: bytes) -> Dict[int, List[Any]]:
    fields: Dict[int, List[Any]] = {}
    offset = 0
    buflen = len(buffer)
    while offset < buflen:
        tag, offset = _read_varint(buffer, offset)
        field, wire = tag >> 3, tag & 0x7
        if wire == 0:
            val, offset = _read_varint(buffer, offset)
            fields.setdefault(field, []).append(val)
        elif wire == 2:
            length, offset = _read_varint(buffer, offset)
            chunk = buffer[offset:offset + length]
            offset += length
            fields.setdefault(field, []).append(chunk)
        else:
            break
    return fields


class FCTV33Service:
    """
    Direct REST + Protobuf integration for FCTV33 (RBTV / TCPWE network).
    Fetches real-time multi-sport catalogs and stream IDs without a headless browser.
    """

    def __init__(self):
        self._cache: List[Dict[str, Any]] = []
        self._cache_time: float = 0.0
        self._cache_ttl: int = FCTV33_CACHE_TTL
        self._signatures: Dict[int, str] = {}
        self._sig_expiry: float = 0.0
        self._lock = asyncio.Lock()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://www.fctv33hd.host/",
            "Origin": "https://www.fctv33hd.host",
            "Accept": "*/*",
        }

    async def _get_signature(self, client: httpx.AsyncClient, code: int = 100, sport_type: int = 1) -> Optional[str]:
        """Obtains dynamic sfver signature for code 100 (/api/match/live) via /api/common/bs."""
        now = time.time()
        if self._signatures.get(code) and now < self._sig_expiry:
            return self._signatures[code]

        try:
            bs_url = f"{FCTV33_API_BASE}/api/common/bs?code=100&code=101&sportType={sport_type}&stream=true"
            resp = await client.get(bs_url, headers=self.headers, timeout=8.0)
            if resp.status_code != 200:
                logger.warning("FCTV33 handshake returned HTTP %s", resp.status_code)
                return None

            data = resp.content
            fields = _read_fields(data)
            for chunk in fields.get(10, []):
                for sub in _read_fields(chunk).get(1, []):
                    sf = _read_fields(sub)
                    c_id = sf.get(1, [None])[0]
                    c_sig = sf.get(2, [None])[0]
                    if c_id and c_sig:
                        sig_str = c_sig.decode("utf-8", errors="replace") if isinstance(c_sig, bytes) else str(c_sig)
                        self._signatures[c_id] = sig_str

            self._sig_expiry = now + 600  # 10 minutes cache
            return self._signatures.get(code)
        except Exception as e:
            logger.warning("Failed refreshing FCTV33 bs signature: %s", e)
            return None

    def _parse_match_item(self, match_bytes: bytes, sport_type: int) -> Optional[Dict[str, Any]]:
        """Parses a single match message chunk from Protobuf into StreamSport dictionary."""
        try:
            mf = _read_fields(match_bytes)
            mid = mf.get(1, [None])[0]
            if not mid:
                return None

            mid_str = str(mid)
            ts = mf.get(3, [None])[0]  # millisecond epoch timestamp
            if not ts:
                return None

            # Title extraction from Field 30 (subfield 2) or Field 150 (slug)
            title = ""
            if 30 in mf:
                t_sub = _read_fields(mf[30][0])
                t_val = t_sub.get(2, [b""])[0]
                if isinstance(t_val, bytes):
                    title = t_val.decode("utf-8", errors="replace").strip()
                elif isinstance(t_val, str):
                    title = t_val.strip()

            slug = ""
            if 150 in mf:
                s_sub = _read_fields(mf[150][0])
                s_val = s_sub.get(20, [b""])[0]
                if isinstance(s_val, bytes):
                    slug = s_val.decode("utf-8", errors="replace").strip()

            if not title and slug:
                # convert slug 'juventus-vs-lazio' to 'Juventus vs Lazio'
                title = slug.replace("-", " ").title()

            if not title:
                return None

            # Team names and logos extraction from Field 30
            home_name = ""
            away_name = ""
            home_logo = None
            away_logo = None

            if 30 in mf and len(mf[30]) >= 3:
                # Team 1 (Home)
                t1_fields = _read_fields(mf[30][1])
                if 10 in t1_fields:
                    sub10_1 = _read_fields(t1_fields[10][0])
                    raw_name1 = sub10_1.get(3, [b""])[0]
                    if isinstance(raw_name1, bytes):
                        s1 = raw_name1.decode("utf-8", errors="replace")
                        raw_h = s1.split("\x12")[-1] if "\x12" in s1 else s1
                        home_name = re.sub(r"^[^a-zA-Z0-9]+", "", raw_h).strip()
                    logo1_raw = sub10_1.get(4, [b""])[0]
                    if isinstance(logo1_raw, bytes) and logo1_raw.startswith(b"http"):
                        home_logo = logo1_raw.decode("utf-8", errors="replace")

                # Team 2 (Away)
                t2_fields = _read_fields(mf[30][2])
                if 10 in t2_fields:
                    sub10_2 = _read_fields(t2_fields[10][0])
                    raw_name2 = sub10_2.get(3, [b""])[0]
                    if isinstance(raw_name2, bytes):
                        s2 = raw_name2.decode("utf-8", errors="replace")
                        raw_a = s2.split("\x12")[-1] if "\x12" in s2 else s2
                        away_name = re.sub(r"^[^a-zA-Z0-9]+", "", raw_a).strip()
                    logo2_raw = sub10_2.get(4, [b""])[0]
                    if isinstance(logo2_raw, bytes) and logo2_raw.startswith(b"http"):
                        away_logo = logo2_raw.decode("utf-8", errors="replace")

            # Fallback: if home/away not parsed from subfields, extract by splitting title
            if (not home_name or not away_name) and title:
                for sep in (" vs ", " - ", " v "):
                    if sep in title:
                        parts = title.split(sep, 1)
                        if not home_name:
                            home_name = parts[0].strip()
                        if not away_name:
                            away_name = parts[1].strip()
                        break

            teams_dict = None
            if home_name and away_name:
                teams_dict = {
                    "home": {"name": home_name},
                    "away": {"name": away_name},
                }

            # League & Country extraction from Field 10
            league = ""
            country = ""
            if 10 in mf:
                l_sub = _read_fields(mf[10][0])
                l_name = l_sub.get(3, [b""])[0]
                if isinstance(l_name, bytes):
                    s = l_name.decode("utf-8", errors="replace")
                    raw_l = s.split("\x12")[-1].strip() if "\x12" in s else s.strip()
                    league = re.sub(r"^[^a-zA-Z0-9]+", "", raw_l).strip()

                if 80 in l_sub:
                    c_raw = l_sub[80][0]
                    if isinstance(c_raw, bytes):
                        cs = c_raw.decode("utf-8", errors="replace")
                        raw_c = cs.split("\x12")[-1].split('"')[0].strip() if "\x12" in cs else ""
                        country = re.sub(r"^[^a-zA-Z0-9]+", "", raw_c).strip()

            # Canonical silo resolution based on sportType
            silo_info = FCTV33_SPORT_TYPE_MAP.get(sport_type, {"silo": "other", "name": "Altri Sport"})
            canonical_silo = silo_info["silo"]

            match_dict = {
                "id": f"fctv33:{mid_str}",
                "title": title,
                "category": canonical_silo,
                "_silo": canonical_silo,
                "date": int(ts),
                "poster": None,
                "competition": league,
                "_competition": league,
                "country": country,
                "_country": country,
                "teams": teams_dict,
                "sources": [
                    {
                        "source": "fctv33",
                        "id": mid_str,
                        "match_id": mid_str,
                        "sport_type": sport_type,
                        "name": "FCTV33 Live",
                    }
                ],
                "_silo": canonical_silo,
                "_fctv33_id": mid_str,
                "_sport_type": sport_type,
                "_provider": "fctv33",
            }

            # Filter out foreign low-tier / irrelevant junk events before returning
            if self.is_junk_event(match_dict):
                return None

            return match_dict
        except Exception as e:
            logger.debug("Error parsing FCTV33 match item: %s", e)
            return None

    @staticmethod
    def is_junk_event(item: Dict[str, Any]) -> bool:
        """
        Evaluates whether an FCTV33 event is low-tier / irrelevant junk for the Italian audience.
        Uses structured 2-level regex inspection primarily on `competition` (with fallback on `title`).
        - Level 1: Motors, tennis, combat sports are completely immune from tournament/geography filtering.
        - Level 2: Strict regex filtering across football, basketball, american-football, baseball, hockey, and volley.
        """
        silo = (item.get("_silo") or item.get("category") or "").lower()
        comp = (item.get("competition") or item.get("_competition") or "").lower().strip()
        country = (item.get("country") or item.get("_country") or "").lower().strip()
        title = (item.get("title") or "").lower().strip()
        comp_or_title = comp or title

        # --- SPORT-SPECIFIC STRUCTURAL FILTERING (No hardcoded team lists) ---

        # 1. TENNIS
        # Keep: Grand Slam, ATP, WTA, ATP Challenger Tour, Davis Cup, Billie Jean King Cup.
        # Discard: Informational/schedule-only summaries, non-matches, table tennis/ping pong leaks.
        if silo == "tennis":
            if re.search(r"(risultati\s*in\s*tempo\s*reale|tennis:\s*atp|atp\s*&\s*wta|schedule\s*only|placeholder)", comp_or_title):
                return True
            if re.search(r"\b(table\s*tennis|ping\s*pong|tt\s*cup|setka\s*cup)\b", comp_or_title):
                return True
            return False

        # 2. MOTOR SPORTS
        # Keep: Formula 1, MotoGP, Superbike, WRC, Rally, NASCAR, IndyCar, WEC, DTM, Supercars.
        # Discard: Novelty / demolition derby / grassroots tractor pulling.
        if silo == "motor-sports":
            if re.search(r"\b(tractor\s*pull|lawn\s*mower|demolition\s*derby|figure\s*8)\b", comp_or_title):
                return True
            return False

        # 3. FIGHT (Sport da Combattimento)
        # Keep: UFC, Bellator, PFL, ONE Championship, Boxe (titoli e promoter d'élite), KSW, Cage Warriors, WWE, AEW.
        # Discard: Local daily episodic Thai circuits / serial televised muay thai cards.
        if silo == "fight":
            if re.search(r"\b(muay\s*mun\s*sanan\s*mueang|the\s*global\s*fight|new\s*power\s*muay\s*thai|rajadamnern\s*knockout)\b", comp_or_title):
                return True
            if re.search(r"\bep\.\s*\d+\b", comp_or_title) and not re.search(r"\b(ufc|wwe|aew|one\s*championship)\b", comp_or_title):
                return True
            return False

        # 4. AMERICAN FOOTBALL
        # Keep: NFL, CFL, UFL, and strictly NCAA Division I FBS.
        # Discard: Lower college divisions (FCS, D-II, D-III, NAIA, JuCo) and high school.
        if silo == "american-football":
            if re.search(r"\b(fcs|ncaa\s*fcs|division\s*(?:ii|iii|1-aa|i-aa)|d-?[23]|naia|juco|high\s*school)\b", comp_or_title):
                return True
            return False

        # 5. BASEBALL
        # Keep: MLB, NPB, KBO, Italian/European leagues, World Baseball Classic, Caribbean Winter Leagues (LIDOM).
        # Discard: Minor leagues (MiLB, Triple-A, Double-A, Single-A, Rookie), college baseball, exotic development circuits.
        if silo == "baseball":
            if re.search(r"\b(milb|triple-a|double-a|single-a|class\s*a|rookie|minor\s*league|phoenix\s*league|college\s*baseball|ncaa)\b", comp_or_title):
                return True
            return False

        # 6. HOCKEY
        # Keep: NHL, KHL, SHL, Liiga, DEL, Swiss National League, Italian/Alpine leagues, IIHF World Championship.
        # Discard: American minor leagues (AHL, ECHL), major junior leagues (OHL, WHL, QMJHL, USHL), college ice hockey.
        if silo == "hockey":
            if re.search(r"\b(ahl|echl|ohl|whl|qmjhl|ushl|junior|college\s*hockey|ncaa)\b", comp_or_title):
                return True
            return False

        # 7. VOLLEYBALL
        # Keep: SuperLega, Serie A1/A2/A3, Coppa Italia, CEV Champions/Cup, VNL, World/Euro Championships, top European leagues (PlusLiga, Ligue A).
        # Discard: Asian/minor exotic domestic cups (KOVO Cup, PVL Philippines, Thai pro challenge), lower college divisions.
        if silo == "volley":
            full_text = f"{comp} {title} {country}"
            is_it_volley = bool(re.search(r"\b(italia|italy|superlega|serie\s*a[123]?|coppa\s*italia)\b", full_text))
            if is_it_volley:
                return False

            # College volleyball, regional/district tournaments
            if re.search(r"\b(college\s*volleyball|ncaa|district|universiade|amateur)\b", comp_or_title):
                return True

            # Asian / exotic minor club tournaments
            ASIAN_EXOTIC_VOLLEY_COUNTRIES = {"south korea", "korea", "philippines", "thailand", "vietnam", "indonesia", "malaysia", "taiwan"}
            is_asian_exotic_country = country in ASIAN_EXOTIC_VOLLEY_COUNTRIES
            is_asian_exotic_comp = bool(re.search(r"\b(kovo\s*cup|kovo|premier\s*volleyball|pvl|pro\s*challenge|v-league\s*korea|thailand\s*league|spikers\s*turf)\b", comp_or_title))
            if is_asian_exotic_country or is_asian_exotic_comp:
                # Protect official FIVB / CEV / Olympic tournaments between national teams
                is_top_world_fivb = bool(re.search(r"\b(vnl|nations\s*league|world\s*championship|olympic|cev|champions\s*league)\b", comp_or_title))
                if not is_top_world_fivb:
                    return True

            return False

        # 5. BASKETBALL
        # Keep: NBA, WNBA, EuroLeague, EuroCup, Basketball Champions League (BCL), FIBA Europe Cup,
        #       LBA Serie A, Serie A2, Coppa Italia, Italian clubs, NCAA Division I.
        # Discard:
        #   - Foreign youth (U14-U21, Sub-17/20, Junior, High School) unless Italian.
        #   - Lower college divisions (D-II, D-III, NAIA, JuCo) and minor amateur development circuits.
        #   - Foreign women's basketball unless WNBA, Italian, or official FIBA World tournaments.
        #   - Asian, African and exotic domestic club competitions (CBA, KBL, PBA, BAL, etc.) unless official senior FIBA World Cup.
        #   - Minor foreign 2nd divisions.
        if silo == "basketball":
            full_text = f"{comp} {title} {country}"

            # A. Youth non-italiane
            if re.search(r"\b(u-?1[0-9]|u-?2[01]|under\s*1[0-9]|under\s*2[01]|sub-?1[0-9]|sub-?20|junior|high\s*school)\b", comp_or_title):
                is_it_youth = bool(re.search(r"\b(italia|italy|lba|serie\s*a)\b", full_text))
                if not is_it_youth:
                    return True

            # B. Lower college divisions & circuiti amatoriali
            if re.search(r"\b(division\s*(?:ii|iii|1-aa|i-aa)|d-?[23]|naia|juco|mpbl|zone\s*5|amateur|development\s*league)\b", comp_or_title):
                return True

            # C. Basket Femminile estero (WNBA e Italia sempre ammessi)
            is_women = bool(re.search(r"\b(women(?:['’]s)?|femminile|femenil|damen|ladies|wnbl|wbla)\b", comp_or_title)) or "women" in country
            if is_women:
                is_wnba = bool(re.search(r"\bwnba\b", comp_or_title)) or "women's national basketball association" in comp
                is_it_women = bool(re.search(r"\b(serie\s*a[12]?|coppa\s*italia|lba|italia|italy)\b", full_text))
                is_top_fiba_women = bool(re.search(r"\b(fiba\s*world\s*cup|eurobasket|olympic)\b", comp_or_title)) and "asia" not in comp_or_title
                if not (is_wnba or is_it_women or is_top_fiba_women):
                    return True

            # D. Campionati asiatici, africani ed esotici di club (CBA, KBL, PBA, BAL, ecc.)
            ASIAN_EXOTIC_BASKET_COUNTRIES = {
                "china", "south korea", "korea", "philippines", "taiwan", "japan",
                "vietnam", "thailand", "indonesia", "malaysia", "singapore", "india",
                "mongolia", "asian", "africa", "rwanda", "uganda", "kenya", "senegal", "angola"
            }
            is_asian_exotic_country = country in ASIAN_EXOTIC_BASKET_COUNTRIES
            is_asian_exotic_comp = bool(re.search(
                r"\b(cba|chinese\s*basketball|kbl|korean\s*basketball|pba|philippine\s*basketball|governors\s*cup|commissioner\s*s\s*cup|t1\s*league|tpbl|plg|b\.league|road\s*to\s*b\.?a\.?l\.?|champions\s*clubs|basketball\s*africa\s*league|bal)\b",
                comp_or_title
            ))
            if is_asian_exotic_country or is_asian_exotic_comp:
                # Proteggi tornei mondiali di Nazionali maggiori FIBA
                is_top_world_fiba = bool(re.search(r"\b(fiba\s*(?:world\s*cup|qualifier)|olympic)\b", comp_or_title)) and not bool(re.search(r"\b(club|asia|bal|league)\b", comp_or_title))
                if not is_top_world_fiba:
                    return True

            # E. Seconde divisioni estere minori
            if re.search(r"\b(aba\s*league\s*2|israeli\s*national\s*league)\b", comp_or_title):
                return True

            return False

        # 6. FOOTBALL (Calcio)
        if silo == "football":
            # A. College Soccer / NCAA
            if re.search(r"\b(college\s*soccer|ncaa)\b", comp_or_title):
                return True

            # B. Seconde/terze divisioni minori extra-europee (J2, J3, K League 2, Torneo Betplay Colombia, ecc.)
            if re.search(r"\b(j[23]\s*league|k\s*league\s*[234]|torneo\s*betplay|colombia(?:n)?\s*torneo\s*betplay|liga\s*de\s*expansi[oó]n|usl\s*league\s*(?:one|two)|mls\s*next\s*pro|s[eé]rie\s*[cd]\s*brazil|primera\s*b\s*(?:nacional|chile|colombia|metropolitana)?)\b", comp_or_title):
                return True

            # C. Youth / Giovanili non italiane (scarta tutte le U17, U19, U20 estere salvo U21 europei o squadre italiane)
            is_youth = bool(re.search(r"\b(u-?\d{1,2}|under\s*\d{1,2}|sub-?\d{1,2}|soeratin|juvenil|cadete|allievi|giovanissimi)\b", comp_or_title))
            if is_youth:
                is_it_youth = bool(re.search(r"\b(italia|italy|primavera|juventus|milan|inter|roma|atalanta|fiorentina|lazio|napoli|torino)\b", f"{comp} {title}"))
                is_u21_major = bool(re.search(r"\b(u-?21|under\s*21)\b", comp_or_title)) and bool(re.search(r"\b(euro|uefa|qualif)\b", comp_or_title)) and "women" not in comp_or_title
                if not is_it_youth and not is_u21_major:
                    return True

            # D. Riserve e leghe di sviluppo amatoriali
            if re.search(r"\b(reserve|reserves|amateur|development\s*league)\b", comp_or_title):
                return True

            # E. Tornei Nazionali maggiori ufficiali (AFCON, Qualifiers Mondiali/Europei, Nations League, ecc.) -> Ammessi se non giovanili
            is_national_tournament = bool(re.search(r"\b(afcon|world cup|qualifier|nations league|copa america|asian cup|gold cup|futsal afcon)\b", comp_or_title))
            if is_national_tournament:
                return False

            # F. Femminile non italiano e non top internazionale
            is_women = bool(re.search(r"\b(women(?:['']s)?|femminile|femenil|dames|frauen|ladies)\b", comp_or_title))
            if is_women:
                is_top_women = bool(re.search(r"\b(serie a|coppa italia|italia|italy|champions|uwcl|world cup|nations league|euro)\b", comp_or_title))
                is_it_team = bool(re.search(r"\b(juventus|roma|inter|milan|fiorentina|sassuolo|como|lazio|sampdoria)\b", title))
                if not is_top_women and not is_it_team:
                    return True

            # G. Campionati nazionali minori / esotici di club non europei
            EXOTIC_CLUB_COUNTRIES = {
                "myanmar", "vietnam", "cambodia", "laos", "indonesia", "thailand",
                "mauritania", "antigua", "antigua and barbuda", "suriname", "st. kitts", "st. kitts and nevis", "saint kitts",
                "maldives", "nepal", "bangladesh", "bhutan", "mongolia", "fiji", "tahiti",
                "uganda", "rwanda", "burundi", "somalia", "djibouti", "eritrea", "sudan", "south sudan",
                "gambia", "liberia", "sierra leone", "guinea-bissau", "tanzania", "kenya", "zambia", "zimbabwe"
            }
            if country in EXOTIC_CLUB_COUNTRIES:
                return True

            for ex in EXOTIC_CLUB_COUNTRIES:
                if re.search(rf"\b{re.escape(ex)}\b", comp_or_title):
                    return True

        return False

    async def get_matches(self, force: bool = False, sport_types: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        """
        Fetches multi-sport events currently live or upcoming today from FCTV33 backend.
        Uses in-RAM caching to prevent redundant upstream requests.
        """
        if not ENABLE_FCTV33:
            return []

        now = time.time()
        if not force and self._cache and (now - self._cache_time < self._cache_ttl):
            return self._cache

        async with self._lock:
            if not force and self._cache and (now - self._cache_time < self._cache_ttl):
                return self._cache

            target_sports = sport_types or DEFAULT_FETCH_SPORTS
            collected: List[Dict[str, Any]] = []

            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                # 1. Obtain signature
                sig = await self._get_signature(client, code=100, sport_type=1)
                if not sig:
                    logger.warning("FCTV33: unable to obtain live signature, skipping")
                    return self._cache

                # 2. Concurrently fetch target sports
                async def fetch_sport(st: int):
                    try:
                        url = f"{FCTV33_API_BASE}/sfver{sig}/api/match/live?sportType={st}&stream=true"
                        resp = await client.get(url, headers=self.headers, timeout=8.0)
                        if resp.status_code != 200:
                            return []

                        env = _read_fields(resp.content)
                        if not env.get(10):
                            return []

                        payload = _read_fields(env[10][0])
                        match_chunks = payload.get(1, [])
                        sport_matches = []
                        for mc in match_chunks:
                            item = self._parse_match_item(mc, st)
                            if item:
                                sport_matches.append(item)
                        return sport_matches
                    except Exception as err:
                        logger.debug("FCTV33: error fetching sportType %s: %s", st, err)
                        return []

                tasks = [fetch_sport(st) for st in target_sports]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for r in results:
                    if isinstance(r, list):
                        collected.extend(r)

            if collected:
                # Deduplicate by ID
                seen_ids = set()
                unique_matches = []
                for m in collected:
                    mid = m["id"]
                    if mid not in seen_ids:
                        seen_ids.add(mid)
                        unique_matches.append(m)

                self._cache = unique_matches
                self._cache_time = now
                logger.info("✅ FCTV33 sync complete: %d matches across %d sports", len(self._cache), len(target_sports))

            return self._cache

    async def get_streams_for_match(self, match_id: str, sport_type: int = 1) -> List[Dict[str, Any]]:
        """
        Queries /api/match/detail in real-time to retrieve the latest broadcast channels and stream IDs.
        """
        if not ENABLE_FCTV33 or not match_id:
            return []

        mid_clean = match_id.replace("fctv33:", "").strip()
        try:
            url = f"{FCTV33_API_BASE}/api/match/detail?matchId={mid_clean}&sportType={sport_type}&digit=foth&country=IT&continent=EU"
            async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=self.headers)
                if resp.status_code != 200:
                    return []

                env = _read_fields(resp.content)
                if not env.get(10):
                    return []

                payload = _read_fields(env[10][0])
                stream_chunks = payload.get(2, [])
                sources = []
                for sc in stream_chunks:
                    sf = _read_fields(sc)
                    sid = sf.get(1, [None])[0]
                    if not sid:
                        continue

                    sname_raw = sf.get(3, [b""])[0]
                    sname = sname_raw.decode("utf-8", errors="replace").strip() if isinstance(sname_raw, bytes) else str(sname_raw).strip()
                    site_type = sf.get(9, [2001])[0]

                    sources.append({
                        "source": "fctv33",
                        "name": sname or "FCTV33 HD",
                        "match_id": mid_clean,
                        "stream_id": str(sid),
                        "sport_type": sport_type,
                        "site_type": site_type,
                    })

                return sources
        except Exception as e:
            logger.debug("FCTV33: error resolving streams for match %s: %s", match_id, e)
            return []


fctv33_service = FCTV33Service()
