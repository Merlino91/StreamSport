from __future__ import annotations
import asyncio
import datetime
import logging
import re
import time
import urllib.parse
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.config import (
    THESPORTSDB_USER,
    THESPORTSDB_PASS,
    THESPORTSDB_CALENDAR_INTERVAL,
    THESPORTSDB_BROWSE_TV_INTERVAL,
)
from app.services.db_service import db_service

logger = logging.getLogger("streamsport.thesportsdb")

THESPORTSDB_API_BASE = "https://www.thesportsdb.com/api/v1/json/3"

THESPORTSDB_SPORT_TO_SILO = {
    "soccer": "football",
    "football": "football",
    "basketball": "basketball",
    "tennis": "tennis",
    "motorsport": "motor-sports",
    "racing": "motor-sports",
    "american football": "american-football",
    "baseball": "baseball",
    "ice hockey": "hockey",
    "hockey": "hockey",
    "volleyball": "volley",
    "mma": "fight",
    "boxing": "fight",
    "wrestling": "fight",
    "fighting": "fight",
    "rugby": "altri_sport",
    "cricket": "altri_sport",
    "golf": "altri_sport",
    "darts": "altri_sport",
    "handball": "altri_sport",
    "cycling": "altri_sport",
}

CATEGORY_SPORT_MAP = {
    "football": {"soccer", "football"},
    "soccer": {"soccer", "football"},
    "basketball": {"basketball"},
    "baseball": {"baseball"},
    "hockey": {"ice hockey", "hockey"},
    "american-football": {"american football"},
    "motor-sports": {"motorsport", "racing"},
    "tennis": {"tennis"},
    "fight": {"mma", "boxing", "wrestling"},
    "rugby": {"rugby"},
    "cricket": {"cricket"},
    "golf": {"golf"},
    "darts": {"darts"},
    "handball": {"handball"},
}

SILO_TO_TSDB_SPORT = {
    "football": "football",
    "calcio_italiano": "football",
    "calcio_estero": "football",
    "soccer": "football",
    "calcio": "football",
    "basketball": "basketball",
    "basket": "basketball",
    "tennis": "tennis",
    "motor-sports": "motor-sports",
    "motori": "motor-sports",
    "motorsport": "motor-sports",
    "american-football": "american-football",
    "football_americano": "american-football",
    "nfl": "american-football",
    "cfl": "american-football",
    "baseball": "baseball",
    "hockey": "hockey",
    "ice hockey": "hockey",
    "volleyball": "volley",
    "volley": "volley",
    "pallavolo": "volley",
    "fight": "fight",
    "combattimento": "fight",
    "mma": "fight",
    "boxe": "fight",
    "boxing": "fight",
    "ufc": "fight",
    "wrestling": "fight",
    "rugby": "altri_sport",
}


