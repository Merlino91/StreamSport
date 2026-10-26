import asyncio
import datetime
from difflib import SequenceMatcher
import logging
import os
import re
import sys
import time
import unicodedata
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config import (
    CATALOG_DEFINITIONS,
    CATALOG_ID,
    CATALOG_NAME,
    CATALOG_SYNC_INTERVAL,
    CATALOG_TYPE,
    ENABLE_REPLAYS,
    SPORT_GENRES,
    STREAMED_API_HOST,
)
from app.services.daddylive_api import daddylive_api
from app.services.db_service import db_service
from app.services.espn_service import espn_service
from app.services.genre_classifier import genre_classifier
from app.services.ocblacktop_service import ocblacktop_service
from app.services.streamed_api import streamed_api
from app.services.banner_service import banner_service, BANNER_VERSION
from app.services.tennis_poster_service import tennis_poster_service
from app.services.thesportsdb_service import thesportsdb_service
from app.services.tvvoo_service import tvvoo_service
from app.services.fctv33_service import fctv33_service
from app.services.mandrakodi_service import mandrakodi_service
from app.services.spon_service import spon_service
from app.services.italian_epg_service import italian_epg_service
from app.services.diretta_service import diretta_service
from app.services.team_matching import extract_team_name
from app.services.canonical_competition_service import (
    detect_canonical_competition,
    get_canonical_display_name,
    is_competition_conflict,
    is_redzone_event,
    is_simulcast_event,
    get_simulcast_info,
)



try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

DEFAULT_TIMEZONE = "Europe/Rome"

def get_safe_timezone(user_tz: Optional[str] = None) -> datetime.tzinfo:
    """Safely resolves timezone from user string, gracefully falling back to Europe/Rome or UTC."""
    target = (user_tz or DEFAULT_TIMEZONE).strip()
    if ZoneInfo is not None:
        try:
            return ZoneInfo(target)
        except (ZoneInfoNotFoundError, ValueError, Exception):
            try:
                return ZoneInfo(DEFAULT_TIMEZONE)
            except Exception:
                pass
    try:
        import pytz
        return pytz.timezone(target)
    except Exception:
        pass
    return datetime.timezone.utc


logger = logging.getLogger("streamsport.catalog")

