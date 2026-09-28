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

    async def fetch_multi_day_calendar(self, days_ahead: int = 2) -> List[Dict[str, Any]]:
        """
        Fetches the official TheSportsDB calendar for today and upcoming days (default 2 days ahead).
        Indexes events by Silo for instant zero-latency match enrichment.
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

            for day_offset in range(days_ahead + 1):
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
        r"\bvirtus bologna\b": "virtus",
        r"\bsegafredo virtus bologna\b": "virtus",
        r"\bolimpia milano\b": "milano",
        r"\bea7 emporio armani milano\b": "milano",
        r"\bea7 milano\b": "milano",
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

    def _extract_teams(self, match: Dict[str, Any]) -> Tuple[str, str]:
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
                    home = self._clean_team_name(parts[0])
                    away = self._clean_team_name(parts[1])
                    break
        return home, away

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
            silo = m.get("_silo") or m.get("category") or ""
            candidates = self._calendar_by_silo.get(silo, [])
            if not candidates:
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

        if reconciled_count > 0:
            logger.info("TheSportsDB Level 1: %d match allacciati con successo al calendario ufficiale.", reconciled_count)
        return reconciled_count

    def enrich_matches(self, matches: List[Dict[str, Any]]) -> int:
        """
        Cross-matches upstream matches with the cached official TheSportsDB calendar.
        Delegates to Level 1 reconcile_matches.
        """
        return self.reconcile_matches(matches)

    async def search_event_thumb_html(self, home_raw: str, away_raw: str) -> Optional[str]:
        """
        LEVEL 2 SEARCH: Queries TheSportsDB web search (browse?s=...)
        using clean primary team keywords (e.g. 'Ostia Forli').
        Parses matching event thumb and returns high-quality poster URL.
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
                r"<a\s+href=['\"]/event/(\d+)-([^'\"]+)['\"][^>]*>(.*?)</a>",
                re.DOTALL | re.IGNORECASE,
            )
            for ev_id, slug, inner in pattern.findall(res.text):
                slug_norm = unicodedata.normalize("NFKD", urllib.parse.unquote(slug)).encode("ascii", "ignore").decode("utf-8").lower()
                if h_kw in slug_norm and a_kw in slug_norm:
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

            thumb = await self.search_event_thumb_html(m_home, m_away)
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


thesportsdb_service = TheSportsDBService()
