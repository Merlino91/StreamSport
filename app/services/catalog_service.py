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
    CATALOG_SYNC_INTERVAL,
    CATALOG_TYPE,
    ENABLE_REPLAYS,
    NUVIO_CATALOG_LOOKUP,
    TIMEZONE,
)
from app.services.daddylive_api import daddylive_api
from app.services.db_service import db_service
from app.services.genre_classifier import genre_classifier
from app.services.ocblacktop_service import ocblacktop_service
from app.services.streamed_api import streamed_api
from app.services.banner_service import banner_service, BANNER_VERSION
from app.services.tennis_poster_service import tennis_poster_service, TENNIS_POSTER_VERSION
from app.services.thesportsdb_service import thesportsdb_service
from app.services.tvvoo_service import tvvoo_service
from app.services.fctv33_service import fctv33_service
from app.services.mandrakodi_service import mandrakodi_service
from app.services.spon_service import spon_service
from app.services.streamfree_service import streamfree_service
from app.services.italian_epg_service import italian_epg_service
from app.services.diretta_service import diretta_service
from app.services.provider_normalizers import (
    clean_daddylive_event,
    clean_fctv33_event,
    clean_streamed_event,
    clean_mandrakodi_event,
    clean_spon_event,
    clean_streamfree_event,
)
from app.services.team_matching import extract_team_name, clean_team_name
from app.services.canonical_competition_service import (
    get_canonical_display_name,
    is_simulcast_event,
    get_simulcast_info,
)



try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

DEFAULT_TIMEZONE = TIMEZONE

def get_safe_timezone(user_tz: Optional[str] = None) -> datetime.tzinfo:
    """Safely resolves timezone from user string, gracefully falling back to Europe/Rome or UTC."""
    target = (user_tz or DEFAULT_TIMEZONE).strip()
    if target.lower() in ("nuvio", "stremio", "invalid", "unknown", "none", ""):
        target = DEFAULT_TIMEZONE
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
        try:
            return pytz.timezone(target)
        except Exception:
            return pytz.timezone(DEFAULT_TIMEZONE)
    except Exception:
        pass
    return datetime.timezone(datetime.timedelta(hours=1))


logger = logging.getLogger("streamsport.catalog")