class CatalogService:
    """
    Builds Stremio Catalog and Meta objects for StreamSport with granular genre filtering.
    """

    def __init__(self):
        self._cached_matches: List[Dict[str, Any]] = []
        self._matches_by_id: Dict[str, Dict[str, Any]] = {}
        self._last_sync_time: float = 0.0
        self._sync_lock: asyncio.Lock = asyncio.Lock()
        self._background_task: Optional[asyncio.Task] = None
        self._is_running: bool = False
        self._sync_reset_event: asyncio.Event = asyncio.Event()

    def get_current_sync_interval(self) -> int:
        """
        Dynamic sync interval based on Rome time:
        - 12:00 to 23:59: 3600 seconds (1 hour) for afternoon/evening sports coverage.
        - 00:00 to 11:59: 14400 seconds (4 hours) for overnight/morning (00:00, 04:00, 08:00, 12:00).
        """
        tz = get_safe_timezone("Europe/Rome")
        now_rome = datetime.datetime.now(tz)
        if 12 <= now_rome.hour <= 23:
            return 3600
        return 14400

    def reset_sync_timer(self) -> None:
        """Resets the background catalog sync timer to postpone the next scheduled sync by a full interval."""
        self._last_sync_time = time.time()
        if hasattr(self, "_sync_reset_event"):
            self._sync_reset_event.set()

    def reconcile_mandrakodi_live_matches(self, mandrakodi_matches: List[Dict[str, Any]]) -> None:
        """
        Merges newly discovered Mandrakodi live/OTT matches into the catalog in memory
        and resets the background sync timer.
        """
        if not mandrakodi_matches:
            return
        if self._cached_matches:
            merged = self.merge_and_deduplicate_by_silos(self._cached_matches, mandrakodi_matches)
            # Standalone uncertified Mandrakodi matches cannot create new orphan cards
            self._cached_matches = [
                m for m in merged
                if not (str(m.get("id", "")).startswith("mandrakodi:") and not m.get("_diretta_matched"))
            ]
        else:
            self._cached_matches = [
                m for m in mandrakodi_matches
                if m.get("_diretta_matched")
            ]

        for m in self._cached_matches:
            mid = m.get("id")
            if mid:
                self._matches_by_id[str(mid)] = m

        self.reset_sync_timer()

    def format_event_date(self, timestamp_ms: int, user_tz: Optional[str] = None) -> str:
        """Converts timestamp in ms to localized Italian date string (e.g. '21 Sep 2026, 20:45')."""
        tz = get_safe_timezone(user_tz)
        dt_utc = datetime.datetime.fromtimestamp(timestamp_ms / 1000, tz=datetime.timezone.utc)
        dt_local = dt_utc.astimezone(tz)
        return dt_local.strftime("%d %b %Y, %H:%M").lstrip("0")

    def normalize_image_url(self, path: Optional[str], base_url: Optional[str] = None) -> Optional[str]:
        """Routes image URL through addon image proxy to bypass ISP blocks."""
        if not path:
            return None
        if path.startswith("/posters/"):
            return f"{base_url}{path}" if base_url else path
        if not path.startswith("http://") and not path.startswith("https://"):
            full_url = f"https://{STREAMED_API_HOST}{path}"
        else:
            full_url = path

        if base_url:
            encoded = urllib.parse.quote(full_url, safe="")
            return f"{base_url}/image-proxy?url={encoded}"

        return full_url

    @staticmethod
    def split_title_and_competition(raw_title: str, genre: Optional[str] = None) -> Tuple[str, str]:
        """
        Splits raw title into (clean_title, competition_name).
        clean_title contains only competitors and preserves team flag emojis,
        while removing decorative emojis.
        competition_name is stripped of all emojis and prefixes.
        """
        sim_info = get_simulcast_info(raw_title, genre)
        if sim_info:
            return sim_info["title"], sim_info["competition"]

        title = (raw_title or "").strip()
        comp = ""

        # 1. Colon pattern: 'Prefix / League: Competitors'
        if ":" in title:
            parts = title.split(":", 1)
            if any(sep in parts[1].lower() for sep in (" vs ", " v ")):
                comp = parts[0].strip()
                title = parts[1].strip()

        # 2. Parentheses pattern: 'Competitors (Tournament)'
        m = re.search(r"^(.*?)\s*\(([^)]+)\)\s*$", title)
        if m:
            c1, c2 = m.group(1).strip(), m.group(2).strip()
            if any(sep in c1.lower() for sep in (" vs ", " v ")):
                title = c1
                comp = c2

        # Strip non-flag decorative emojis from title (preserve flag emojis)
        non_flag_pattern = (
            r"[\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
            r"\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF"
            r"\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
            r"\U00002600-\U000026FF\U00002700-\U000027BF\U00002B50]"
        )
        clean_title = re.sub(non_flag_pattern, "", title).strip()
        clean_title = re.sub(r"^\s*[-–:|]\s*", "", clean_title).strip()
        clean_title = re.sub(r"\s+", " ", clean_title).strip()

        # Clean competition: strip ALL emojis (flags, cups, and non-flags)
        all_emoji_pattern = r"[\U0001F1E6-\U0001F1FF]{2}|" + non_flag_pattern
        clean_comp = re.sub(all_emoji_pattern, "", comp).strip()
        clean_comp = re.sub(r"^\s*[-–:|]\s*", "", clean_comp).strip()
        clean_comp = re.sub(r"\s+", " ", clean_comp).strip()

        if not clean_comp and genre:
            clean_comp = re.sub(all_emoji_pattern, "", genre).strip()

        return clean_title, clean_comp

    @classmethod
    def get_live_window_minutes(cls, match: Dict[str, Any]) -> int:
        """
        Returns the duration in minutes an event remains 'LIVE ORA' after its kickoff/start time.
        Strictly determined by the canonical SPORT SILO of the event.
        No complicated tournament or league checks needed: every sport has its standard duration.
        """
        silo = cls.get_canonical_silo(match)

        # 1. Calcio (Soccer): 160 minutes (~2h 40m covers 90m + halftime + stoppage + extra-time/penalties)
        if silo == "football":
            return 160

        # 2. Basket (Basketball): 180 minutes (~3h covers 4 quarters + overtime + post-match)
        if silo == "basketball":
            return 180

        # 3. Tennis: 160 minutes for VS matches, 360 minutes (6 hours) for tournament sessions / main events
        if silo == "tennis":
            title = (match.get("title") or "").lower()
            teams = match.get("teams")
            is_vs_match = (
                " vs " in title
                or " - " in title
                or (isinstance(teams, dict) and bool(teams.get("home")) and bool(teams.get("away")))
            )
            is_session = bool(re.search(r"\b(atp\s*masters|wta\s*1000|wta\s*500|wta\s*250|sessione|quarti\s*di\s*finale|semifinal|finali?|diretta)\b", title))
            if is_vs_match and not is_session:
                return 160
            return 360

        # 4. Motori (Motorsport): 80m for practice/qualifying/sprints, 160m for Grand Prix / main races
        if silo == "motor-sports":
            title = (match.get("title") or "").lower()
            if any(k in title for k in ("practice", "fp1", "fp2", "fp3", "qualif", "sprint", "warm up", "warm-up")):
                return 80
            return 160

        # 5. Football Americano (NFL / College): 230 minutes (~3h 50m)
        if silo == "american-football":
            return 230

        # 6. Baseball (MLB): 200 minutes (~3h 20m)
        if silo == "baseball":
            return 200

        # 7. Hockey (NHL): 170 minutes (~2h 50m)
        if silo == "hockey":
            return 170

        # 8. Sport da combattimento (UFC / Boxe / MMA): 240 minutes (main card ~4h)
        if silo == "fight":
            return 240

        # 9. Volley / Pallavolo: 150 minutes (~2h 30m up to 5 sets)
        if silo == "volley":
            return 150

        # 10. Rugby: 140 minutes (~2h 20m)
        if silo == "rugby":
            return 140

        # 11. Altri Sport (fallback generico): 180 minutes (3 hours max)
        return 180

    def build_meta_item(
        self,
        match: Dict[str, Any],
        genre: str,
        user_tz: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Converts a raw match object into a Stremio meta preview object."""
        match_id = match.get("id", "")
        display_title = match.get("display_title_it") or match.get("_italian_title") or match.get("title", "Live Sports")
        date_ms = match.get("date", 0)
        is_popular = match.get("popular", False)
        poster_url = self.normalize_image_url(match.get("poster"), base_url=base_url)

        clean_title, comp_name = self.split_title_and_competition(display_title, genre)
        if not comp_name:
            comp_name = match.get("competition") or match.get("_competition") or ""

        now_ms = time.time() * 1000
        desc_parts = []

        # Determine match status and timing for dynamic banner
        banner_status = "upcoming"
        banner_time = ""
        live_window = self.get_live_window_minutes(match)

        if not date_ms:
            formatted_date = "Live"
            status_text = "🔴 LIVE ORA"
            banner_status = "live"
        else:
            formatted_date = self.format_event_date(date_ms, user_tz)
            diff_mins = int((date_ms - now_ms) / 60000)

            if diff_mins < -live_window:
                status_text = "🏁 Conclusa • Replay e Sintesi"
                banner_status = "replay"
            elif diff_mins <= 0:
                status_text = "🔴 LIVE ORA"
                banner_status = "live"
            else:
                if diff_mins <= 20:
                    status_text = f"⏳ Inizio tra {diff_mins} min"
                elif diff_mins < 1440:
                    status_text = f"📅 Inizio tra {diff_mins // 60}h {diff_mins % 60}m"
                else:
                    days_left = diff_mins // 1440
                    status_text = f"📅 Tra {days_left} giorni"

                banner_status = "upcoming"
                tz = get_safe_timezone(user_tz)
                dt = datetime.datetime.fromtimestamp(date_ms / 1000.0, tz=tz)
                months_it = {
                    1: "GEN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAG", 6: "GIU",
                    7: "LUG", 8: "AGO", 9: "SET", 10: "OTT", 11: "NOV", 12: "DIC"
                }
                m_str = months_it.get(dt.month, dt.strftime("%b").upper())
                banner_time = f"{dt.strftime('%d')} {m_str} • {dt.strftime('%H:%M')}"

        # Extract certified competition name
        GENERIC_GENRES = {
            "", "other", "unknown", "altri sport", "live sports", "football", "soccer",
            "calcio", "basket", "tennis", "undefined",
            "americhe e leghe extra-ue", "altre leghe europee", "altri campionati europei",
            "calcio internazionale", "partite amichevoli", "campionati minori",
            "leghe europee hockey", "leghe europee"
        }

        comp_badge = ""
        canon = match.get("canonical_competition") or match.get("_canonical_competition")
        raw_comp = match.get("competition") or match.get("_competition") or comp_name or ""
        c_clean = re.sub(r"[\U00010000-\U0010ffff]", "", raw_comp).strip(": -")

        # 1. Specific Canonical competition first (official display name, unless it was generic hockey_euro)
        if canon and canon != "hockey_euro":
            comp_display = get_canonical_display_name(canon)
            if comp_display:
                comp_badge = f"🏆 {comp_display}"

        # 2. Specific competition from Diretta / Livesport / TheSportsDB
        if not comp_badge:
            if c_clean and c_clean.lower() not in GENERIC_GENRES:
                comp_badge = f"🏆 {c_clean}"

        # 2b. Fallback to generic canonical display name if available
        if not comp_badge and canon:
            comp_display = get_canonical_display_name(canon)
            if comp_display:
                comp_badge = f"🏆 {comp_display}"

        # 3. Fallback to genre only if no specific competition was identified
        if not comp_badge:
            if genre and genre.lower() not in ("altri sport", "other", "unknown", "live sports"):
                comp_badge = genre
            elif comp_name:
                comp_badge = comp_name

        # Description structure: Status first -> Certified Competition with trophy -> Competitors -> Popular
        desc_parts.append(status_text)
        if comp_badge:
            desc_parts.append(comp_badge)
        if clean_title:
            desc_parts.append(clean_title)

        if is_popular:
            desc_parts.append("⭐ In Evidenza")

        description = " • ".join(desc_parts)
        stremio_id = f"streamsport:{match_id}"

        has_tvvoo = any(
            isinstance(s, dict) and s.get("source") == "tvvoo"
            for s in match.get("sources", [])
        )

        clean_match_id = match_id or "unknown"
        poster_endpoint = f"/poster/{clean_match_id}.jpg?s={banner_status}"
        if banner_time:
            poster_endpoint += f"&t={urllib.parse.quote(banner_time)}"
        if has_tvvoo:
            poster_endpoint += "&tv=1"
        poster_endpoint += f"&v={BANNER_VERSION}"

        dynamic_poster_url = f"{base_url}{poster_endpoint}" if base_url else poster_endpoint

        item = {
            "id": stremio_id,
            "type": CATALOG_TYPE,
            "name": clean_title if clean_title else display_title,
            "genres": [genre],
            "posterShape": "landscape",
            "description": description,
            "releaseInfo": formatted_date,
            "poster": dynamic_poster_url,
            "background": dynamic_poster_url,
            "_date_ms": date_ms,
            "_live_window": live_window,
        }
        return item

    def _clean_tokens(self, title: str) -> str:
        t = (title or "").lower()
        t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("utf-8")
        t = re.sub(r"\|\s*[^|]+$", "", t)
        t = re.sub(r"^[^:]+:\s*", "", t)
        t = re.sub(r"\([^)]*\)", "", t)
        t = re.sub(r"^(?:italy|england|spain|germany|france|uefa|fifa|brazil|usa|[a-z]+)\s*-\s*", "", t)
        t = re.sub(r"[^a-z0-9]+", " ", t).strip()
        EXCLUDED_NOISE_WORDS = (
            "vs", "the", "fc", "ac", "cf", "sc", "as", "at", "live", "stream", "bc",
            "pallacanestro", "pallacan", "baloncesto", "basquet", "basket", "calcio", "volley", "pallavolo", "club"
        )
        COMMON_ALIASES = {
            "internazionale": "inter",
            "juve": "juventus",
            "wolves": "wolverhampton",
            "spurs": "tottenham",
            "blazers": "trailblazers",
        }
        words = sorted([COMMON_ALIASES.get(w, w) for w in t.split() if w not in EXCLUDED_NOISE_WORDS and len(w) > 1])
        return " ".join(words)

    def _get_teams_key(self, match: Dict[str, Any]) -> Optional[Any]:
        if is_simulcast_event(match.get("title", "")) or is_simulcast_event(str(match.get("id", ""))):
            return None
        teams = match.get("teams")
        if isinstance(teams, dict) and teams.get("home") and teams.get("away"):
            home_val = teams.get("home")
            home_str = home_val.get("name") if isinstance(home_val, dict) else str(home_val)
            away_val = teams.get("away")
            away_str = away_val.get("name") if isinstance(away_val, dict) else str(away_val)
            h = re.sub(r"[^a-z0-9]+", " ", (home_str or "").lower()).strip()
            a = re.sub(r"[^a-z0-9]+", " ", (away_str or "").lower()).strip()
            EXCLUDED_CLUB_TERMS = (
                "fc", "ac", "cf", "sc", "as", "the", "bc",
                "pallacanestro", "pallacan", "baloncesto", "basquet", "basket", "calcio", "volley", "pallavolo", "club"
            )
            h_words = frozenset([w for w in h.split() if w not in EXCLUDED_CLUB_TERMS and len(w) > 1])
            a_words = frozenset([w for w in a.split() if w not in EXCLUDED_CLUB_TERMS and len(w) > 1])
            if h_words and a_words:
                return frozenset([h_words, a_words])
        return None

    @classmethod
    def get_canonical_silo(cls, m: Dict[str, Any]) -> str:
        """
        Determines the canonical sport silo for a match, reconciling
        inconsistencies between _catalog, _silo tags, category strings, and classifier.
        """
        # 1. Direct resolution from certified _catalog
        cat = (m.get("_catalog") or "").lower().strip()
        if cat in ("calcio_italiano", "calcio_estero"):
            return "football"
        if cat == "basket":
            return "basketball"
        if cat == "tennis":
            return "tennis"
        if cat == "motori":
            return "motor-sports"
        if cat == "football_americano":
            return "american-football"

        # 2. Direct resolution from _silo tag
        s = (m.get("_silo") or "").lower().strip()
        if s:
            if s in ("soccer", "football"): return "football"
            if s in ("basket", "basketball"): return "basketball"
            if s in ("volleyball", "volley"): return "volley"
            if s in ("motor-sports", "motorsport", "motori"): return "motor-sports"
            if s in ("fight", "combattimento", "boxe", "boxing", "mma", "ufc"): return "fight"
            return s

        # 2b. Guard for upstream mislabeled niche sports
        t_low = (m.get("title") or "").lower()
        if any(w in t_low for w in ("handball", "pallamano", "ehf", "🤾", "snooker", "billiards")):
            return "altri_sport"

        # 3. Direct resolution from provider category string
        c = (m.get("category") or "").lower().strip()
        if c in ("soccer", "football"): return "football"
        if c in ("basket", "basketball"): return "basketball"
        if c in ("volleyball", "volley"): return "volley"
        if c in ("motor-sports", "motorsport", "motori"): return "motor-sports"
        if c in ("fight", "combattimento", "boxe", "boxing", "mma", "ufc"): return "fight"
        if c in ("baseball",): return "baseball"
        if c in ("hockey",): return "hockey"
        if c in ("american-football", "nfl", "cfl"): return "american-football"
        if c in ("tennis",): return "tennis"

        # 4. Fallback: inspect via genre classifier heuristic
        try:
            from app.services.genre_classifier import genre_classifier
            cl_cat, _ = genre_classifier.classify(m)
            if cl_cat in ("calcio_italiano", "calcio_estero"):
                return "football"
            if cl_cat == "basket":
                return "basketball"
            if cl_cat == "tennis":
                return "tennis"
            if cl_cat == "motori":
                return "motor-sports"
            if cl_cat == "football_americano":
                return "american-football"
        except Exception:
            pass

        return "altri_sport"

    @classmethod
    def is_silo_compatible(cls, s1: str, s2: str) -> bool:
        """Determines if two sport silo names are compatible for deduplication."""
        s1 = (s1 or "").lower().strip()
        s2 = (s2 or "").lower().strip()
        if not s1 or not s2:
            return True
        c1 = cls.get_canonical_silo({"_silo": s1, "category": s1})
        c2 = cls.get_canonical_silo({"_silo": s2, "category": s2})
        return c1 == c2

    @staticmethod
    def _is_token_match(tokens1: str, tokens2: str) -> bool:
        """
        Determines if two token strings represent the exact same match.
        Robust against:
        1. Inverted team order (Home vs Away vs Away vs Home)
        2. Spelling / transliteration differences (Turkey vs Türkiye, Munich vs München)
        3. Minor competition name additions
        Guarantees that rival clubs (e.g. Real Madrid vs Atletico Madrid) NEVER merge.
        """
        w1 = [w for w in tokens1.split() if len(w) > 1]
        w2 = [w for w in tokens2.split() if len(w) > 1]
        if not w1 or not w2:
            return False
        if w1 == w2:
            return True

        b1 = [max(SequenceMatcher(None, x, y).ratio() for y in w2) for x in w1]
        b2 = [max(SequenceMatcher(None, y, x).ratio() for x in w1) for y in w2]
        avg1 = sum(b1) / len(b1)
        avg2 = sum(b2) / len(b2)
        min1 = min(b1)
        min2 = min(b2)

        # Equal word count: each word must have a strong counterpart (ratio >= 0.70)
        # and high overall similarity (average >= 0.80)
        if len(w1) == len(w2):
            return (avg1 + avg2) / 2.0 >= 0.80 and min1 >= 0.70 and min2 >= 0.70

        # Unequal word count: the smaller title (at least 2 words) must be fully and cleanly covered in the larger title
        if len(w1) < len(w2):
            return len(w1) >= 2 and avg1 >= 0.85 and min1 >= 0.70
        else:
            return len(w2) >= 2 and avg2 >= 0.85 and min2 >= 0.70

    @staticmethod
    def _clean_team_str(s: str) -> str:
        s = (s or "").lower().strip()
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("utf-8")
        s = re.sub(r"\b(pallacanestro|pallacan|baloncesto|basquet|basket|bc|fc|ac|cf|sc|as|calcio|volley|pallavolo|club)\b", " ", s)
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()

    @classmethod
    def _teams_match_fuzzy(cls, t1: Optional[Dict[str, Any]], t2: Optional[Dict[str, Any]]) -> bool:
        """
        Symmetrically checks if two structured teams objects match (direct order or inverted).
        Returns True if (Home1 ≈ Home2 and Away1 ≈ Away2) OR (Home1 ≈ Away2 and Away1 ≈ Home2).
        """
        if not isinstance(t1, dict) or not isinstance(t2, dict):
            return False
        h1 = cls._clean_team_str(extract_team_name(t1.get("home")))
        a1 = cls._clean_team_str(extract_team_name(t1.get("away")))
        h2 = cls._clean_team_str(extract_team_name(t2.get("home")))
        a2 = cls._clean_team_str(extract_team_name(t2.get("away")))
        if not h1 or not a1 or not h2 or not a2:
            return False

        # Direct comparison (Home1 vs Home2, Away1 vs Away2)
        d1 = SequenceMatcher(None, h1, h2).ratio()
        d2 = SequenceMatcher(None, a1, a2).ratio()
        if d1 >= 0.75 and d2 >= 0.75:
            return True

        # Inverted comparison (Away @ Home / provider reversed: Home1 vs Away2, Away1 vs Home2)
        i1 = SequenceMatcher(None, h1, a2).ratio()
        i2 = SequenceMatcher(None, a1, h2).ratio()
        if i1 >= 0.75 and i2 >= 0.75:
            return True

        return False

    @classmethod
    def calc_similarity(cls, tokens1: str, tokens2: str) -> float:
        """Calculates token-level similarity ratio between 0.0 and 1.0."""
        if not tokens1 or not tokens2:
            return 0.0
        if tokens1 == tokens2:
            return 1.0
        w1 = set(tokens1.split())
        w2 = set(tokens2.split())
        if w1 == w2:
            return 1.0
        if w1.issubset(w2) or w2.issubset(w1):
            return 0.88
        seq = SequenceMatcher(None, tokens1, tokens2).ratio()
        overlap = len(w1 & w2)
        if overlap:
            r1 = overlap / len(w1)
            r2 = overlap / len(w2)
            token_ratio = (r1 + r2) / 2.0
            return max(token_ratio, seq)
        return seq

    def merge_and_deduplicate(
        self,
        primary_matches: List[Dict[str, Any]],
        secondary_matches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        merged_list: List[Dict[str, Any]] = [dict(m) for m in primary_matches]
        id_to_idx: Dict[str, int] = {}
        for idx, m in enumerate(merged_list):
            m_id = m.get("id")
            if m_id:
                id_to_idx[str(m_id)] = idx
            if m.get("title") and "raw_titles" not in m:
                m["raw_titles"] = [m["title"]]
            if m.get("teams") and "alt_teams" not in m:
                m["alt_teams"] = [m["teams"]]

        for m2 in secondary_matches:
            m2_id = m2.get("id")
            m2_tokens = self._clean_tokens(m2.get("title", ""))
            m2_teams = self._get_teams_key(m2)
            m2_date = m2.get("date") or 0
            m2_silo = self.get_canonical_silo(m2)
            is_m2_simulcast = is_simulcast_event(m2.get("title", "")) or is_simulcast_event(str(m2_id or ""))

            matched_idx = -1

            # 0. Direct ID matching: identical ID is 100% the exact same match! (O(1) instant hash lookup)
            if m2_id and str(m2_id) in id_to_idx:
                matched_idx = id_to_idx[str(m2_id)]

            # 1. Similarity and weighted scoring matching within the same silo
            if matched_idx < 0:
                for idx, existing in enumerate(merged_list):
                    ex_silo = self.get_canonical_silo(existing)
                    if ex_silo != m2_silo:
                        continue

                    is_ex_simulcast = is_simulcast_event(existing.get("title", "")) or is_simulcast_event(str(existing.get("id", "")))

                    # RULE: A simulcast show and a regular 1v1 match MUST NEVER merge!
                    if is_m2_simulcast != is_ex_simulcast:
                        continue

                    ex_date = existing.get("date") or 0
                    time_diff_ms = abs(ex_date - m2_date) if ex_date and m2_date else 0
                    time_diff_min = time_diff_ms / 60000.0

                    # 1a. Special Simulcast & Multi-match show matching (coincides within 4h window)
                    if is_m2_simulcast and is_ex_simulcast:
                        if time_diff_min <= 240:
                            m2_info = get_simulcast_info(m2.get("title", "")) or get_simulcast_info(str(m2_id or ""))
                            ex_info = get_simulcast_info(existing.get("title", "")) or get_simulcast_info(str(existing.get("id", "")))
                            if m2_info and ex_info and m2_info["title"] == ex_info["title"]:
                                matched_idx = idx
                                break
                            if self.calc_similarity(m2_tokens, self._clean_tokens(existing.get("title", ""))) >= 0.70:
                                matched_idx = idx
                                break
                        continue

                    # 1b. Date Compatibility: hard cutoff at 45m for regular matches
                    if ex_date and m2_date:
                        if time_diff_min > 45:
                            continue

                    # 1c. Base Team / Title Similarity
                    ex_teams = self._get_teams_key(existing)
                    ex_tokens = self._clean_tokens(existing.get("title", ""))

                    base_sim = 0.0
                    if m2_teams and ex_teams:
                        if m2_teams == ex_teams:
                            base_sim = 1.0
                        elif self._teams_match_fuzzy(m2.get("teams"), existing.get("teams")):
                            base_sim = 0.85
                        else:
                            base_sim = self.calc_similarity(m2_tokens, ex_tokens)
                    elif m2_tokens and ex_tokens:
                        base_sim = self.calc_similarity(m2_tokens, ex_tokens)

                    # Backwards-compatible token match boost
                    if self._is_token_match(m2_tokens, ex_tokens) and base_sim < 0.75:
                        base_sim = 0.80

                    if base_sim < 0.45:
                        continue

                    # 1d. Soft Time Penalty
                    time_penalty = 0.0
                    if time_diff_min > 30:
                        time_penalty = 0.08
                    elif time_diff_min > 15:
                        time_penalty = 0.04

                    # 1e. Competition Modifier (+0.08 bonus if matching, -0.20 if in direct conflict, 0.0 neutral if either is None)
                    c1 = existing.get("canonical_competition")
                    c2 = m2.get("canonical_competition")
                    comp_mod = 0.0
                    if c1 and c2:
                        if c1 == c2:
                            comp_mod = 0.08
                        elif is_competition_conflict(c1, c2):
                            comp_mod = -0.20

                    final_score = base_sim - time_penalty + comp_mod

                    if final_score >= 0.68:
                        matched_idx = idx
                        break

            if matched_idx >= 0:
                existing = merged_list[matched_idx]
                existing_sources = existing.setdefault("sources", [])
                existing_ids = {str(s.get("id")) for s in existing_sources if isinstance(s, dict)}

                new_tvvoo = []
                new_other = []
                for s in m2.get("sources", []):
                    if isinstance(s, dict) and str(s.get("id")) not in existing_ids:
                        existing_ids.add(str(s.get("id")))
                        if s.get("source") == "tvvoo":
                            new_tvvoo.append(s)
                        else:
                            new_other.append(s)

                if new_tvvoo or new_other:
                    existing["sources"] = new_tvvoo + existing_sources + new_other

                # Preserve all variant titles across merged providers
                raw_titles = existing.setdefault("raw_titles", [])
                if existing.get("title") and existing["title"] not in raw_titles:
                    raw_titles.append(existing["title"])
                m2_title = m2.get("title")
                if m2_title and m2_title not in raw_titles:
                    raw_titles.append(m2_title)

                # Preserve all alternative team objects across merged providers
                alt_teams = existing.setdefault("alt_teams", [])
                if existing.get("teams") and existing["teams"] not in alt_teams:
                    alt_teams.append(existing["teams"])
                m2_teams = m2.get("teams")
                if m2_teams and m2_teams not in alt_teams:
                    alt_teams.append(m2_teams)

                if not existing.get("teams") and m2.get("teams"):
                    existing["teams"] = m2["teams"]
                p_ex = str(existing.get("poster") or "")
                p_m2 = str(m2.get("poster") or "")
                if (not p_ex or "nostream" in p_ex) and (p_m2 and "nostream" not in p_m2):
                    existing["poster"] = m2["poster"]
                elif p_m2.startswith("http") and not p_ex.startswith("http"):
                    existing["poster"] = m2["poster"]
                if not existing.get("date") and m2.get("date"):
                    existing["date"] = m2["date"]
                if not existing.get("canonical_competition") and m2.get("canonical_competition"):
                    existing["canonical_competition"] = m2["canonical_competition"]
                if not existing.get("competition") and m2.get("competition"):
                    existing["competition"] = m2["competition"]
                if not existing.get("_competition") and m2.get("_competition"):
                    existing["_competition"] = m2["_competition"]
                if not existing.get("_silo") and m2.get("_silo"):
                    existing["_silo"] = m2["_silo"]
                if not existing.get("_fctv33_id") and m2.get("_fctv33_id"):
                    existing["_fctv33_id"] = m2["_fctv33_id"]
                if not existing.get("_sport_type") and m2.get("_sport_type"):
                    existing["_sport_type"] = m2["_sport_type"]
            else:
                m2_dict = dict(m2)
                if m2_dict.get("title") and "raw_titles" not in m2_dict:
                    m2_dict["raw_titles"] = [m2_dict["title"]]
                if m2_dict.get("teams") and "alt_teams" not in m2_dict:
                    m2_dict["alt_teams"] = [m2_dict["teams"]]
                if m2_id:
                    id_to_idx[str(m2_id)] = len(merged_list)
                merged_list.append(m2_dict)


        return merged_list

    def merge_and_deduplicate_by_silos(
        self,
        primary_matches: List[Dict[str, Any]],
        secondary_matches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Groups matches by sport silo and performs deduplication within each silo,
        completely eliminating cross-sport collisions and accelerating merge operations.
        """
        from collections import defaultdict
        p_by_silo = defaultdict(list)
        s_by_silo = defaultdict(list)

        for m in primary_matches:
            silo = self.get_canonical_silo(m)
            p_by_silo[silo].append(m)

        for m in secondary_matches:
            silo = self.get_canonical_silo(m)
            s_by_silo[silo].append(m)

        all_silos = set(p_by_silo.keys()) | set(s_by_silo.keys())
        all_merged: List[Dict[str, Any]] = []
        for silo in sorted(all_silos):
            merged_silo = self.merge_and_deduplicate(p_by_silo[silo], s_by_silo[silo])
            all_merged.extend(merged_silo)

        return all_merged

    def link_official_schedule_to_provider_clusters(
        self,
        official_schedule: List[Dict[str, Any]],
        provider_clusters: List[Dict[str, Any]],
        time_window_minutes: float = 90.0,
    ) -> List[Dict[str, Any]]:
        """
        Links official Diretta/Flashscore schedule entries to deduplicated provider streaming clusters.
        Matching is partitioned strictly by sport silo, within an accepted 90-minute time window,
        using bilateral team fuzzy matching and trait protection (youth/women).
        """
        from collections import defaultdict
        from app.services.team_matching import calculate_match_score

        clusters_by_silo = defaultdict(list)
        for c in provider_clusters:
            silo = self.get_canonical_silo(c)
            clusters_by_silo[silo].append(c)

        claimed_cluster_ids = set()
        linked_schedule: List[Dict[str, Any]] = []

        for off in official_schedule:
            off_copy = dict(off)
            silo = off_copy.get("_silo") or self.get_canonical_silo(off_copy)
            candidates = clusters_by_silo.get(silo, [])
            off_date = off_copy.get("date") or 0

            # Time proximity: strictly within time_window_minutes (default 90m) in the same silo
            time_filtered = [
                c for c in candidates
                if c.get("id") not in claimed_cluster_ids
                and abs((c.get("date") or 0) - off_date) <= time_window_minutes * 60 * 1000
            ]

            best_cand = None
            best_score = 0.0
            for cand in time_filtered:
                score_res = calculate_match_score(cand, off_copy, phase="phase1_diretta")
                if score_res["matched"] and score_res["score"] > best_score:
                    best_score = score_res["score"]
                    best_cand = cand
                    if best_score >= 0.95:
                        break

            if best_cand and best_score >= 0.75:
                # Inherit sources from deduplicated provider cluster
                cand_sources = list(best_cand.get("sources", []))
                off_copy["sources"] = cand_sources
                off_copy["_matched_cluster_id"] = best_cand.get("id")
                # Preserve any alternate titles from the provider
                if best_cand.get("raw_titles"):
                    existing_raw = set(off_copy.get("raw_titles", []))
                    for rt in best_cand["raw_titles"]:
                        if rt not in existing_raw:
                            off_copy.setdefault("raw_titles", []).append(rt)
                claimed_cluster_ids.add(best_cand.get("id"))
            else:
                off_copy.setdefault("sources", [])

            linked_schedule.append(off_copy)

        return linked_schedule

    COMMON_TEAM_ACRONYMS: Dict[str, str] = {
        "lafc": "los angeles fc",
        "lag": "la galaxy",
        "nycfc": "new york city fc",
        "rbny": "new york red bulls",
        "psg": "paris saint germain",
        "man utd": "manchester united",
        "mufc": "manchester united",
        "man city": "manchester city",
        "mcfc": "manchester city",
        "wolves": "wolverhampton",
        "spurs": "tottenham",
        "inter": "internazionale",
        "juve": "juventus",
        "atleti": "atletico madrid",
        "atletico": "atletico madrid",
        "bvb": "borussia dortmund",
        "dortmund": "borussia dortmund",
        "bayern": "bayern monaco",
        "barca": "barcelona",
        "sporting": "sporting lisbona",
    }

    def _clean_tokens_expanded(self, text: str) -> Set[str]:
        t = (text or "").lower()
        t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("utf-8")
        t = re.sub(r"[\U0001F1E6-\U0001F1FF]{2}", "", t)
        for comp in (
            "new york", "los angeles", "san diego", "san francisco", "kansas city",
            "oklahoma city", "salt lake", "st louis", "san jose", "san antonio",
            "green bay", "tampa bay", "notre dame", "penn state", "ohio state",
            "florida state", "michigan state", "north carolina", "south carolina"
        ):
            t = t.replace(comp, comp.replace(" ", "_"))
        for acr, full in self.COMMON_TEAM_ACRONYMS.items():
            t = re.sub(rf"\b{acr}\b", full, t)
        words = set(re.sub(r"[^a-z0-9_]+", " ", t).split())
        stopwords = {
            "vs", "v", "fc", "ac", "cf", "sc", "as", "club", "u19", "u20", "u21", "u23", "at", "the",
            "calcio", "basket", "champions", "league", "cup", "coppa", "supercup", "supercoppa",
            "division", "serie", "liga", "torneo", "tournament", "simulcast", "highlight", "highlights",
            "show", "direct", "live", "conference", "europa", "uefa", "fifa", "concacaf", "conmebol",
            "afc", "caf", "ehf", "chl", "city", "de", "del", "da", "do", "el", "al"
        }
        return {w for w in words if w not in stopwords and len(w) > 1}

    def are_post_validation_duplicates(self, m1: Dict[str, Any], m2: Dict[str, Any]) -> bool:
        """
        Determines whether two matches represent the exact same real-world sporting event
        based on post-validation signals:
        1. Identical TheSportsDB official 16:9 poster URL (within 90 min) AND at least 1 matching team token.
        2. Identical normalized title (within 45 min).
        3. Proximity (within 45 min) + team tokens overlap with acronym resolution (e.g. LAFC vs Austin FC and Los Angeles FC vs Austin FC).
        Guards: Youth vs Senior matches NEVER merge, and no merge is allowed without team overlap.
        """
        d1 = m1.get("date") or 0
        d2 = m2.get("date") or 0
        if not d1 or not d2:
            return False

        time_diff = abs(d1 - d2)
        if time_diff > 90 * 60 * 1000:
            return False

        # Guard: Cross-silo matches must NEVER merge
        silo1 = self.get_canonical_silo(m1)
        silo2 = self.get_canonical_silo(m2)
        if silo1 != silo2:
            return False

        # Guard: Simulcast events (Diretta Gol, RedZone) must NEVER merge with regular 1v1 matches
        sim1 = is_simulcast_event(m1.get("title", "")) or is_simulcast_event(str(m1.get("id", "")))
        sim2 = is_simulcast_event(m2.get("title", "")) or is_simulcast_event(str(m2.get("id", "")))
        if sim1 != sim2:
            return False

        # Guard: Youth matches must NEVER merge with Senior matches
        y1 = bool(re.search(r"\b(u-?1[5-9]|u-?2[01]|under\s*1[5-9]|under\s*2[01]|primavera|youth)\b", (m1.get("title") or "") + " " + (m1.get("competition") or ""), re.IGNORECASE))
        y2 = bool(re.search(r"\b(u-?1[5-9]|u-?2[01]|under\s*1[5-9]|under\s*2[01]|primavera|youth)\b", (m2.get("title") or "") + " " + (m2.get("competition") or ""), re.IGNORECASE))
        if y1 != y2:
            return False

        # Signal 1: Identical official poster URL (within 90 min) with team token confirmation
        p1 = str(m1.get("poster") or "")
        p2 = str(m2.get("poster") or "")
        if p1 and p2 and p1 == p2 and p1.startswith("http") and "nostream" not in p1 and "no_thumb" not in p1:
            w1 = self._clean_tokens_expanded(m1.get("title", ""))
            w2 = self._clean_tokens_expanded(m2.get("title", ""))
            if w1 and w2 and (w1 & w2):
                return True

        # Signal 2: Identical normalized title (within 45 min)
        t1 = (m1.get("title") or "").strip().lower()
        t2 = (m2.get("title") or "").strip().lower()
        if t1 and t2 and t1 == t2 and time_diff <= 45 * 60 * 1000:
            return True

        # Signal 3: Time <= 45 min + team token overlap with acronym expansion
        if time_diff <= 45 * 60 * 1000:
            w1 = self._clean_tokens_expanded(m1.get("title", ""))
            w2 = self._clean_tokens_expanded(m2.get("title", ""))
            if w1 and w2:
                common = w1 & w2
                if len(common) >= 2:
                    return True

                # Check if 1 token matches and they share the same certified competition
                c1 = (m1.get("canonical_competition") or m1.get("competition") or "").lower().strip()
                c2 = (m2.get("canonical_competition") or m2.get("competition") or "").lower().strip()
                generic = {"", "other", "unknown", "altri sport", "live sports", "football", "soccer", "americhe e leghe extra-ue", "altre leghe europee"}
                if c1 and c2 and c1 not in generic and c2 not in generic and (c1 == c2 or c1 in c2 or c2 in c1):
                    if len(common) >= 1:
                        raw_t1 = (m1.get("title") or "").lower()
                        raw_t2 = (m2.get("title") or "").lower()
                        for acr, full in self.COMMON_TEAM_ACRONYMS.items():
                            if (acr in raw_t1 and full in raw_t2) or (acr in raw_t2 and full in raw_t1):
                                return True

        # Signal 4: Garbage Side Asymmetric Match for Team Sports
        # Protects against corrupt upstream provider team names (e.g. '114 vs Ipswich Town' vs 'Manchester City vs Ipswich Town')
        TEAM_SPORT_SILOS = {
            "football", "soccer", "calcio", "basket", "basketball", "hockey",
            "baseball", "football_americano", "american_football", "volley", "pallavolo", "rugby"
        }
        silo1 = self.get_canonical_silo(m1)
        silo2 = self.get_canonical_silo(m2)
        if (
            silo1 in TEAM_SPORT_SILOS and silo2 in TEAM_SPORT_SILOS
            and time_diff <= 15 * 60 * 1000
            and not is_simulcast_event(m1.get("title", ""))
            and not is_simulcast_event(m2.get("title", ""))
            and not is_simulcast_event(str(m1.get("id", "")))
            and not is_simulcast_event(str(m2.get("id", "")))
        ):
            t1_raw = (m1.get("title") or "").strip()
            t2_raw = (m2.get("title") or "").strip()

            def _get_garbage_opponent(t: str) -> Optional[str]:
                # Matches "114 vs Opponent" or "Opponent vs 114"
                m_num = re.search(r"^\d+\s+(?:vs\.?|\-|v\.?)\s+(.+)$", t, re.IGNORECASE)
                if m_num:
                    return m_num.group(1).strip()
                m_num2 = re.search(r"^(.+?)\s+(?:vs\.?|\-|v\.?)\s+\d+$", t, re.IGNORECASE)
                if m_num2:
                    return m_num2.group(1).strip()
                return None

            opp1 = _get_garbage_opponent(t1_raw)
            opp2 = _get_garbage_opponent(t2_raw)

            # Strictly asymmetric: exactly one event must have a garbage side
            if (opp1 and not opp2) or (opp2 and not opp1):
                clean_opp = (opp1 or opp2).lower()
                clean_target = (t2_raw if opp1 else t1_raw).lower()
                opp_tokens = self._clean_tokens_expanded(clean_opp)
                target_tokens = self._clean_tokens_expanded(clean_target)
                common_tokens = opp_tokens & target_tokens
                if len(common_tokens) >= 1:
                    GENERIC_CITY_WORDS = {"new", "york", "san", "diego", "los", "angeles", "state", "city", "north", "south", "fc", "real", "club", "united"}
                    substantive = [w for w in common_tokens if w not in GENERIC_CITY_WORDS and len(w) >= 4]
                    if substantive or len(common_tokens) >= 2:
                        return True

        return False

    def deduplicate_post_validation(self, matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Level 2e Post-Validation Multi-Signal Deduplicator:
        Scans all validated matches within their respective sport silos and merges duplicates
        that slipped past upstream deduplication but were certified downstream.
        Combines stream sources so all provider links are united under a single clean card.
        """
        from collections import defaultdict
        by_silo = defaultdict(list)
        for m in matches:
            silo = self.get_canonical_silo(m)
            by_silo[silo].append(m)

        all_deduped: List[Dict[str, Any]] = []
        total_merged = 0
        merged_away_ids: List[str] = []

        generic_comp = {"", "other", "unknown", "altri sport", "live sports", "football", "soccer", "americhe e leghe extra-ue", "altre leghe europee"}

        for silo, silo_matches in by_silo.items():
            deduped_silo: List[Dict[str, Any]] = []
            for m in silo_matches:
                matched_target = None
                for target in deduped_silo:
                    if self.are_post_validation_duplicates(m, target):
                        matched_target = target
                        break

                if matched_target:
                    total_merged += 1
                    m_id = m.get("id")
                    if m_id:
                        merged_away_ids.append(m_id)

                    # Merge m into matched_target
                    # 1. Prefer non-garbage, normalized or longer title
                    t_new = m.get("title") or ""
                    t_cur = matched_target.get("title") or ""
                    new_is_garbage = bool(re.search(r"^\d+\s+(?:vs\.?|\-|v\.?)\s+|\s+(?:vs\.?|\-|v\.?)\s+\d+$", t_new, re.I))
                    cur_is_garbage = bool(re.search(r"^\d+\s+(?:vs\.?|\-|v\.?)\s+|\s+(?:vs\.?|\-|v\.?)\s+\d+$", t_cur, re.I))

                    if cur_is_garbage and not new_is_garbage:
                        matched_target["title"] = t_new
                    elif not cur_is_garbage and new_is_garbage:
                        pass
                    elif m.get("_diretta_matched") and not matched_target.get("_diretta_matched"):
                        matched_target["title"] = t_new
                    elif len(t_new) > len(t_cur) and not matched_target.get("_diretta_matched"):
                        matched_target["title"] = t_new

                    # 2. Date: prefer Diretta or TSDB verified date
                    if m.get("_diretta_matched") and m.get("date"):
                        matched_target["date"] = m["date"]

                    # 3. Poster: prefer official 16:9 poster
                    p_new = str(m.get("poster") or "")
                    p_cur = str(matched_target.get("poster") or "")
                    if (not p_cur or "nostream" in p_cur) and (p_new and "nostream" not in p_new):
                        matched_target["poster"] = m["poster"]
                    elif p_new.startswith("http") and not p_cur.startswith("http"):
                        matched_target["poster"] = m["poster"]

                    # 4. Competition: prefer non-generic
                    c_new = (m.get("competition") or "").strip()
                    c_cur = (matched_target.get("competition") or "").strip()
                    if (not c_cur or c_cur.lower() in generic_comp) and (c_new and c_new.lower() not in generic_comp):
                        matched_target["competition"] = c_new
                        matched_target["_competition"] = c_new
                    if m.get("canonical_competition") and not matched_target.get("canonical_competition"):
                        matched_target["canonical_competition"] = m["canonical_competition"]
                        matched_target["_canonical_competition"] = m["canonical_competition"]

                    # 5. Metadata flags
                    if m.get("popular"):
                        matched_target["popular"] = True
                    if m.get("_diretta_matched"):
                        matched_target["_diretta_matched"] = True
                    if m.get("_tsdb_matched"):
                        matched_target["_tsdb_matched"] = True

                    if m.get("teams") and not matched_target.get("teams"):
                        matched_target["teams"] = m["teams"]
                    if m.get("_fctv33_id") and not matched_target.get("_fctv33_id"):
                        matched_target["_fctv33_id"] = m["_fctv33_id"]
                        matched_target["_sport_type"] = m.get("_sport_type", 1)

                    # 6. Combine stream sources without duplicate URLs
                    existing_urls = {
                        s.get("url") for s in matched_target.get("sources", [])
                        if isinstance(s, dict) and s.get("url")
                    }
                    merged_sources = list(matched_target.get("sources", []))
                    for s in m.get("sources", []):
                        if not isinstance(s, dict):
                            continue
                        u = s.get("url")
                        if u and u not in existing_urls:
                            existing_urls.add(u)
                            merged_sources.append(s)
                        elif not u:
                            merged_sources.append(s)
                    matched_target["sources"] = merged_sources
                else:
                    deduped_silo.append(m)

            all_deduped.extend(deduped_silo)

        if total_merged > 0:
            logger.info("Deduplicatore Post-Convalida: fusi con successo %d eventi duplicati a valle.", total_merged)

        # Clean merged away IDs from SQLite persistence
        valid_merged_ids = [mid for mid in merged_away_ids if mid]
        if valid_merged_ids:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(db_service.delete_matches_by_ids_async(valid_merged_ids))
                logger.info("Deduplicatore Post-Convalida: schedulata epurazione asincrona di %d ID duplicati assorbiti dal db.", len(valid_merged_ids))
            except RuntimeError:
                try:
                    db_service.delete_matches_by_ids(valid_merged_ids)
                    logger.info("Deduplicatore Post-Convalida: epurati dal database %d ID duplicati assorbiti.", len(valid_merged_ids))
                except Exception as ex:
                    logger.debug("Errore rimozione sync ID duplicati fusi dal db: %s", ex)
            except Exception as e:
                logger.debug("Errore rimozione ID duplicati fusi dal db: %s", e)

        return all_deduped

    @staticmethod
    def is_real_match(m: Dict[str, Any]) -> bool:
        date_ms = m.get("date") or 0
        if date_ms <= 0:
            return False
        title = (m.get("title") or "").lower()
        if m.get("_provider") != "mandrakodi" and any(w in title for w in ("schedule", "channel", "24/7", "tv shows", "big brother", "live camera")):
            return False
        m_id = str(m.get("id") or "").lower()
        if m_id.startswith("admin-") or m_id.endswith("_live"):
            return False

        # Must have at least one real playable stream source
        sources = m.get("sources") or []
        valid_sources = [
            s for s in sources
            if isinstance(s, dict)
            and str(s.get("id", "")).strip() not in ("0", "00", "")
            and "channel not listed" not in (s.get("name") or "").lower()
            and "just ask in chat" not in (s.get("name") or "").lower()
        ]
        if not valid_sources:
            return False

        return True

    @staticmethod
    def is_replay_eligible(match: Dict[str, Any]) -> bool:
        """
        Determines whether a concluded event should be kept in the replay/highlights catalog.
        Strictly keeps only top-tier and Italian events where official highlights/replays exist.
        Excludes all US college/NCAA sports, minor baseball/hockey, and niche sports.
        """
        cat = match.get("_catalog") or ""
        genre = match.get("_genre") or ""
        title = (match.get("title") or "").lower()

        # Immediate rejection of all US college / NCAA sports across any category
        if any(w in title for w in ("ncaa", "college", "cws", "cfb", "march madness")):
            return False
        if any(w in genre.lower() for w in ("ncaa", "college")):
            return False

        # 1. Calcio Italiano: All top and professional divisions
        if cat == "calcio_italiano":
            if genre == "Nazionali e Amichevoli":
                teams = match.get("teams") or {}
                home = extract_team_name(teams.get("home")).lower() if isinstance(teams, dict) else ""
                away = extract_team_name(teams.get("away")).lower() if isinstance(teams, dict) else ""
                combined_check = f"{title} {home} {away}"
                return bool(re.search(r"\b(italia|italy|azzurr[ie])\b", combined_check))
            return True

        # 2. Calcio Internazionale: Top European leagues, UEFA cups, and National team tournaments
        if cat == "calcio_estero":
            if genre in ("Nazionali e Amichevoli", "Europei Under 21 e Nazionali Giovanili"):
                teams = match.get("teams") or {}
                home = extract_team_name(teams.get("home")).lower() if isinstance(teams, dict) else ""
                away = extract_team_name(teams.get("away")).lower() if isinstance(teams, dict) else ""
                combined_check = f"{title} {home} {away}"
                return bool(re.search(r"\b(italia|italy|azzurr[ie])\b", combined_check))

            allowed_genres = {
                "Champions League",
                "Europa e Conference League",
                "Premier League",
                "La Liga",
                "Bundesliga e Ligue 1",
            }
            return genre in allowed_genres

        # 3. Motori: Formula 1, MotoGP, Superbike
        if cat == "motori":
            if genre in ("Formula 1", "MotoGP e Moto2/3", "Superbike"):
                return True
            if any(w in title for w in ("formula 1", "f1", "motogp", "superbike")):
                return True
            return False

        # 4. Tennis: Grand Slams, ATP Tour, WTA Tour, Davis Cup
        if cat == "tennis":
            return genre in ("Grandi Slam", "ATP", "WTA", "Coppa Davis e BJK Cup")

        # 5. Basket: EuroLeague, NBA, Italian Serie A (LBA), FIBA tournaments
        if cat == "basket":
            allowed_genres = {
                "NBA",
                "Eurolega ed Eurocup",
                "LBA Serie A",
                "FIBA e Tornei Nazionali",
            }
            return genre in allowed_genres

        # 6. Sport da Combattimento: UFC and major Boxing
        if cat == "combattimento":
            if genre in ("UFC", "Boxe"):
                return True
            return any(w in title for w in ("ufc", "boxing", "championship"))

        # 7. Football Americano: NFL only
        if cat == "football_americano":
            return genre == "NFL" or "nfl" in title

        # All other categories (baseball, hockey, altri_sport) have NO replays
        return False

    async def sync_matches(self, force: bool = False) -> None:
        """
        Synchronizes matches from upstream providers and SQLite database in the background.
        Runs deduplication, persists to DB, and pre-classifies all events into RAM cache.
        """
        async with self._sync_lock:
            now = time.time()
            interval = self.get_current_sync_interval()
            if not force and self._cached_matches and (now - self._last_sync_time < interval):
                return

            logger.info("Starting background sports schedule sync...")
            try:
                # 1. Fetch live matches from StreamedAPI, DaddyLiveAPI, FCTV33, Italian EPG (Sky & Tivùsat), and official registries concurrently
                tasks = [
                    streamed_api.get_all_matches(force_refresh=True),
                    daddylive_api.get_matches(force=True),
                    ocblacktop_service.get_official_sessions(),
                    thesportsdb_service.fetch_multi_day_calendar(days_ahead=2, days_behind=1),
                    italian_epg_service.get_matches(),
                    diretta_service.get_official_sliding_schedule(cutoff_hours_behind=6, hours_ahead=48, force=True),
                    fctv33_service.get_matches(force=True),
                    mandrakodi_service.get_matches(force=True),
                    spon_service.get_matches(force=True),
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                streamed_raw = results[0] if isinstance(results[0], list) else []
                daddylive_raw = results[1] if isinstance(results[1], list) else []
                ocb_sessions = results[2] if isinstance(results[2], list) else []
                epg_raw = results[4] if isinstance(results[4], list) else []
                diretta_events = results[5] if isinstance(results[5], list) else []
                official_schedule = diretta_events
                fctv33_raw = results[6] if isinstance(results[6], list) else []
                mandrakodi_raw = results[7] if isinstance(results[7], list) else []
                spon_raw = results[8] if isinstance(results[8], list) else []

                streamed_matches = [m for m in streamed_raw if self.is_real_match(m)]
                daddylive_matches = [m for m in daddylive_raw if self.is_real_match(m)]
                epg_matches = [m for m in epg_raw if self.is_real_match(m)]
                fctv33_matches = [m for m in fctv33_raw if self.is_real_match(m)]
                mandrakodi_matches = [m for m in mandrakodi_raw if self.is_real_match(m)]
                spon_matches = [m for m in spon_raw if self.is_real_match(m)]

                # Merge streamed and daddylive matches silo-by-silo into combined fresh
                combined_fresh = self.merge_and_deduplicate_by_silos(streamed_matches, daddylive_matches)
                if fctv33_matches:
                    combined_fresh = self.merge_and_deduplicate_by_silos(combined_fresh, fctv33_matches)
                if mandrakodi_matches:
                    combined_fresh = self.merge_and_deduplicate_by_silos(combined_fresh, mandrakodi_matches)
                if spon_matches:
                    combined_fresh = self.merge_and_deduplicate_by_silos(combined_fresh, spon_matches)

                # Ingest certified multicourt tournament sessions and simulcasts from Italian EPG (Sky/Tivùsat)
                epg_certified_sessions = [
                    m for m in epg_matches
                    if (
                        (m.get("_silo") == "tennis" or m.get("category") == "tennis")
                        and not bool(isinstance(m.get("teams"), dict) and m["teams"].get("home") and m["teams"].get("away"))
                        and any(k in (m.get("title") or "").lower() for k in ("live", "wta", "atp", "masters", "slam"))
                    )
                    or (
                        (m.get("_silo") == "golf" or m.get("category") in ("golf", "altri_sport"))
                        and not bool(isinstance(m.get("teams"), dict) and m["teams"].get("home") and m["teams"].get("away"))
                    )
                    or is_simulcast_event(m.get("title", ""))
                ]
                if epg_certified_sessions:
                    combined_fresh = self.merge_and_deduplicate_by_silos(combined_fresh, epg_certified_sessions)

                # Pull all active matches from DB to retain existing enriched posters
                all_db_matches = [m for m in await db_service.get_active_matches_async() if self.is_real_match(m)]
                db_posters = {}
                db_posters_by_teams = {}
                db_posters_by_tokens = {}
                for m in all_db_matches:
                    p = m.get("poster")
                    if not p or "nostream" in str(p):
                        continue
                    if m.get("id"):
                        db_posters[m["id"]] = p
                    tk = self._get_teams_key(m)
                    if tk:
                        db_posters_by_teams[tk] = p
                    tok = self._clean_tokens(m.get("title", ""))
                    if tok:
                        db_posters_by_tokens[tok] = p

                # 2. TOP-DOWN INVERTED LINKING:
                # Official Diretta/Flashscore schedule is primary.
                # It links to deduplicated provider clusters in combined_fresh.
                if official_schedule:
                    linked_schedule = self.link_official_schedule_to_provider_clusters(official_schedule, combined_fresh)
                    if epg_certified_sessions:
                        linked_schedule.extend(epg_certified_sessions)
                    all_matches = linked_schedule
                    logger.info("Top-Down Pipeline: linked %d official matches to provider stream clusters.", len(all_matches))
                else:
                    logger.warning("Diretta sliding schedule unavailable; falling back to provider bottom-up pipeline.")
                    fresh_ids = {m.get("id") for m in combined_fresh if m.get("id")}
                    all_db_matches = [
                        m for m in all_db_matches
                        if m.get("id") not in fresh_ids and not str(m.get("id", "")).startswith("mandrakodi:")
                    ]
                    all_matches = self.merge_and_deduplicate_by_silos(combined_fresh, all_db_matches) if all_db_matches else combined_fresh
                    if diretta_service.is_healthy:
                        diretta_service.reconcile_matches(all_matches)
                    else:
                        espn_events = await espn_service.get_official_events()
                        if espn_events:
                            espn_service.reconcile_matches(all_matches, espn_events, self._clean_tokens, self._get_teams_key)

                # Reconcile motorsport events against official Orange Cat Blacktop session registry
                if ocb_sessions:
                    ocblacktop_service.reconcile_matches(all_matches, ocb_sessions)

                # Transfer existing DB posters to fresh matches if missing
                for m in all_matches:
                    if m.get("poster") and "nostream" not in str(m.get("poster")):
                        continue
                    m_id = m.get("id")
                    if m_id and m_id in db_posters:
                        m["poster"] = db_posters[m_id]
                        continue
                    tk = self._get_teams_key(m)
                    if tk and tk in db_posters_by_teams:
                        m["poster"] = db_posters_by_teams[tk]
                        continue
                    tok = self._clean_tokens(m.get("title", ""))
                    if tok and tok in db_posters_by_tokens:
                        m["poster"] = db_posters_by_tokens[tok]
                        continue
                    if m.get("alt_teams"):
                        for at in m["alt_teams"]:
                            if isinstance(at, dict):
                                at_k = self._get_teams_key({"teams": at})
                                if at_k and at_k in db_posters_by_teams:
                                    m["poster"] = db_posters_by_teams[at_k]
                                    break

                # 4. FASE 2: ARRICCHIMENTO FLUSSI TV (TvVoo, Mandrakodi & EPG Sky/Tivùsat)
                # Enrich any match (from DaddyLive, Streamed, etc.) with TvVoo FHD streams if matching recognized broadcasters
                tvvoo_service.enrich_matches_with_tvvoo(all_matches)

                # Enrich any match with Mandrakodi linear channels (ClearKey 1080p 50fps and AceStream)
                mandrakodi_service.enrich_matches_with_mandrakodi(all_matches)

                # Enrich confirmed real matches with official Italian Sky & Tivùsat TvVoo channels from Italian EPG
                # Now benefits from certified kickoff times and _diretta_tv_channels!
                await italian_epg_service.enrich_matches_with_epg_channels(all_matches)

                # Enrich again with Mandrakodi in case EPG added new Italian Sky/Tivùsat channels
                mandrakodi_service.enrich_matches_with_mandrakodi(all_matches)

                # Purge uncertified or duplicate provider multicourt / aggregated tennis events:
                # Standalone multicourt / aggregated tournament session cards in tennis are strictly
                # reserved for the official Sky/Tivùsat EPG catalog items. External streaming providers
                # (DaddyLive, Streamed, Mandrakodi) can only supply sources to the official Sky session.
                all_matches = [
                    m for m in all_matches
                    if not (
                        (m.get("_silo") == "tennis" or m.get("category") == "tennis")
                        and not bool(isinstance(m.get("teams"), dict) and m["teams"].get("home") and m["teams"].get("away"))
                        and m.get("_provider") != "italian_epg"
                    )
                ]

                # Purge uncertified standalone Mandrakodi events:
                # Dynamic/last-minute matches originating strictly from Mandrakodi that did NOT
                # find validation in the official Diretta.it / LiveSport schedule are silenced (preventing ghost/replay cards).
                all_matches = [
                    m for m in all_matches
                    if not (
                        str(m.get("id", "")).startswith("mandrakodi:")
                        and not m.get("_diretta_matched")
                        and not m.get("_espn_matched")
                    )
                ]

                # Purge palpably conflicting Italian TV channels (aligned with asymmetric window)
                italian_epg_service.filter_conflicting_italian_channels(all_matches)

                # 5. FASE 3: ASSEGNAZIONE LOCANDINE 16:9 & CALENDARIO THESPORTSDB
                # Reconcile against official TheSportsDB Calendar registry
                # Dual-test evaluates both raw provider titles and Diretta.it normalized team names!
                thesportsdb_service.reconcile_matches(all_matches)

                # Reconcile motorsport events against TheSportsDB in-RAM calendar (with canonized OCB session data)
                thesportsdb_service.reconcile_motorsport_matches(all_matches)

                # 6. FASE 4: DEDUPLICAZIONE POST-VALIDAZIONE & PERSISTENZA
                # Purge matches older than retention window from DB (6h if ENABLE_REPLAYS=False, 72h if True)
                await db_service.purge_expired_matches_async()
                banner_service.purge_stale_banners(max_age_hours=24)

                # LEVEL 2e: Post-Validation Multi-Signal Deduplication (in RAM and cleans absorbed IDs from DB)
                all_matches = self.deduplicate_post_validation(all_matches)

                # Persist ONLY clean, deduplicated matches into SQLite!
                if all_matches:
                    await db_service.save_matches_async(all_matches)

                # 5. LEVEL 3: Pre-classify every match into catalog and genre
                # Matches with _tsdb_matched have competition set, enabling exact genre matching;
                # Unmatched matches use the 5-layer heuristic fallback.
                for m in all_matches:
                    if not m.get("_espn_matched") and not m.get("_ocb_matched"):
                        cat, genre = genre_classifier.classify(m)
                        # Strict rule: only officially verified events can enter Formula 1 or MotoGP e Superbike
                        if cat == "motori" and genre in ("Formula 1", "MotoGP e Superbike"):
                            genre = "NASCAR e IndyCar"
                        m["_catalog"] = cat
                        m["_genre"] = genre


                # 6. Replay Whitelist & Concluded Purge Filter:
                # If ENABLE_REPLAYS=False, purge ALL concluded matches immediately from DB and memory!
                # If ENABLE_REPLAYS=True, purge only non-eligible replays (retain Serie A, F1, NBA, etc. for 72h).
                now_ms = time.time() * 1000
                ids_to_purge = []
                max_future_ms = now_ms + (3 * 24 * 3600 * 1000)
                filtered_matches = []
                for m in all_matches:
                    d = m.get("date") or 0
                    if d > 0 and d > max_future_ms:
                        continue
                    live_window = self.get_live_window_minutes(m)
                    is_concluded = (d > 0) and ((d - now_ms) / 60000 < -live_window)
                    if is_concluded:
                        if not ENABLE_REPLAYS:
                            m_id = m.get("id")
                            if m_id:
                                ids_to_purge.append(m_id)
                        elif self.is_replay_eligible(m):
                            filtered_matches.append(m)
                        else:
                            m_id = m.get("id")
                            if m_id:
                                ids_to_purge.append(m_id)
                    else:
                        filtered_matches.append(m)

                if ids_to_purge:
                    await db_service.delete_matches_by_ids_async(ids_to_purge)
                    logger.info("Purged %d concluded events from SQLite (ENABLE_REPLAYS=%s).", len(ids_to_purge), ENABLE_REPLAYS)

                all_matches = filtered_matches

                # Clean up cached posters and athlete cutouts of concluded events
                try:
                    tennis_poster_service.cleanup_finished_events(all_matches)
                except Exception as e:
                    logger.debug("Tennis cache cleanup error: %s", e)

                # Build indexed dictionary for instant O(1) lookups
                index_by_id: Dict[str, Dict[str, Any]] = {}
                for m in all_matches:
                    mid = str(m.get("id", ""))
                    if mid:
                        index_by_id[mid] = m
                        if mid.startswith("streamsport:"):
                            index_by_id[mid.split(":", 1)[1]] = m

                self._matches_by_id = index_by_id
                self._cached_matches = list(all_matches)
                self._last_sync_time = time.time()
                logger.info("Background sports sync complete. %d active events indexed in RAM.", len(all_matches))

                # 7. Dynamic 16:9 poster generation for Tennis events lacking artwork
                tennis_poster_service.start_background_enrichment(all_matches)

                # 8. Level 2 TheSportsDB semantic search fallback for orphan sports events
                thesportsdb_service.start_background_fallback_search(all_matches)
            except Exception as e:
                logger.error("Error during background sports schedule sync: %s", e, exc_info=True)

    def start_background_sync(self):
        """Starts the periodic background sync task with watchdog supervision."""
        if self._is_running:
            return
        self._is_running = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        self._background_task = loop.create_task(self._background_loop())
        self._background_task.add_done_callback(self._on_background_task_done)
        logger.info("CatalogService background sync scheduled every %d seconds with watchdog.", CATALOG_SYNC_INTERVAL)

    def stop_background_sync(self):
        """Stops the periodic background sync task."""
        self._is_running = False
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()
            logger.info("CatalogService background sync stopped.")

    def _on_background_task_done(self, task: asyncio.Task):
        """Supervises the background sync task, catching unexpected failures and auto-restarting."""
        if not self._is_running:
            return

        # Check if cancelled or failed with exception
        exc = None
        if not task.cancelled():
            exc = task.exception()

        if exc:
            logger.critical("CatalogService background sync task died with unhandled exception: %s", exc, exc_info=exc)
        elif task.cancelled():
            logger.warning("CatalogService background sync task was cancelled while _is_running=True.")
        else:
            logger.warning("CatalogService background sync task terminated unexpectedly while _is_running=True.")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
            except Exception:
                loop = None

        if loop and loop.is_running() and self._is_running:
            logger.info("Watchdog: Scheduling auto-restart of CatalogService background sync in 10s...")
            loop.call_later(10.0, self._restart_background_sync, loop)

    def _restart_background_sync(self, loop: asyncio.AbstractEventLoop):
        """Restarts the background loop if the service is still active."""
        if not self._is_running:
            return
        logger.info("Watchdog: Restarting CatalogService background sync task...")
        self._background_task = loop.create_task(self._background_loop())
        self._background_task.add_done_callback(self._on_background_task_done)

    async def _background_loop(self):
        """Periodic background loop with dynamic interval (15m peak 12-22, 60m off-peak) and reset support."""
        while self._is_running:
            try:
                await self.sync_matches()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Unexpected error in background sync loop: %s", e)

            # Wait dynamically, waking up if reset_sync_timer is called
            while self._is_running:
                interval = self.get_current_sync_interval()
                elapsed = time.time() - self._last_sync_time
                remaining = interval - elapsed
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(self._sync_reset_event.wait(), timeout=min(remaining, 15.0))
                    self._sync_reset_event.clear()
                except asyncio.TimeoutError:
                    pass
                except asyncio.CancelledError:
                    return

    async def ensure_synced(self):
        """Ensures that at least one sync has completed before serving requests."""
        if not self._cached_matches:
            if "unittest" in sys.modules or os.environ.get("STREAMSPORT_TESTING") == "1":
                return
            await self.sync_matches()

    async def get_catalog(
        self,
        catalog_id: Optional[str] = None,
        genre_filter: Optional[str] = None,
        search_query: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
        user_tz: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns catalog items filtered by catalog, genre, and optional search query.
        Served instantly from RAM cache with real-time countdown minutes and localization.
        """
        if not self._cached_matches:
            await self.ensure_synced()

        matches_to_use = self._cached_matches

        # Classify and filter by catalog and genre
        now_ms = time.time() * 1000
        filtered: List[Dict[str, Any]] = []
        target_genre = (genre_filter or "").strip()
        is_all_genre = not target_genre or target_genre in (
            "Tutti gli Eventi", "all", "All", "All Sports", "Tutti i Generi", "Tutti", "Tutti i generi"
        )
        filter_catalog = catalog_id if catalog_id and catalog_id not in ("streamsport_events", "all", "All") else None

        def norm_g(s: str) -> str:
            return s.lower().replace("&", "e").replace("  ", " ").strip()

        norm_target = norm_g(target_genre)

        for m in matches_to_use:
            if not self.is_real_match(m):
                continue
            item_catalog = m.get("_catalog")
            item_genre = m.get("_genre")
            if not item_catalog or not item_genre:
                item_catalog, item_genre = genre_classifier.classify(m)
                if item_catalog == "motori" and item_genre in ("Formula 1", "MotoGP e Superbike") and not m.get("_ocb_matched") and not m.get("_espn_matched"):
                    item_genre = "NASCAR e IndyCar"
                m["_catalog"] = item_catalog
                m["_genre"] = item_genre


            # Concluded match filter: only keep replay-eligible matches (or discard if replays disabled)
            d = m.get("date") or 0
            live_window = self.get_live_window_minutes(m)
            is_concluded = (d > 0) and ((d - now_ms) / 60000 < -live_window)
            if is_concluded:
                if not ENABLE_REPLAYS:
                    continue
                if not self.is_replay_eligible(m):
                    continue

            if filter_catalog and item_catalog != filter_catalog:
                continue

            if not is_all_genre:
                norm_item = norm_g(item_genre)
                legacy_target = {"americhe e altre leghe": "americhe e leghe extra-ue"}.get(norm_target, norm_target)
                if norm_item != norm_target and norm_item != legacy_target:
                    continue

            # Search filter
            if search_query:
                q = search_query.lower()
                m_title = (m.get("title") or "").lower()
                teams = m.get("teams") or {}
                home = extract_team_name(teams.get("home")).lower() if isinstance(teams, dict) else ""
                away = extract_team_name(teams.get("away")).lower() if isinstance(teams, dict) else ""
                if q not in m_title and q not in home and q not in away:
                    continue

            meta_item = self.build_meta_item(m, item_genre, user_tz=user_tz, base_url=base_url)
            filtered.append(meta_item)

        # 5. Sort matches chronologically:
        # Priority 0: LIVE matches now (diff <= 0 and diff >= -live_window) -> ASCENDING DATE (12:00 before 12:15)
        # Priority 1: IMMINENT & UPCOMING (diff > 0) -> ASCENDING DATE (closest to start first)
        # Priority 2: CONCLUDED REPLAYS (diff < -live_window) -> STAY AT THE BOTTOM (most recently finished first)
        # Priority 3: No date -> AT THE VERY END
        now_ms = time.time() * 1000

        def sort_priority(item: Dict[str, Any]) -> Tuple[int, float]:
            d = item.get("_date_ms") or 0
            if not d:
                return (3, 0.0)
            diff = (d - now_ms) / 60000
            live_window = item.get("_live_window", 240)
            if -live_window <= diff <= 0:
                return (0, float(d))   # Live now: chronological order (earliest kickoff first)
            elif diff > 0:
                return (1, float(d))   # Upcoming: chronological order (closest to start first)
            else:
                return (2, -float(d))  # Concluded: stay at the bottom of the list

        filtered.sort(key=sort_priority)

        # 6. Safety Deduplication: Guarantee 100% unique IDs for Android Jetpack Compose
        seen_ids = set()
        unique_filtered = []
        for meta_item in filtered:
            mid = meta_item.get("id")
            if mid and mid in seen_ids:
                continue
            seen_ids.add(mid)
            unique_filtered.append(meta_item)
        filtered = unique_filtered

        # 7. Pagination & Schema Cleanup (Row limit: max 4 items on first page for Nuvio/Android TV)
        effective_limit = 4 if (skip == 0 and not search_query) else limit
        page = filtered[skip : skip + effective_limit]
        for item in page:
            item.pop("_date_ms", None)
            item.pop("_live_window", None)

        return page

    async def get_meta_detail(
        self,
        slug_id: str,
        user_tz: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves full meta details for a single match by slug or ID.
        """
        clean_id = slug_id.split(":", 1)[1] if ":" in slug_id else slug_id

        # 1. Check in-memory cached matches first (instant O(1) indexed lookup!)
        match = self.get_cached_match(clean_id)

        # 1. Check Streamed
        if not match:
            match = await streamed_api.find_match_by_slug_and_id(clean_id)

        # 2. Check DaddyLive
        if not match:
            dl_matches = await daddylive_api.get_matches()
            delimiters = ("-", ":", "_")
            for m in dl_matches:
                mid = str(m.get("id", ""))
                if mid == clean_id or any(mid.endswith(f"{d}{clean_id}") for d in delimiters):
                    match = m
                    break

        # 3. Check SQLite DB
        if not match:
            try:
                match = await db_service.get_match_by_id_async(clean_id)
            except Exception as e:
                logger.warning("DB lookup failed for %s: %s", clean_id, e)

        if not match:
            return None

        genre = match.get("_genre")
        if not genre:
            _, genre = genre_classifier.classify(match)
        meta = self.build_meta_item(match, genre, user_tz=user_tz, base_url=base_url)
        meta["background"] = meta.get("poster")
        return meta

    def get_cached_matches(self) -> List[Dict[str, Any]]:
        """Returns an immutable snapshot copy of cached matches."""
        return list(self._cached_matches)

    def get_cached_match(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a match from the in-memory cache by ID with O(1) index and fallback."""
        if not match_id:
            return None
        clean_id = match_id.replace("streamsport:", "")
        # 1. Fast O(1) dictionary index lookup
        m = self._matches_by_id.get(match_id) or self._matches_by_id.get(clean_id)
        if m:
            return m
        # 2. Snapshot iteration fallback (exact match or delimited suffix; prevents false positive substring collisions)
        snapshot = self._cached_matches
        delimiters = ("-", ":", "_")
        for item in snapshot:
            mid = str(item.get("id", ""))
            if mid == clean_id or mid == match_id or any(mid.endswith(f"{d}{clean_id}") for d in delimiters):
                return item
        return None


catalog_service = CatalogService()