class TheSportsDBService:
    """
    Service for fetching official sports schedules and event posters (strThumb) from TheSportsDB.
    Uses authenticated calendar scraping (browse_calendar.php) every 12 hours for multi-day schedule coverage.
    Keeps legacy single-event search dormant for future fallback evaluation.
    """

    def __init__(self):
        self._min_interval: float = 1.2
        self._blocked_until: float = 0.0
        self._rate_limit_reason: str = ""
        self._enrichment_task: Optional[asyncio.Task] = None
        self._is_enriching: bool = False

        # Authenticated Calendar State
        self._client: Optional[httpx.AsyncClient] = None
        self._is_logged_in: bool = False
        self._calendar_cache: List[Dict[str, Any]] = []
        self._calendar_by_silo: Dict[str, List[Dict[str, Any]]] = {}
        self._last_calendar_fetch: float = 0.0
        self._calendar_lock = asyncio.Lock()

        # Browse TV Schedule State
        self._browse_tv_cache: List[Dict[str, Any]] = []
        self._last_browse_tv_fetch: float = 0.0
        self._browse_tv_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/128.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://www.thesportsdb.com/browse_calendar.php",
                },
                timeout=20.0,
            )
        return self._client

    async def login(self) -> bool:
        """
        Authenticates against TheSportsDB using user credentials from config.
        Captures CSRF token and maintains session cookies.
        """
        if not THESPORTSDB_USER or not THESPORTSDB_PASS:
            logger.warning("TheSportsDB credentials (THESPORTSDB_USER / THESPORTSDB_PASS) not configured.")
            return False

        try:
            client = await self._get_client()
            r1 = await client.get("https://www.thesportsdb.com/user_login.php")
            m = re.search(r'name=[\"\']csrf_token[\"\']\s+value=[\"\']([^\"\']+)[\"\']', r1.text)
            if not m:
                logger.warning("TheSportsDB login: csrf_token not found in login page.")
                return False
            csrf_token = m.group(1)

            payload = {
                "csrf_token": csrf_token,
                "username": THESPORTSDB_USER,
                "password": THESPORTSDB_PASS,
                "rememberme": "Yes",
            }
            r2 = await client.post("https://www.thesportsdb.com/user_login.php", data=payload)
            if r2.status_code == 200:
                self._is_logged_in = True
                logger.info("TheSportsDB: login riuscito con successo per %s.", THESPORTSDB_USER)
                return True
            logger.warning("TheSportsDB: login fallito (status %d).", r2.status_code)
            return False
        except Exception as e:
            logger.warning("TheSportsDB: eccezione durante il login: %s", e)
            return False

    def parse_calendar_html(self, html: str, date_str: str) -> List[Dict[str, Any]]:
        """
        Parses TheSportsDB calendar table (browse_calendar.php) into structured event dicts.
        Extracts Time, Sport, League/Competition, Event, and Poster Thumb.
        """
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL | re.IGNORECASE)
        events: List[Dict[str, Any]] = []

        for row in rows:
            cols = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL | re.IGNORECASE)
            if len(cols) < 4:
                continue

            clean = [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', c)).strip() for c in cols]
            time_str = clean[0]
            sport_raw = clean[1]
            league_raw = clean[2]
            event_raw = clean[3]

            if time_str == "Time" or not re.match(r'^\d{2}:\d{2}$', time_str):
                continue

            # Extract thumb if present
            img_match = re.search(r'src=[\"\']([^\"\']*thumb[^\"\']*)[\"\']', row)
            thumb = img_match.group(1) if img_match else None
            if thumb and "no_thumb" not in thumb:
                thumb = re.sub(r'/(?:small|tiny|preview)$', '', thumb)
                if not thumb.endswith('/medium'):
                    thumb = f"{thumb}/medium"
            else:
                thumb = None

            # Calculate date_ms in UTC (default timezone)
            try:
                dt = datetime.datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M").replace(
                    tzinfo=datetime.timezone.utc
                )
                date_ms = int(dt.timestamp() * 1000)
            except Exception:
                date_ms = 0

            # Map sport to silo
            sport_clean = re.sub(r'[^\w\s-]', '', sport_raw).strip().lower()
            silo = THESPORTSDB_SPORT_TO_SILO.get(sport_clean, "altri_sport")

            # Extract home and away
            home, away = "", ""
            if " vs " in event_raw:
                parts = event_raw.split(" vs ", 1)
                home = parts[0].strip()
                away = parts[1].strip()
            elif " - " in event_raw:
                parts = event_raw.split(" - ", 1)
                home = parts[0].strip()
                away = parts[1].strip()

            events.append({
                "title": event_raw,
                "home": home,
                "away": away,
                "sport": sport_raw,
                "_silo": silo,
                "competition": league_raw,
                "_competition": league_raw,
                "date_str": date_str,
                "time_str": time_str,
                "date_ms": date_ms,
                "thumb": thumb,
            })

        return events

    async def fetch_multi_day_calendar(self, days_ahead: int = 3, days_behind: int = 1) -> List[Dict[str, Any]]:
        """
        Fetches the official TheSportsDB calendar for recent and upcoming days (default: yesterday to +3 days ahead).
        Indexes events by Silo for instant zero-latency match enrichment across full race weekends.
        Throttled to run at most once every THESPORTSDB_CALENDAR_INTERVAL (12 hours).
        """
        async with self._calendar_lock:
            now = time.time()
            if self._calendar_cache and (now - self._last_calendar_fetch < THESPORTSDB_CALENDAR_INTERVAL):
                return self._calendar_cache

            if not self._is_logged_in:
                ok = await self.login()
                if not ok:
                    return self._calendar_cache

            client = await self._get_client()
            today_utc = datetime.datetime.now(datetime.timezone.utc).date()
            all_events: List[Dict[str, Any]] = []

            for day_offset in range(-days_behind, days_ahead + 1):
                d = today_utc + datetime.timedelta(days=day_offset)
                d_str = d.strftime("%Y-%m-%d")
                url = f"https://www.thesportsdb.com/browse_calendar.php?d={d_str}"

                try:
                    logger.info("Scaricamento calendario TheSportsDB per il giorno %s...", d_str)
                    res = await client.get(url)
                    # Check if session expired / redirect to login
                    if "user_login.php" in str(res.url) or "login" in res.text[:500].lower():
                        logger.warning("Sessione TheSportsDB scaduta, rieseguo il login...")
                        self._is_logged_in = False
                        if await self.login():
                            res = await client.get(url)

                    if res.status_code == 200:
                        day_events = self.parse_calendar_html(res.text, d_str)
                        all_events.extend(day_events)
                        logger.info("TheSportsDB: estratti %d eventi ufficiali per %s.", len(day_events), d_str)
                    else:
                        logger.warning("TheSportsDB browse_calendar error %d for %s", res.status_code, d_str)
                except Exception as e:
                    logger.warning("TheSportsDB errore durante fetch calendar %s: %s", d_str, e)

                # Throttle slightly between day requests
                await asyncio.sleep(1.0)

            # Re-index by silo
            from collections import defaultdict
            by_silo = defaultdict(list)
            for ev in all_events:
                by_silo[ev["_silo"]].append(ev)

            self._calendar_cache = all_events
            self._calendar_by_silo = dict(by_silo)
            self._last_calendar_fetch = now
            logger.info("TheSportsDB calendario completato: %d eventi totali indicizzati in memoria.", len(all_events))
            return all_events

    TSDB_ALIASES = {
        r"\blafc\b": "los angeles fc",
        r"\bwolves\b": "wolverhampton",
        r"\bman city\b": "manchester city",
        r"\bman utd\b": "manchester united",
        r"\bman united\b": "manchester united",
        r"\bspurs\b": "tottenham",
        r"\bpsg\b": "paris saint germain",
        r"\bparis sg\b": "paris saint germain",
        r"\binter\b": "internazionale",
        r"\bbayern\b": "bayern munchen",
        r"\bbayern munich\b": "bayern munchen",
        r"\bdortmund\b": "borussia dortmund",
        r"\batletico\b": "atletico madrid",
        r"\bnewcastle\b": "newcastle united",
        r"\bleicester\b": "leicester city",
        r"\bwest ham\b": "west ham united",
        r"\bsir safety perugia\b": "perugia",
        r"\bitas trentino\b": "trentino",
        r"\btrentino volley\b": "trentino",
        r"\bcucine lube civitanova\b": "lube",
        r"\blube civitanova\b": "lube",
        r"\bvalsa group modena\b": "modena",
        r"\bmodena volley\b": "modena",
        r"\bsavino del bene scandicci\b": "scandicci",
        r"\bprosecco doc imoco conegliano\b": "conegliano",
        r"\bimoco conegliano\b": "conegliano",
        r"\bigor gorgonzola novara\b": "novara",
        r"\bmint vero volley monza\b": "monza",
        r"\bvero volley monza\b": "monza",
        r"\bgas sales bluenergy piacenza\b": "piacenza",
        r"\bgas sales piacenza\b": "piacenza",
        r"\brana verona\b": "verona",
        r"\bsonepar padova\b": "padova",
        r"\bgioiella prisma taranto\b": "taranto",
        r"\byuasa battery grottazzolina\b": "grottazzolina",
        r"\bcisterna volley\b": "cisterna",
    }

    GENERIC_CLUB_WORDS = {
        "real", "atletico", "sporting", "racing", "deportivo", "dinamo",
        "inter", "union", "united", "city", "club", "estrella", "olimpia",
        "virtus", "san", "santa", "saint", "st"
    }

    def _clean_team_name(self, name: str) -> str:
        s = (name or "").lower()
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("utf-8")
        s = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", s)
        for pat, rep in self.TSDB_ALIASES.items():
            s = re.sub(pat, rep, s)
        # Strip common sports club qualifiers, prefixes and suffixes (including Italian ones like lidocalcio, citta di)
        s = re.sub(
            r"\b(fc|cf|bc|sc|ac|as|ss|ssd|asd|usd|us|lidocalcio|lido\s*calcio|citta\s*di|polisportiva|unione\s*sportiva|basket|pallacanestro|calcio|volley|pallavolo|the)\b",
            " ",
            s,
        )
        s = re.sub(r"[^a-z0-9]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _team_matches(self, m_t: str, cal_t: str) -> bool:
        if not m_t or not cal_t:
            return False
        # 1. Exact match
        if m_t == cal_t:
            return True
        # 2. Space-insensitive compact equality (e.g. "ostiamare" == "ostiamare" from "ostia mare")
        m_compact = m_t.replace(" ", "")
        cal_compact = cal_t.replace(" ", "")
        if m_compact == cal_compact:
            return True
        # 3. Substring matching (only if significant length >= 5)
        if len(m_t) >= 5 and len(cal_t) >= 5:
            if (m_t in cal_t) or (cal_t in m_t):
                return True
        # 4. Space-insensitive compact substring (if length >= 6)
        if len(m_compact) >= 6 and len(cal_compact) >= 6:
            if (cal_compact in m_compact) or (m_compact in cal_compact):
                return True
        # 5. Significant word set intersection (excluding generic club words)
        m_words = set(w for w in m_t.split() if len(w) >= 4)
        c_words = set(w for w in cal_t.split() if len(w) >= 4)
        distinct_common = (m_words & c_words) - self.GENERIC_CLUB_WORDS
        if distinct_common:
            return True
        # 6. Metric fuzzy similarity (ratio >= 0.75 for names of length >= 4, e.g. turkiye vs turkey)
        from difflib import SequenceMatcher
        if len(m_t) >= 4 and len(cal_t) >= 4:
            if SequenceMatcher(None, m_t, cal_t).ratio() >= 0.75:
                return True
        return False

    NON_COMPETITOR_WORDS = {
        "atp", "wta", "itf", "challenger", "tour", "open", "finals", "final",
        "semi-final", "quarter-final", "round", "day", "session", "singles",
        "doubles", "qualifying", "qualification", "practice", "fp1", "fp2",
        "fp3", "sprint", "gara", "race", "qualif", "warmup", "gp", "grand prix",
    }

    def _extract_teams(self, match: Dict[str, Any]) -> Tuple[str, str]:
        # Motorsports do not have team-vs-team matches; never extract fake home/away
        m_silo = self.get_match_tsdb_silo(match)
        if any(k in m_silo for k in ("motor", "racing", "motori")):
            return "", ""

        teams = match.get("teams")
        home = ""
        away = ""
        if isinstance(teams, dict) and teams.get("home") and teams.get("away"):
            home = self._clean_team_name(teams.get("home", {}).get("name", ""))
            away = self._clean_team_name(teams.get("away", {}).get("name", ""))

        if not home or not away:
            raw_title = match.get("title", "")
            # Strip time prefix, e.g. "24-09 20:45 "
            clean_t = re.sub(r"^[0-9:\s-]+(?:\s*:\s*)?", "", raw_title)
            # Strip category / league prefix, e.g. "Italy - Serie C : " or "ITA D1 : "
            clean_t = re.sub(r"^[A-Za-z0-9\s-]+:\s*", "", clean_t)
            # Remove parenthesized notes
            clean_t = re.sub(r"\([^)]*\)", "", clean_t)
            for sep in (" vs ", " - ", " v "):
                if sep in clean_t:
                    parts = clean_t.split(sep, 1)
                    p0_lower = parts[0].strip().lower()
                    p1_lower = parts[1].strip().lower()

                    # Guard against non-competitor hyphen splits (e.g. 'ATP Tokyo - Finals', 'Formula 1 - Practice 1')
                    if sep == " - ":
                        has_nc_word = any(
                            re.search(rf"\b{re.escape(w)}\b", p0_lower) or re.search(rf"\b{re.escape(w)}\b", p1_lower)
                            for w in self.NON_COMPETITOR_WORDS
                        )
                        if has_nc_word:
                            continue

                    home = self._clean_team_name(parts[0])
                    away = self._clean_team_name(parts[1])
                    break
        return home, away

    @staticmethod
    def get_match_tsdb_silo(m: Dict[str, Any]) -> str:
        silo = (m.get("_silo") or "").lower().strip()
        if silo in SILO_TO_TSDB_SPORT:
            return SILO_TO_TSDB_SPORT[silo]
        cat = (m.get("category") or "").lower().strip()
        if cat in SILO_TO_TSDB_SPORT:
            return SILO_TO_TSDB_SPORT[cat]
        for k, v in SILO_TO_TSDB_SPORT.items():
            if k in silo or k in cat:
                return v
        return "altri_sport"

    @staticmethod
    def is_tsdb_sport_compatible(event_sport_raw: str, expected_silo: Optional[str]) -> bool:
        """
        Verifies if the sport scraped from TheSportsDB (e.g. 'Soccer', 'American-Football')
        is compatible with the match's canonical sport silo (e.g. 'football', 'american-football').
        """
        if not event_sport_raw or not expected_silo or expected_silo == "altri_sport":
            return True
        s = re.sub(r"[^\w]+", "", (event_sport_raw or "").lower())
        e = re.sub(r"[^\w]+", "", (expected_silo or "").lower())

        if e in ("football", "soccer", "calcio"):
            return s in ("soccer", "football") and "american" not in s

        if "american" in e or e in ("nfl", "cfl"):
            return "american" in s or s in ("nfl", "cfl")

        if "basket" in e:
            return "basket" in s

        if "tennis" in e:
            return "tennis" in s

        if "baseball" in e:
            return "baseball" in s

        if "hockey" in e:
            return "hockey" in s

        if any(k in e for k in ("motor", "racing", "motori")):
            return any(k in s for k in ("motor", "racing"))

        if "volley" in e or "pallavolo" in e:
            return "volley" in s

        if any(k in e for k in ("fight", "mma", "ufc", "box")):
            return any(k in s for k in ("fight", "mma", "ufc", "box", "wrestling"))

        return True

    def reconcile_matches(self, matches: List[Dict[str, Any]]) -> int:
        """
        LEVEL 1 RECONCILIATION:
        Matches upstream matches (Streamed, DaddyLive, Virgilio) against the official TheSportsDB Calendar.
        Prioritizes:
        1. Official 16:9 event poster (cal['thumb'])
        2. Official competition name (cal['competition'])
        3. Canonical home/away team names
        4. Tags match with _tsdb_matched = True
        """
        if not self._calendar_cache or not matches:
            return 0

        reconciled_count = 0
        for m in matches:
            tsdb_silo = self.get_match_tsdb_silo(m)
            candidates = self._calendar_by_silo.get(tsdb_silo, [])
            if not candidates:
                if tsdb_silo != "altri_sport":
                    continue
                candidates = self._calendar_cache

            m_home, m_away = self._extract_teams(m)
            if not m_home or not m_away:
                continue

            m_date = m.get("date", 0)

            best_match = None
            for cal in candidates:
                cal_home = self._clean_team_name(cal.get("home", ""))
                cal_away = self._clean_team_name(cal.get("away", ""))
                if not cal_home or not cal_away:
                    continue

                h_direct = self._team_matches(m_home, cal_home)
                a_direct = self._team_matches(m_away, cal_away)
                h_inverted = self._team_matches(m_home, cal_away)
                a_inverted = self._team_matches(m_away, cal_home)

                if (h_direct and a_direct) or (h_inverted and a_inverted):
                    # Check date proximity (+/- 14 hours for timezone flexibility)
                    cal_date = cal.get("date_ms", 0)
                    if m_date and cal_date:
                        diff_hours = abs(m_date - cal_date) / 3600000.0
                        if diff_hours <= 14:
                            best_match = cal
                            break
                    else:
                        best_match = cal
                        break

            if best_match:
                # 1. Poster assignment
                cal_thumb = best_match.get("thumb")
                if cal_thumb and (not m.get("poster") or "nostream" in m.get("poster", "") or not str(m.get("poster")).startswith("http")):
                    m["poster"] = cal_thumb
                    db_service.update_match_poster(m["id"], cal_thumb)
                    q_key = f"{best_match['home']} vs {best_match['away']}"
                    db_service.save_poster_to_cache(q_key, cal_thumb, "found")

                # 2. Official competition assignment
                cal_comp = best_match.get("competition")
                if cal_comp:
                    m["competition"] = cal_comp
                    m["_competition"] = cal_comp

                # 3. Canonical team names if missing
                if not m.get("teams") or not isinstance(m.get("teams"), dict):
                    m["teams"] = {
                        "home": {"name": best_match["home"]},
                        "away": {"name": best_match["away"]},
                    }

                m["_tsdb_matched"] = True
                reconciled_count += 1

        # Level 1 Motorsport Reconciliation & Weekend GP Cascade
        reconciled_motors = self.reconcile_motorsport_matches(matches)
        total_reconciled = reconciled_count + reconciled_motors

        if total_reconciled > 0:
            logger.info("TheSportsDB Level 1: %d match allacciati con successo al calendario ufficiale (di cui %d motori).", total_reconciled, reconciled_motors)
        return total_reconciled

    @staticmethod
    def get_motorsport_series_key(text: str) -> str:
        """Extracts canonical motorsport series identifier."""
        s = (text or "").lower()
        if "formula 1" in s or "formula one" in s or re.search(r"\bf1\b", s):
            return "f1"
        if "formula 2" in s or re.search(r"\bf2\b", s):
            return "f2"
        if "formula 3" in s or re.search(r"\bf3\b", s):
            return "f3"
        if "formula e" in s or re.search(r"\bfe\b", s):
            return "fe"
        if "moto2" in s or "moto 2" in s:
            return "moto2"
        if "moto3" in s or "moto 3" in s:
            return "moto3"
        if "motogp" in s or "moto gp" in s:
            return "motogp"
        if "superbike" in s or "wsbk" in s or "bsb" in s:
            return "superbike"
        if "nascar" in s:
            return "nascar"
        if "indycar" in s or "indy car" in s or re.search(r"\bindy\b", s):
            return "indycar"
        if "wec" in s or "le mans" in s:
            return "wec"
        if "wrc" in s or "rally" in s:
            return "wrc"
        if "imsa" in s:
            return "imsa"
        if "dtm" in s:
            return "dtm"
        if "gt world" in s or "gt3" in s:
            return "gt"
        return ""

    @staticmethod
    def is_motorsport_match(m: Dict[str, Any]) -> bool:
        """Determines if a match belongs to motorsport category."""
        silo = (m.get("_silo") or "").lower()
        cat = (m.get("category") or "").lower()
        catalog = (m.get("_catalog") or "").lower()
        genre = (m.get("_genre") or "").lower()
        title = (m.get("title") or "").lower()

        if any(k in silo for k in ("motor", "racing", "motori")):
            return True
        if any(k in cat for k in ("motor", "racing", "motori")):
            return True
        if any(k in catalog for k in ("motor", "racing", "motori")):
            return True
        if any(k in genre for k in ("formula", "motogp", "superbike", "nascar", "indycar")):
            return True
        if any(k in title for k in ("formula 1", "formula 2", "f1", "motogp", "moto2", "moto3", "nascar", "indycar", "superbike", "grand prix", "rally")):
            return True
        return False

    def reconcile_motorsport_matches(self, matches: List[Dict[str, Any]]) -> int:
        """
        LEVEL 1 MOTORSPORT RECONCILIATION & WEEKEND GP CASCADE FALLBACK:
        Matches motorsport events strictly in-RAM against _calendar_by_silo['motor-sports'].
        Zero network latency, zero false positives from past years.

        Algorithm:
        1. Identifies motorsport events and extracts the series key (F1, MotoGP, etc.).
        2. Filters the in-RAM calendar for events of the same series within a +/- 84h window (race weekend).
        3. Priority 1 (Session Thumb): Checks if the candidate session has an official thumb.
        4. Priority 2 (Weekend GP Cascade Fallback): Inherits the official 16:9 poster from the Main
           Grand Prix / Race event of the weekend.
        """
        if not self._calendar_cache or not matches:
            return 0

        ms_candidates = self._calendar_by_silo.get("motor-sports", [])
        if not ms_candidates:
            ms_candidates = [
                ev for ev in self._calendar_cache
                if any(k in (ev.get("_silo") or "").lower() for k in ("motor", "racing"))
                or any(k in (ev.get("sport") or "").lower() for k in ("motor", "racing"))
            ]
        if not ms_candidates:
            return 0

        reconciled_count = 0
        for m in matches:
            if not self.is_motorsport_match(m):
                continue

            # Skip if match already has an enriched/valid poster (not placeholder)
            poster = m.get("poster")
            if poster and "nostream" not in str(poster) and str(poster).startswith("http"):
                continue

            # Identify series key from title, competition, official_event or genre
            m_text = f"{m.get('title', '')} {m.get('competition', '')} {m.get('_official_event', '')} {m.get('_genre', '')}"
            m_series = self.get_motorsport_series_key(m_text)
            if not m_series:
                continue

            m_date = m.get("date") or 0

            # Filter candidates of the same series within race weekend window (+/- 84 hours)
            weekend_cals = []
            for cal in ms_candidates:
                cal_text = f"{cal.get('competition', '')} {cal.get('title', '')}"
                cal_series = self.get_motorsport_series_key(cal_text)
                if cal_series != m_series:
                    continue

                if m_date and cal.get("date_ms"):
                    diff_hours = abs(m_date - cal["date_ms"]) / 3600000.0
                    if diff_hours > 84.0:
                        continue
                weekend_cals.append(cal)

            if not weekend_cals:
                continue

            chosen_thumb: Optional[str] = None
            best_cal: Optional[Dict[str, Any]] = None
            m_title_lower = (m.get("title") or "").lower()

            # Priority 1: Exact / Close session match with valid thumb
            for cal in weekend_cals:
                c_thumb = cal.get("thumb")
                if c_thumb:
                    c_title_lower = (cal.get("title") or "").lower()
                    if any(
                        sess in m_title_lower and sess in c_title_lower
                        for sess in ("qualif", "sprint", "practice", "fp1", "fp2", "fp3", "warm up", "warmup")
                    ):
                        chosen_thumb = c_thumb
                        best_cal = cal
                        break

            # Priority 2: Cascade Fallback to Main GP of the weekend
            if not chosen_thumb:
                scored = []
                for cal in weekend_cals:
                    thumb = cal.get("thumb")
                    if not thumb:
                        continue
                    score = 0
                    c_title = (cal.get("title") or "").lower()
                    if "gp" in c_title or "grand prix" in c_title:
                        score += 20
                    if "race" in c_title or "gara" in c_title:
                        score += 10
                    if "sprint" in c_title:
                        score -= 5
                    if any(w in c_title for w in ("practice", "fp", "qualif", "warm")):
                        score -= 10
                    scored.append((score, cal))

                if scored:
                    scored.sort(key=lambda x: x[0], reverse=True)
                    best_cal = scored[0][1]
                    chosen_thumb = best_cal["thumb"]

            if chosen_thumb:
                m["poster"] = chosen_thumb
                m["background"] = chosen_thumb
                m["_tsdb_matched"] = True
                db_service.update_match_poster(m["id"], chosen_thumb)

                if not m.get("competition") and best_cal and best_cal.get("competition"):
                    m["competition"] = best_cal["competition"]
                    m["_competition"] = best_cal["competition"]

                reconciled_count += 1

        if reconciled_count > 0:
            logger.info("TheSportsDB Motori: allacciate %d locandine ufficiali (in-RAM & Fallback GP).", reconciled_count)
        return reconciled_count

    def enrich_matches(self, matches: List[Dict[str, Any]]) -> int:
        """
        Cross-matches upstream matches with the cached official TheSportsDB calendar.
        Delegates to Level 1 reconcile_matches.
        """
        return self.reconcile_matches(matches)

    async def search_event_thumb_html(
        self,
        home_raw: str,
        away_raw: str,
        match_date_ms: int = 0,
        expected_silo: Optional[str] = None,
    ) -> Optional[str]:
        """
        LEVEL 2 SEARCH: Queries TheSportsDB web search (browse?s=...)
        using clean primary team keywords (e.g. 'Ostia Forli').
        Parses matching event thumb and validates against expected date and sport.
        """
        h_clean = self._clean_team_name(home_raw)
        a_clean = self._clean_team_name(away_raw)

        h_words = [w for w in h_clean.split() if w not in self.GENERIC_CLUB_WORDS and len(w) >= 3]
        a_words = [w for w in a_clean.split() if w not in self.GENERIC_CLUB_WORDS and len(w) >= 3]
        if not h_words or not a_words:
            return None

        h_kw = h_words[0]
        a_kw = a_words[0]
        query = f"{h_kw} {a_kw}"
        url = f"https://www.thesportsdb.com/browse?s={urllib.parse.quote(query)}"

        try:
            client = await self._get_client()
            res = await client.get(url)
            if res.status_code == 429:
                logger.warning("TheSportsDB rate limited (429) during HTML search for %s.", query)
                return None
            if res.status_code != 200:
                return None

            pattern = re.compile(
                r"<a\s+href=['\"]/event/(\d+)-([^'\"]+)['\"][^>]*>(.*?)</a>(?:\s*(?:<[^>]+>\s*)*\(([0-9]{4}-[0-9]{2}-[0-9]{2})\))?",
                re.DOTALL | re.IGNORECASE,
            )
            for ev_id, slug, inner, date_str in pattern.findall(res.text):
                slug_norm = unicodedata.normalize("NFKD", urllib.parse.unquote(slug)).encode("ascii", "ignore").decode("utf-8").lower()
                if h_kw not in slug_norm or a_kw not in slug_norm:
                    continue

                # 1. Sport validation if SVG icon is present
                sport_m = re.search(r'/sports/([^.\x27\x22/]+)\.svg', inner, re.IGNORECASE)
                event_sport = sport_m.group(1) if sport_m else ""
                if event_sport and expected_silo:
                    if not self.is_tsdb_sport_compatible(event_sport, expected_silo):
                        continue

                # 2. Date proximity validation: tolerance of +/- 1 calendar day to absorb worldwide timezones
                if match_date_ms > 0 and date_str:
                    try:
                        ev_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                        m_dt = datetime.datetime.fromtimestamp(match_date_ms / 1000.0, tz=datetime.timezone.utc).date()
                        diff_days = abs((m_dt - ev_dt).days)
                        if diff_days > 1:
                            continue
                    except Exception:
                        pass

                # 3. Valid thumbnail extraction
                m_thumb = re.search(r"src=['\"](https?://[^'\" >]+/thumb/[^'\" >]+)['\"]", inner)
                if m_thumb and "no_thumb" not in m_thumb.group(1):
                    return self.format_thumb_url(m_thumb.group(1))
        except Exception as e:
            logger.debug("TheSportsDB HTML search error for %s: %s", query, e)

        return None

    async def fallback_semantic_search_orphans(self, matches: List[Dict[str, Any]]):
        """
        LEVEL 2 FALLBACK:
        For matches that still lack an official poster after Level 1 in-RAM matching,
        performs a polite, one-off web search on TheSportsDB.
        Caches both 'found' and 'not_found' in SQLite so each match is only queried once.
        """
        orphans = [
            m for m in matches
            if (not m.get("poster") or "nostream" in m.get("poster", "") or not str(m.get("poster")).startswith("http"))
        ]
        if not orphans:
            return

        found_count = 0
        for m in orphans:
            m_home, m_away = self._extract_teams(m)
            if not m_home or not m_away:
                continue

            q_key = f"{m_home} vs {m_away}"
            cached = db_service.get_poster_from_cache(q_key)
            if cached:
                status, thumb_url, _ = cached
                if status == "found" and thumb_url:
                    m["poster"] = thumb_url
                    m["background"] = thumb_url
                    db_service.update_match_poster(m["id"], thumb_url)
                    found_count += 1
                continue

            m_date = m.get("date", 0)
            m_silo = self.get_match_tsdb_silo(m)
            thumb = await self.search_event_thumb_html(m_home, m_away, match_date_ms=m_date, expected_silo=m_silo)
            if thumb:
                m["poster"] = thumb
                m["background"] = thumb
                db_service.update_match_poster(m["id"], thumb)
                db_service.save_poster_to_cache(q_key, thumb, "found")
                found_count += 1
                logger.info("TheSportsDB Level 2 Fallback: trovata locandina per '%s vs %s' -> %s", m_home, m_away, thumb)
            else:
                db_service.save_poster_to_cache(q_key, None, "not_found")

            # Polite throttle (1.2s) between external requests
            await asyncio.sleep(1.2)

        if found_count > 0:
            logger.info("TheSportsDB Level 2: arricchiti %d match orfani tramite fallback semantico.", found_count)

    def start_background_enrichment(self, matches: List[Dict[str, Any]]):
        """
        Starts the non-blocking background enrichment task.
        Executes the 12-hour calendar sync and enriches matches in memory and SQLite.
        """
        if self._is_enriching:
            return

        async def _enrich_task():
            self._is_enriching = True
            try:
                # 1. Fetch 3-day calendar every 12 hours
                await self.fetch_multi_day_calendar(days_ahead=2)
                # 2. Match and enrich active matches
                n = self.enrich_matches(matches)
                if n > 0:
                    logger.info("TheSportsDB Calendario: arricchite con successo %d locandine.", n)
                # 3. Fallback semantic search for remaining orphan matches
                await self.fallback_semantic_search_orphans(matches)
            except Exception as e:
                logger.warning("TheSportsDB errore durante arricchimento calendario: %s", e)
            finally:
                self._is_enriching = False

        self._enrichment_task = asyncio.create_task(_enrich_task())

    def start_background_fallback_search(self, matches: List[Dict[str, Any]]):
        """Triggers the non-blocking Level 2 fallback semantic search for orphan matches."""
        if self._is_enriching:
            return

        async def _search_task():
            self._is_enriching = True
            try:
                await self.fallback_semantic_search_orphans(matches)
            except Exception as e:
                logger.debug("TheSportsDB background fallback search error: %s", e)
            finally:
                self._is_enriching = False

        asyncio.create_task(_search_task())

    # =========================================================================
    # DORMANT METHODS: Legacy Single-Event Search (Kept for fallback evaluation)
    # =========================================================================

    def parse_retry_delay(self, text: str, headers: Optional[Dict[str, str]] = None, default_seconds: int = 300) -> int:
        if headers:
            retry_after = headers.get("retry-after") or headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                return int(retry_after)

        if not text:
            return default_seconds

        m_sec = re.search(r"(?:riprova|retry)\s+(?:in|tra)\s*(\d+)\s*(?:s|sec|secondi|seconds)\b", text, re.I)
        if m_sec:
            return int(m_sec.group(1))

        m_min = re.search(r"(?:riprova|retry)\s+(?:in|tra)\s*(\d+)\s*(?:m|min|minuti|minutes)\b", text, re.I)
        if m_min:
            return int(m_min.group(1)) * 60

        m_any_min = re.search(r"\b(\d+)\s*(?:m|min|minuti|minutes)\b", text, re.I)
        if m_any_min:
            return int(m_any_min.group(1)) * 60

        m_any_sec = re.search(r"\b(\d+)\s*(?:s|sec|secondi|seconds)\b", text, re.I)
        if m_any_sec:
            return int(m_any_sec.group(1))

        return default_seconds

    def clean_event_query(
        self, title: str, teams: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, Optional[str], Optional[str]]:
        home = ""
        away = ""
        if teams and isinstance(teams, dict):
            home = (teams.get("home", {}) or {}).get("name", "").strip()
            away = (teams.get("away", {}) or {}).get("name", "").strip()

        if home and away:
            clean_home = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", home).strip()
            clean_away = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", away).strip()
            clean_home = re.sub(r"^[0-9:\s-]+", "", clean_home).strip()
            clean_away = re.sub(r"\s*\([^)]*\)$", "", clean_away).strip()
            return f"{clean_home} vs {clean_away}", clean_home, clean_away

        t = title or ""
        # Strip timestamps at start: "24-09 20:45 "
        t = re.sub(r"^\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{2})?\s*:?\s*", "", t)
        # Strip live markers
        t = re.sub(r"^🔴\s*Inizio\s*:\s*[0-9:]+\s*", "", t, flags=re.I).strip()
        # Strip category/league prefix: "ITA D1 : ", "Italy - Serie A : "
        t = re.sub(r"^[^:]+:\s*", "", t)
        # Strip parenthesized notes: "(NBL)", "(One Day International)"
        t = re.sub(r"\([^)]*\)", "", t)
        # Strip bracket notes: "[HD]"
        t = re.sub(r"\[[^\]]*\]", "", t)
        # Strip flag emojis
        t = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", t)
        t = re.sub(r"\s+", " ", t).strip()

        if " vs " in t.lower():
            parts = re.split(r"\s+vs\s+", t, flags=re.I)
            h = parts[0].strip()
            a = parts[1].strip()
            return f"{h} vs {a}", h, a
        elif " - " in t:
            parts = t.split(" - ", 1)
            h = parts[0].strip()
            a = parts[1].strip()
            return f"{h} vs {a}", h, a

        return t, None, None

    @staticmethod
    def format_thumb_url(thumb: Optional[str]) -> Optional[str]:
        if not thumb:
            return None
        t = thumb.strip()
        t = re.sub(r"/(?:small|tiny|preview)$", "", t)
        if "thesportsdb.com" in t and not t.endswith("/medium"):
            t = f"{t}/medium"
        return t

    def parse_browse_events(self, html: str) -> List[Dict[str, Any]]:
        if "<b>Events</b>" not in html:
            return []

        events_section = html.split("<b>Events</b>")[1].split("</div>")[0]
        pattern = re.compile(
            r"<a\s+href=['\"]/event/(\d+)-([^'\"]+)['\"][^>]*>"
            r"(.*?)"
            r"</a>\s*(?:\(([0-9]{4}-[0-9]{2}-[0-9]{2})\))?",
            re.DOTALL | re.IGNORECASE,
        )

        results = []
        for match in pattern.finditer(events_section):
            ev_id = match.group(1)
            inner_html = match.group(3)
            date_str = match.group(4) or ""

            thumb_match = re.search(r"src=['\"](https?://[^'\" >]+/thumb/[^'\" >]+)['\"]", inner_html)
            thumb = thumb_match.group(1) if thumb_match else ""
            if "no_thumb" in thumb:
                thumb = ""

            sport_match = re.search(r"/sports/([^/'\" >]+)\.svg", inner_html, re.I)
            sport = sport_match.group(1).lower() if sport_match else ""

            clean_name = re.sub(r"<[^>]+>", " ", inner_html).strip()
            clean_name = re.sub(r"\s+", " ", clean_name)

            results.append({
                "idEvent": ev_id,
                "strEvent": clean_name,
                "strSport": sport,
                "dateEvent": date_str,
                "strThumb": thumb,
            })
        return results

    async def search_event_thumb(
        self,
        query: str,
        home: Optional[str] = None,
        away: Optional[str] = None,
        date_ms: Optional[int] = None,
        category: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[int]]:
        """Dormant single-event search implementation."""
        # Query API as primary
        api_query = query.replace(" ", "_")
        url = f"{THESPORTSDB_API_BASE}/searchevents.php?e={urllib.parse.quote(api_query)}"
        try:
            client = await self._get_client()
            res = await client.get(url)
            if res.status_code == 429:
                wait_sec = self.parse_retry_delay(res.text, res.headers, default_seconds=300)
                return None, wait_sec
            if res.status_code == 200:
                data = res.json()
                events = data.get("event") or []
                for ev in events:
                    thumb = ev.get("strThumb")
                    if thumb:
                        return self.format_thumb_url(thumb), None
        except Exception as e:
            logger.debug("search_event_thumb exception: %s", e)
        return None, None

    async def fetch_browse_tv_matches(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Scrapes https://www.thesportsdb.com/browse_tv to discover live TV sports broadcasts for
        Italy, Spain, Germany, France, United Kingdom, and World.
        Maps recognized broadcasters directly to TvVoo FHD streams.
        Returns match objects ready for merging into the central catalog.
        Caches results for THESPORTSDB_BROWSE_TV_INTERVAL seconds (default: 6 hours).
        """
        now = time.time()
        if not force and self._browse_tv_cache and (now - self._last_browse_tv_fetch < THESPORTSDB_BROWSE_TV_INTERVAL):
            return self._browse_tv_cache

        async with self._browse_tv_lock:
            if not force and self._browse_tv_cache and (now - self._last_browse_tv_fetch < THESPORTSDB_BROWSE_TV_INTERVAL):
                return self._browse_tv_cache

            from app.services.tvvoo_service import tvvoo_service
            await tvvoo_service.ensure_synced()

            url = "https://www.thesportsdb.com/browse_tv"
            try:
                client = await self._get_client()
                res = await client.get(url)
                if res.status_code != 200:
                    logger.warning("TheSportsDB browse_tv returned status %d", res.status_code)
                    return self._browse_tv_cache
                html_content = res.text
            except Exception as e:
                logger.warning("Failed to fetch TheSportsDB browse_tv: %s", e)
                return self._browse_tv_cache

            cards = re.findall(
                r'<td\s+class=[\x27\x22]tv-event-card[\x27\x22][^>]*>(.*?)</td>',
                html_content,
                re.DOTALL | re.IGNORECASE,
            )
            if not cards:
                return self._browse_tv_cache

            target_countries = {
                "italy", "italia", "spain", "germany", "france",
                "united-kingdom", "united_kingdom", "world"
            }
            today_utc = datetime.datetime.now(datetime.timezone.utc).date()
            matches: List[Dict[str, Any]] = []

            for card in cards:
                ev_m = re.search(r'/event/(\d+)-([^\x27\x22]+)', card)
                ev_id = ev_m.group(1) if ev_m else ""
                ev_slug = ev_m.group(2) if ev_m else ""

                # Decode untruncated event name from the URL slug (TSDB HTML cards truncate title at ~32 chars)
                slug_title = ""
                if ev_slug:
                    slug_unquoted = urllib.parse.unquote(ev_slug).replace('-', ' ')
                    slug_title = re.sub(r'\s+vs\s+', ' vs ', slug_unquoted.title(), flags=re.IGNORECASE).strip()

                t_m = re.search(r'calendar\.png[^\>]*\>\s*([^<]+)<', card)
                card_title = t_m.group(1).strip() if t_m else ""

                # Prefer untruncated slug title if card title was truncated or missing
                if slug_title and (not card_title or len(slug_title) > len(card_title)):
                    title = slug_title
                else:
                    title = card_title or slug_title
                if not title:
                    continue

                time_m = re.search(r'(\d{2}:\d{2})\s*UTC', card)
                time_str = time_m.group(1) if time_m else "00:00"

                try:
                    dt = datetime.datetime.strptime(f"{today_utc} {time_str}", "%Y-%m-%d %H:%M").replace(
                        tzinfo=datetime.timezone.utc
                    )
                    date_ms = int(dt.timestamp() * 1000)
                except Exception:
                    date_ms = int(time.time() * 1000)

                # Poster thumbnail
                th_m = re.search(r'src=[\x27\x22]([^\x27\x22]*thumb[^\x27\x22]*)[\x27\x22]', card)
                thumb = th_m.group(1) if th_m else None
                if thumb and "no_thumb" not in thumb:
                    thumb = re.sub(r'/(?:small|tiny|preview)$', '', thumb)
                    if not thumb.endswith('/medium'):
                        thumb = f"{thumb}/medium"
                else:
                    thumb = None

                # Channel matching for target countries
                ch_matches = re.findall(
                    r'<img[^>]*flags/([^.\x27\x22/]+)\.(?:svg|png)[^>]*>\s*<a\s+href=[\x27\x22]/channel/(\d+)-([^\x27\x22]+)[\x27\x22][^>]*/*>([^<]+)</a>',
                    card,
                    re.DOTALL,
                )

                sources: List[Dict[str, Any]] = []
                seen_streams = set()

                for flag, ch_id, ch_slug, ch_name in ch_matches:
                    flag_norm = flag.lower().replace('_', '-')
                    is_target = any(tc in flag_norm or flag_norm in tc for tc in target_countries)
                    if not is_target:
                        continue

                    streams = tvvoo_service.get_channel_streams(ch_name.strip())
                    for st in streams:
                        key = (st.get("canonical"), st.get("tag"))
                        if key not in seen_streams and st.get("url"):
                            seen_streams.add(key)
                            sources.append({
                                "source": "tvvoo",
                                "id": st.get("canonical", "tvvoo"),
                                "name": st.get("display_name", ch_name.strip()),
                                "url": st["url"],
                                "tag": st.get("tag", "c"),
                                "country": st.get("country", ""),
                            })

                # GHOST EVENT FILTER: Must have at least one playable TvVoo source
                if not sources:
                    continue

                # Prioritize Italian TvVoo sources at index 0..k
                sources.sort(key=lambda s: 0 if s.get("country") == "Italy" or "🇮🇹" in s.get("name", "") else 1)

                # Structured teams
                teams_dict = None
                if " vs " in title:
                    parts = title.split(" vs ", 1)
                    teams_dict = {"home": {"name": parts[0].strip()}, "away": {"name": parts[1].strip()}}
                elif " - " in title:
                    parts = title.split(" - ", 1)
                    teams_dict = {"home": {"name": parts[0].strip()}, "away": {"name": parts[1].strip()}}

                # Category / Silo resolution from calendar cache if available
                silo = "altri_sport"
                comp = ""
                if self._calendar_cache:
                    clean_title_tokens = set(re.sub(r'[^a-z0-9]+', ' ', title.lower()).split())
                    for cev in self._calendar_cache:
                        cev_title = cev.get("title", "").strip().lower()
                        if cev_title == title.lower():
                            silo = cev.get("_silo", "altri_sport")
                            comp = cev.get("competition", "")
                            if not thumb and cev.get("thumb"):
                                thumb = cev.get("thumb")
                            break
                        # Token similarity match in calendar cache
                        cev_tokens = set(re.sub(r'[^a-z0-9]+', ' ', cev_title).split())
                        if len(clean_title_tokens) >= 2 and len(cev_tokens) >= 2:
                            common = clean_title_tokens.intersection(cev_tokens)
                            if len(common) >= 2 and len(common) >= min(len(clean_title_tokens), len(cev_tokens)) - 1:
                                silo = cev.get("_silo", "altri_sport")
                                comp = cev.get("competition", "")
                                if not thumb and cev.get("thumb"):
                                    thumb = cev.get("thumb")
                                break

                # Keyword fallback for silo if not in calendar
                if silo == "altri_sport":
                    t_low = title.lower()
                    if any(w in t_low for w in ("basket", "baloncesto", "olimpia", "virtus", "partizan", "barcelona basket", "real madrid basket", "euroleague")):
                        silo = "basketball"
                    elif any(w in t_low for w in ("fc ", "united", "inter", "milan", "juventus", "calcio", "madrid", "harrogate", "aldershot", "gateshead", "spain vs croatia", "england", "scotland", "national league")):
                        silo = "football"
                    elif any(w in t_low for w in ("nfl", "bears", "eagles", "patriots", "chiefs")):
                        silo = "american-football"
                    elif any(w in t_low for w in ("braves", "phillies", "astros", "white sox", "yankees", "red sox", "mlb")):
                        silo = "baseball"
                    elif any(w in t_low for w in ("hurricanes", "panthers", "nhl", "rangers", "bruins")):
                        silo = "hockey"

                slug_title = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                match_id = f"tsdb_tv_{ev_id or slug_title}_{int(date_ms / 1000)}"

                match_obj: Dict[str, Any] = {
                    "id": match_id,
                    "title": title,
                    "category": silo,
                    "_silo": silo,
                    "competition": comp,
                    "_competition": comp,
                    "date": date_ms,
                    "sources": sources,
                    "popular": False,
                    "poster": thumb,
                    "_source_type": "thesportsdb_tv",
                }
                if teams_dict:
                    match_obj["teams"] = teams_dict

                matches.append(match_obj)

            self._browse_tv_cache = matches
            self._last_browse_tv_fetch = now
            logger.info("TheSportsDB browse_tv: estratti %d eventi con stream TvVoo pronti.", len(matches))
            return matches


thesportsdb_service = TheSportsDBService()