class CatalogService:
    """
    Builds Stremio Catalog and Meta objects for StreamSport with granular genre filtering.
    """

    # Class-level immutable sets and precompiled regexes to avoid re-allocation in loops
    _EXCLUDED_NOISE_WORDS = frozenset({
        "vs", "the", "fc", "ac", "cf", "sc", "as", "at", "live", "stream", "bc",
        "pallacanestro", "pallacan", "baloncesto", "basquet", "basket", "calcio", "volley", "pallavolo", "club"
    })
    _EXCLUDED_CLUB_TERMS = frozenset({
        "fc", "ac", "cf", "sc", "as", "the", "bc",
        "pallacanestro", "pallacan", "baloncesto", "basquet", "basket", "calcio", "volley", "pallavolo", "club"
    })
    _REPLAY_ELIGIBLE_CALCIO_ESTERO = frozenset({
        "Champions League",
        "Champions League Femminile",
        "Europa e Conference League",
        "Premier League",
        "La Liga",
        "Bundesliga e Ligue 1",
    })
    _REPLAY_ELIGIBLE_BASKET = frozenset({
        "NBA",
        "Eurolega ed Eurocup",
        "LBA Serie A",
        "FIBA e Tornei Nazionali",
    })

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
        Dynamic sync interval based on Rome time, scaled by CATALOG_SYNC_INTERVAL:
        - 12:00 to 23:59: CATALOG_SYNC_INTERVAL (default 3600 seconds) for afternoon/evening sports coverage.
        - 00:00 to 11:59: max(CATALOG_SYNC_INTERVAL * 2, 14400) for overnight/morning (or CATALOG_SYNC_INTERVAL * 2 if custom).
        """
        tz = get_safe_timezone("Europe/Rome")
        now_rome = datetime.datetime.now(tz)
        base = max(CATALOG_SYNC_INTERVAL, 300)
        if 12 <= now_rome.hour <= 23:
            return base
        return max(base * 2, 14400) if base == 3600 else base * 2

    def reset_sync_timer(self) -> None:
        """Resets the background catalog sync timer to postpone the next scheduled sync by a full interval."""
        self._last_sync_time = time.time()
        if hasattr(self, "_sync_reset_event"):
            self._sync_reset_event.set()

    def reconcile_mandrakodi_live_matches(self, mandrakodi_matches: List[Dict[str, Any]]) -> None:
        """
        Attaches newly discovered Mandrakodi live/OTT streams to certified matches in memory
        and resets the background sync timer.
        """
        if not mandrakodi_matches:
            return
        from app.services.team_matching import calculate_match_score

        clean_mk = [clean_mandrakodi_event(m) for m in mandrakodi_matches]
        if self._cached_matches:
            claimed = set()
            for off in self._cached_matches:
                off_date = off.get("date") or 0
                for cand in clean_mk:
                    if cand.get("id") in claimed:
                        continue
                    if abs((cand.get("date") or 0) - off_date) <= 60 * 60 * 1000:
                        score_res = calculate_match_score(
                            cand,
                            off,
                            phase="phase1_diretta",
                            max_time_diff_min=60.0
                        )
                        if score_res.get("matched") and score_res.get("score", 0) >= 0.75:
                            existing_src_ids = {str(s.get("id")) for s in off.get("sources", []) if isinstance(s, dict)}
                            for src in cand.get("sources", []):
                                if isinstance(src, dict) and str(src.get("id")) not in existing_src_ids:
                                    existing_src_ids.add(str(src.get("id")))
                                    off.setdefault("sources", []).append(src)
                            claimed.add(cand.get("id"))
                            break
            # Standalone solo matches certified by Diretta are also admitted
            for m in clean_mk:
                if m.get("_diretta_matched") and m.get("id") not in claimed:
                    self._cached_matches.append(m)
        else:
            self._cached_matches = [
                m for m in clean_mk
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
        EPG / Linear broadcast channels respect their exact scheduled broadcast duration
        (Sequential Channel Handover).
        Standard silo fallback durations apply when no linear end date is provided.
        """
        # EPG / Linear broadcast channels: respect actual scheduled broadcast duration
        d_start = match.get("date", 0)
        d_end = match.get("end_date", 0)
        if d_start and d_end and d_end > d_start:
            delta_mins = int((d_end - d_start) / 60000)
            if delta_mins > 0:
                if match.get("_provider") == "italian_epg" or str(match.get("id", "")).startswith(("sky_", "tivusat_")):
                    return max(15, delta_mins + 10)

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
        display_title = match.get("title") or match.get("display_title_it") or match.get("_italian_title") or "Live Sports"
        date_ms = match.get("date", 0)
        is_popular = match.get("popular", False)

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
        raw_mid = str(match_id or "unknown")
        clean_match_id = raw_mid.split(":", 1)[1] if raw_mid.startswith("streamsport:") else raw_mid
        stremio_id = f"streamsport:{clean_match_id}"

        has_tvvoo = any(
            isinstance(s, dict) and s.get("source") == "tvvoo"
            for s in match.get("sources", [])
        )

        poster_endpoint = f"/poster/{clean_match_id}.jpg?s={banner_status}"
        if banner_time:
            poster_endpoint += f"&t={urllib.parse.quote(banner_time)}"
        if has_tvvoo:
            poster_endpoint += "&tv=1"
        is_tennis = (
            "tennis" in str(match.get("category") or "").lower()
            or "tennis" in str(match.get("_catalog") or "").lower()
            or "tennis" in str(match.get("_silo") or "").lower()
        )
        ver_tag = f"{BANNER_VERSION}_{TENNIS_POSTER_VERSION}" if is_tennis else str(BANNER_VERSION)
        poster_endpoint += f"&v={ver_tag}"

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
        if ":" in t:
            after_colon = t.split(":", 1)[1]
            if re.search(r"\bvs\b|\s+-\s+", after_colon):
                t = after_colon
            else:
                t = t.replace(":", " ")
        t = re.sub(r"\([^)]*\)", "", t)
        t = re.sub(r"^(?:italy|england|spain|germany|france|uefa|fifa|brazil|usa|[a-z]+)\s*-\s*", "", t)
        cleaned_t = clean_team_name(t)
        words = sorted([w for w in cleaned_t.split() if w not in self._EXCLUDED_NOISE_WORDS and len(w) > 1])
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
            h_words = frozenset([w for w in h.split() if w not in self._EXCLUDED_CLUB_TERMS and len(w) > 1])
            a_words = frozenset([w for w in a.split() if w not in self._EXCLUDED_CLUB_TERMS and len(w) > 1])
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

    def link_official_schedule_to_providers(
        self,
        official_schedule: List[Dict[str, Any]],
        providers: Dict[str, List[Dict[str, Any]]],
        time_window_minutes: float = 60.0,
    ) -> List[Dict[str, Any]]:
        """
        Direct Top-Down Linking: Official Diretta/Flashscore entries directly query each
        provider's candidate list. For each provider, the candidate with the HIGHEST score
        (>= 0.75) within the sport silo and time window (default 60m) is selected and claimed.
        """
        from collections import defaultdict
        from app.services.team_matching import calculate_match_score

        # Pre-index provider candidates by silo for fast lookups
        indexed_providers: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for prov_name, match_list in providers.items():
            by_silo = defaultdict(list)
            for m in match_list:
                silo = self.get_canonical_silo(m)
                by_silo[silo].append(m)
            indexed_providers[prov_name] = by_silo

        claimed_ids: Dict[str, Set[str]] = {p: set() for p in providers.keys()}
        linked_schedule: List[Dict[str, Any]] = []

        for off in official_schedule:
            off_copy = dict(off)
            off_copy["sources"] = list(off.get("sources") or [])
            off_silo = off_copy.get("_silo") or self.get_canonical_silo(off_copy)
            off_date = off_copy.get("date") or 0

            # Prioritize Flashscore.com English metadata as primary for matching against providers
            off_copy["clean_home"] = off_copy.get("alt_home") or off_copy.get("home") or ""
            off_copy["clean_away"] = off_copy.get("alt_away") or off_copy.get("away") or ""

            # Query EACH provider independently (strictly within the same canonical sport silo)
            for prov_name, silo_map in indexed_providers.items():
                candidates = silo_map.get(off_silo, [])

                time_filtered = [
                    c for c in candidates
                    if c.get("id") not in claimed_ids[prov_name]
                    and (
                        not off_date
                        or not c.get("date")
                        or abs((c.get("date") or 0) - off_date) <= time_window_minutes * 60 * 1000
                    )
                ]

                best_cand = None
                best_score = 0.0

                for cand in time_filtered:
                    score_res = calculate_match_score(
                        cand,
                        off_copy,
                        phase="phase1_diretta",
                        max_time_diff_min=time_window_minutes,
                    )
                    score = score_res.get("score", 0.0)
                    if score_res.get("matched") and score > best_score:
                        best_score = score
                        best_cand = cand
                        if best_score >= 0.98:
                            break  # Optimal exact match

                if best_cand and best_score >= 0.75:
                    # Inherit all stream sources from this provider's winning event
                    existing_src_ids = {str(s.get("id")) for s in off_copy["sources"] if isinstance(s, dict)}
                    for src in best_cand.get("sources", []):
                        if isinstance(src, dict) and str(src.get("id")) not in existing_src_ids:
                            existing_src_ids.add(str(src.get("id")))
                            off_copy["sources"].append(src)

                    claimed_ids[prov_name].add(best_cand.get("id"))
                    off_copy["_matched_cluster_id"] = best_cand.get("id")
                    if best_cand.get("_fctv33_id"):
                        off_copy["_fctv33_id"] = best_cand["_fctv33_id"]
                    if best_cand.get("_sport_type"):
                        off_copy["_sport_type"] = best_cand["_sport_type"]

            linked_schedule.append(off_copy)

        return linked_schedule

    @classmethod
    def is_real_match(cls, m: Dict[str, Any]) -> bool:
        date_ms = m.get("date") or 0
        if date_ms <= 0:
            return False
        title = (m.get("title") or "").lower()
        if m.get("_provider") != "mandrakodi" and any(w in title for w in ("schedule", "channel", "24/7", "tv shows", "big brother", "live camera")):
            return False
        m_id = str(m.get("id") or "").lower()
        if m_id.startswith("admin-") or m_id.endswith("_live"):
            return False

        # Certified official Diretta/LiveSport matches (pre-filtered by is_tier_match)
        # are real scheduled sporting events. They do not need pre-existing stream links
        # when upcoming or live, as sources can attach JIT on-demand when clicked.
        is_official = bool(
            m.get("flashscore_id")
            or m.get("_official_id")
            or m.get("_diretta_title")
            or m_id.startswith("diretta-")
        )

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
            if is_official:
                now_ms = time.time() * 1000
                live_window = cls.get_live_window_minutes(m)
                is_concluded = (date_ms > 0) and ((date_ms - now_ms) / 60000 < -live_window)
                if is_concluded:
                    return False
                return True
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

            return genre in CatalogService._REPLAY_ELIGIBLE_CALCIO_ESTERO

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
            return genre in CatalogService._REPLAY_ELIGIBLE_BASKET

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

    @staticmethod
    def _merge_epg_certified_sessions(
        linked_schedule: List[Dict[str, Any]],
        epg_certified_sessions: List[Dict[str, Any]],
    ) -> None:
        """Appends certified Italian EPG sessions not already covered by official schedule items."""
        if not epg_certified_sessions:
            return
        from app.services.thesportsdb_service import TheSportsDBService

        orphan_epg = []
        for ep in epg_certified_sessions:
            ep_title = (ep.get("title") or "").strip()
            ep_silo = (ep.get("_silo") or ep.get("category") or "").lower()
            ep_date = ep.get("date", 0)
            is_simulcast = is_simulcast_event(ep_title)
            already_covered = False

            for off in linked_schedule:
                off_title = (off.get("title") or "").strip()
                off_silo = (off.get("_silo") or off.get("category") or "").lower()
                time_diff = abs((off.get("date") or 0) - ep_date)

                if off_silo != ep_silo or time_diff > (60 * 60 * 1000):
                    continue

                # Simulcast events (e.g. "Diretta Gol", "NFL RedZone") are never covered by a single match
                if is_simulcast:
                    if is_simulcast_event(off_title) and ep_title.lower() == off_title.lower():
                        already_covered = True
                        break
                    continue

                if ep_silo == "motor-sports":
                    m_ser = TheSportsDBService.get_motorsport_series_key(off_title)
                    e_ser = TheSportsDBService.get_motorsport_series_key(ep_title)
                    if m_ser and e_ser and m_ser == e_ser:
                        off_sess = TheSportsDBService.normalize_motorsport_session_name(off_title)
                        ep_sess = TheSportsDBService.normalize_motorsport_session_name(ep_title)
                        if off_sess == ep_sess:
                            already_covered = True
                            break
                elif ep_silo == "tennis":
                    # Tennis multicourt/tournament channel sessions (e.g. "ATP 1000 - Sky Sport Tennis")
                    # are distinct from single 1v1 matches
                    if " vs " not in ep_title.lower() and " vs " in off_title.lower():
                        continue
                    if ep_title.lower() == off_title.lower():
                        already_covered = True
                        break
                else:
                    clean_ep = re.sub(r"[^a-zA-Z0-9]+", " ", ep_title.lower()).strip()
                    clean_off = re.sub(r"[^a-zA-Z0-9]+", " ", off_title.lower()).strip()
                    if clean_ep == clean_off or SequenceMatcher(None, clean_ep, clean_off).ratio() >= 0.75:
                        already_covered = True
                        break

            if not already_covered:
                orphan_epg.append(ep)

        linked_schedule.extend(orphan_epg)

    def _append_historical_replays(
        self,
        linked_schedule: List[Dict[str, Any]],
        all_db_matches: List[Dict[str, Any]],
    ) -> None:
        """Preserves replay-eligible concluded matches (6h-72h) from SQLite when Diretta shifts forward."""
        if not ENABLE_REPLAYS or not all_db_matches:
            return
        existing_ids = {m.get("id") for m in linked_schedule if m.get("id")}
        now_ms = time.time() * 1000
        historical_replays = [
            db_m for db_m in all_db_matches
            if db_m.get("id") not in existing_ids
            and self.is_real_match(db_m)
            and self.is_replay_eligible(db_m)
            and ((db_m.get("date") or 0) > 0)
            and (((db_m.get("date") or 0) - now_ms) / 60000 < -self.get_live_window_minutes(db_m))
        ]
        if historical_replays:
            linked_schedule.extend(historical_replays)
            logger.info("Preserved %d historical replay matches (6h-72h) from SQLite.", len(historical_replays))

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
                    streamfree_service.get_matches(force=True),
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
                streamfree_raw = results[9] if isinstance(results[9], list) else []

                streamed_matches = [m for m in streamed_raw if self.is_real_match(m)]
                daddylive_matches = [m for m in daddylive_raw if self.is_real_match(m)]
                epg_matches = [m for m in epg_raw if self.is_real_match(m)]
                fctv33_matches = [m for m in fctv33_raw if self.is_real_match(m)]
                mandrakodi_matches = [m for m in mandrakodi_raw if self.is_real_match(m)]
                spon_matches = [m for m in spon_raw if self.is_real_match(m)]
                streamfree_matches = [m for m in streamfree_raw if self.is_real_match(m)]

                # Clean each provider's matches with its dedicated normalizer
                clean_dl = [clean_daddylive_event(m) for m in daddylive_matches]
                clean_fc = [clean_fctv33_event(m) for m in fctv33_matches]
                clean_st = [clean_streamed_event(m) for m in streamed_matches]
                clean_mk = [clean_mandrakodi_event(m) for m in mandrakodi_matches]
                clean_sp = [clean_spon_event(m) for m in spon_matches]
                clean_sf = [clean_streamfree_event(m) for m in streamfree_matches]

                providers_map = {
                    "daddylive": clean_dl,
                    "fctv33": clean_fc,
                    "streamed": clean_st,
                    "mandrakodi": clean_mk,
                    "spon": clean_sp,
                    "streamfree": clean_sf,
                }

                # Ingest certified multicourt tournament sessions and simulcasts from Italian EPG (Sky/Tivùsat)
                epg_certified_sessions = [
                    m for m in epg_matches
                    if not m.get("_is_replay") and not m.get("is_replay") and not m.get("is_differita")
                    and (
                        (
                            (m.get("_silo") == "tennis" or m.get("category") == "tennis")
                            and not bool(isinstance(m.get("teams"), dict) and m["teams"].get("home") and m["teams"].get("away"))
                            and any(k in f"{m.get('title', '')} {m.get('competition', '')} {m.get('eventSynopsis', '')}".lower() for k in ("live", "wta", "atp", "masters", "slam"))
                        )
                        or (
                            (m.get("_silo") == "golf" or m.get("category") in ("golf", "altri_sport"))
                            and not bool(isinstance(m.get("teams"), dict) and m["teams"].get("home") and m["teams"].get("away"))
                        )
                        or (
                            # Certified live Padel tournament sessions (e.g. Premier Padel)
                            (m.get("_silo") in ("padel", "altri_sport") or m.get("category") in ("padel", "altri_sport"))
                            and any(k in (m.get("title") or "").lower() or k in (m.get("competition") or "").lower() for k in ("padel", "premier padel"))
                            and m.get("is_live") is True
                        )
                        or (
                            # Certified live Motorsport sessions from Sky (if not covered by Diretta)
                            (m.get("_silo") == "motor-sports" or m.get("category") == "motori")
                            and m.get("is_live") is True
                            and any(k in (m.get("title") or "").lower() for k in ("prove libere", "qualifiche", "gara", "sprint", "warm up"))
                            and not any(k in (m.get("title") or "").lower() for k in ("race anatomy", "aspettando", "storie di", "rubrica", "highlights"))
                        )
                        or is_simulcast_event(m.get("title", ""))
                    )
                ]
                # Pull all active matches from DB to retain existing enriched posters
                all_db_matches = [m for m in await db_service.get_active_matches_async() if self.is_real_match(m)]
                db_posters = {}
                db_posters_by_teams = {}
                db_posters_by_tokens = {}
                for m in all_db_matches:
                    p = m.get("poster")
                    if not p or "nostream" in str(p):
                        continue
                    if str(p).startswith("/posters/tennis_") and not str(p).endswith(f"_v{TENNIS_POSTER_VERSION}.jpg"):
                        continue
                    if m.get("id"):
                        db_posters[m["id"]] = p
                    tk = self._get_teams_key(m)
                    if tk:
                        db_posters_by_teams[tk] = p
                    tok = self._clean_tokens(m.get("title", ""))
                    if tok:
                        db_posters_by_tokens[tok] = p

                # 2. TOP-DOWN DIRECT LINKING:
                # Official Diretta/Flashscore schedule is primary.
                # It links directly to each provider's candidate list within a strict 60m window.
                if official_schedule:
                    linked_schedule = self.link_official_schedule_to_providers(
                        official_schedule, providers_map, time_window_minutes=60.0
                    )
                    self._merge_epg_certified_sessions(linked_schedule, epg_certified_sessions)
                    self._append_historical_replays(linked_schedule, all_db_matches)
                    all_matches = linked_schedule
                    logger.info("Top-Down Pipeline: linked %d official matches directly to providers.", len(all_matches))
                else:
                    # Diretta unavailable: fall back to last healthy catalog in RAM, then SQLite active cache,
                    # and re-link fresh provider sources + EPG certified sessions cleanly.
                    ram_schedule = [dict(m) for m in self._cached_matches if self.is_real_match(m)]
                    cached_db = [dict(m) for m in all_db_matches if self.is_real_match(m)]
                    fallback_base = ram_schedule or cached_db
                    if fallback_base:
                        src_label = "RAM cache" if ram_schedule else "SQLite cache"
                        logger.warning(
                            "Diretta sliding schedule unavailable; falling back to %s (%d events).",
                            src_label, len(fallback_base),
                        )
                        all_matches = self.link_official_schedule_to_providers(
                            fallback_base, providers_map, time_window_minutes=60.0
                        )
                    else:
                        logger.warning("Diretta sliding schedule unavailable and no cached schedule available; keeping EPG certified sessions only.")
                        all_matches = []
                    self._merge_epg_certified_sessions(all_matches, epg_certified_sessions)
                    self._append_historical_replays(all_matches, all_db_matches)

                # Reconcile motorsport events against official Orange Cat Blacktop session registry
                if ocb_sessions:
                    ocblacktop_service.reconcile_matches(all_matches, ocb_sessions)

                # Transfer existing DB posters to fresh matches if missing
                for m in all_matches:
                    p_val = str(m.get("poster") or "")
                    if p_val and "nostream" not in p_val:
                        if not (p_val.startswith("/posters/tennis_") and not p_val.endswith(f"_v{TENNIS_POSTER_VERSION}.jpg")):
                            continue
                        # Clear obsolete tennis poster so it will be freshly re-enriched
                        m["poster"] = None
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

                # Persist verified official matches into SQLite!
                if all_matches:
                    await db_service.save_matches_async(all_matches)

                # 5. LEVEL 3: Pre-classify every match into catalog and genre
                # Matches with _tsdb_matched have competition set, enabling exact genre matching;
                # Unmatched matches use the 5-layer heuristic fallback.
                for m in all_matches:
                    if not m.get("_ocb_matched"):
                        cat, genre = genre_classifier.classify(m)
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
        elif self._is_running:
            logger.critical("Watchdog: Unable to acquire a running event loop to schedule auto-restart of CatalogService sync.")

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

        # Check if catalog_id is a Nuvio flat standalone catalog
        target_genre = (genre_filter or "").strip()
        actual_catalog = catalog_id
        if catalog_id and catalog_id in NUVIO_CATALOG_LOOKUP:
            actual_catalog, target_genre = NUVIO_CATALOG_LOOKUP[catalog_id]

        # Classify and filter by catalog and genre
        now_ms = time.time() * 1000
        filtered: List[Dict[str, Any]] = []
        is_all_genre = not target_genre or target_genre in (
            "Tutti gli Eventi", "all", "All", "All Sports", "Tutti i Generi", "Tutti", "Tutti i generi"
        )
        filter_catalog = actual_catalog if actual_catalog and actual_catalog not in ("streamsport_events", "all", "All") else None

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

            filtered.append((m, item_genre))

        # 5. Sort matches chronologically:
        # Priority 0: LIVE matches now (diff <= 0 and diff >= -live_window) -> ASCENDING DATE (12:00 before 12:15)
        # Priority 1: IMMINENT & UPCOMING (diff > 0) -> ASCENDING DATE (closest to start first)
        # Priority 2: CONCLUDED REPLAYS (diff < -live_window) -> STAY AT THE BOTTOM (most recently finished first)
        # Priority 3: No date -> AT THE VERY END
        now_ms = time.time() * 1000

        def sort_priority(item_pair: Tuple[Dict[str, Any], str]) -> Tuple[int, float]:
            m = item_pair[0]
            d = m.get("date") or 0
            if not d:
                return (3, 0.0)
            diff = (d - now_ms) / 60000
            live_window = self.get_live_window_minutes(m)
            if -live_window <= diff <= 0:
                return (0, float(d))   # Live now: chronological order (earliest kickoff first)
            elif diff > 0:
                return (1, float(d))   # Upcoming: chronological order (closest to start first)
            else:
                return (2, -float(d))  # Concluded: stay at the bottom of the list

        filtered.sort(key=sort_priority)

        # 6. Safety Deduplication: Guarantee 100% unique IDs for Android Jetpack Compose
        seen_ids = set()
        unique_filtered: List[Tuple[Dict[str, Any], str]] = []
        for m, item_genre in filtered:
            raw_mid = str(m.get("id") or "")
            clean_id = raw_mid.split(":", 1)[1] if raw_mid.startswith("streamsport:") else raw_mid
            stremio_id = f"streamsport:{clean_id}"
            if stremio_id in seen_ids:
                continue
            seen_ids.add(stremio_id)
            unique_filtered.append((m, item_genre))

        # 7. Pagination & Build Meta Only for Paged Items (Massive CPU & String Allocation Optimization)
        page_slice = unique_filtered[skip : skip + limit]
        page: List[Dict[str, Any]] = []
        for m, item_genre in page_slice:
            meta_item = self.build_meta_item(m, item_genre, user_tz=user_tz, base_url=base_url)
            meta_item.pop("_date_ms", None)
            meta_item.pop("_live_window", None)
            page.append(meta_item)

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
        meta.pop("_date_ms", None)
        meta.pop("_live_window", None)
        return meta

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


    def update_cached_match(self, match: Dict[str, Any]) -> None:
        """Updates or inserts a match in the in-memory cache and ID index."""
        if not match or not match.get("id"):
            return
        m_id = str(match["id"])
        clean_id = m_id.replace("streamsport:", "")
        self._matches_by_id[m_id] = match
        self._matches_by_id[clean_id] = match
        for i, m in enumerate(self._cached_matches):
            mid = str(m.get("id", ""))
            if mid == m_id or mid == clean_id:
                self._cached_matches[i] = match
                return
        self._cached_matches.append(match)


catalog_service = CatalogService()
