import asyncio
import datetime
import logging
import re
import time
import unicodedata
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple
import pytz

from app.config import (
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
from app.services.tennis_poster_service import tennis_poster_service
from app.services.thesportsdb_service import thesportsdb_service


logger = logging.getLogger("streamsport.catalog")

class CatalogService:
    """
    Builds Stremio Catalog and Meta objects for StreamSport with granular genre filtering.
    """

    def __init__(self):
        self._cached_matches: List[Dict[str, Any]] = []
        self._last_sync_time: float = 0.0
        self._sync_lock: asyncio.Lock = asyncio.Lock()
        self._background_task: Optional[asyncio.Task] = None
        self._is_running: bool = False

    def format_event_date(self, timestamp_ms: int, user_tz: Optional[str] = None) -> str:
        """Converts timestamp in ms to localized Italian date string (e.g. '21 Sep 2026, 20:45')."""
        try:
            tz = pytz.timezone(user_tz or "Europe/Rome")
        except Exception:
            tz = pytz.timezone("Europe/Rome")

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

    @staticmethod
    def get_live_window_minutes(match: Dict[str, Any]) -> int:
        """
        Returns the duration in minutes an event remains 'LIVE ORA' after its kickoff/start time.
        Tailored to each sport's real-world duration.
        Falls back to 240 minutes (4 hours) for unrecognized or uncategorized events.
        """
        cat = (match.get("category") or match.get("_catalog") or "").lower()
        title = (match.get("title") or "").lower()
        genre = (match.get("_genre") or "").lower()

        # 1. Tennis: marathons up to 5 hours (especially Grand Slams)
        if "tennis" in cat or "tennis" in genre:
            return 300  # 5 hours

        # 2. Football Americano (NFL / NCAA / CFL / UFL) - must precede regular football/soccer!
        if any(k in cat for k in ("american-football", "football_americano", "nfl", "cfl", "ufl")) or "nfl" in genre:
            return 230  # 3h 50m

        # 3. Calcio / Football (Soccer): 150m for regular league matches, 170m for knockout cup competitions
        if any(k in cat for k in ("football", "soccer", "calcio")) or "calcio" in genre:
            if any(k in title for k in ("cup", "coppa", "champions", "europa", "conference", "trophy", "pokal", "del rey", "supercup", "supercoppa")):
                return 170  # ~2h 50m
            return 150  # 2h 30m

        # 4. Basket: 150m (Eurolega / Serie A / NBA)
        if "basket" in cat or "basket" in genre:
            return 150  # 2h 30m

        # 5. Motori: sprints/practice/qualifying vs full races
        if any(k in cat for k in ("motor", "racing", "motori")) or "motori" in genre:
            if any(k in title for k in ("practice", "fp1", "fp2", "fp3", "qualif", "sprint", "warm up", "warm-up")):
                return 80  # 1h 20m
            if any(k in title for k in ("nascar", "indycar", "wec", "endurance", "24h")):
                return 210  # 3h 30m
            return 160  # ~2h 40m for F1 / MotoGP Grand Prix

        # 6. Baseball (MLB)
        if "baseball" in cat or "baseball" in genre:
            return 200  # 3h 20m

        # 7. Hockey (NHL)
        if "hockey" in cat or "hockey" in genre:
            return 170  # 2h 50m

        # 8. Sport da combattimento (UFC / Boxe / MMA)
        if any(k in cat for k in ("fight", "combattimento", "mma", "ufc", "boxing")):
            return 240  # 4 hours (covers 5-fight main cards)

        # 9. Volley / Pallavolo
        if any(k in cat for k in ("volley", "pallavolo")):
            return 150  # 2h 30m

        # 10. Rugby
        if "rugby" in cat or "rugby" in genre:
            return 140  # 2h 20m

        # Fallback for uncategorized or any other sport: 240 minutes (4 hours)
        return 240

    def build_meta_item(
        self,
        match: Dict[str, Any],
        genre: str,
        user_tz: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Converts a raw match object into a Stremio meta preview object."""
        match_id = match.get("id", "")
        title = match.get("title", "Live Sports")
        date_ms = match.get("date", 0)
        is_popular = match.get("popular", False)
        poster_url = self.normalize_image_url(match.get("poster"), base_url=base_url)

        clean_title, comp_name = self.split_title_and_competition(title, genre)
        if not comp_name:
            comp_name = match.get("competition") or match.get("_competition") or ""

        now_ms = time.time() * 1000
        desc_parts = []

        if not date_ms:
            formatted_date = "Live"
            status_text = "🔴 LIVE ORA"
        else:
            formatted_date = self.format_event_date(date_ms, user_tz)
            diff_mins = int((date_ms - now_ms) / 60000)
            live_window = self.get_live_window_minutes(match)

            if diff_mins < -live_window:
                status_text = "🏁 Conclusa • Replay e Sintesi"
            elif diff_mins <= 0:
                status_text = "🔴 LIVE ORA"
            elif diff_mins <= 20:
                status_text = f"⏳ Inizio tra {diff_mins} min"
            elif diff_mins < 1440:
                status_text = f"📅 Inizio tra {diff_mins // 60}h {diff_mins % 60}m"
            else:
                days_left = diff_mins // 1440
                status_text = f"📅 Tra {days_left} giorni"

        # Description structure: Status first -> Competition without emoji -> Competitors
        desc_parts.append(status_text)
        if comp_name:
            desc_parts.append(comp_name)
        if clean_title:
            desc_parts.append(clean_title)

        if is_popular:
            desc_parts.append("⭐ In Evidenza")

        description = " • ".join(desc_parts)
        stremio_id = f"streamsport:{match_id}"

        item = {
            "id": stremio_id,
            "type": CATALOG_TYPE,
            "name": clean_title if clean_title else title,
            "genres": [genre],
            "posterShape": "landscape",
            "description": description,
            "releaseInfo": formatted_date,
            "_date_ms": date_ms,
            "_live_window": live_window,
        }
        if poster_url:
            item["poster"] = poster_url
            item["background"] = poster_url
        return item

    def _clean_tokens(self, title: str) -> str:
        t = (title or "").lower()
        t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("utf-8")
        t = re.sub(r"\|\s*[^|]+$", "", t)
        t = re.sub(r"^[^:]+:\s*", "", t)
        t = re.sub(r"\([^)]*\)", "", t)
        t = re.sub(r"^(?:italy|england|spain|germany|france|uefa|fifa|brazil|usa|[a-z]+)\s*-\s*", "", t)
        t = re.sub(r"[^a-z0-9]+", " ", t).strip()
        words = sorted([w for w in t.split() if w not in ("vs", "the", "fc", "ac", "cf", "sc", "as", "at", "live", "stream") and len(w) > 1])
        return " ".join(words)

    def _get_teams_key(self, match: Dict[str, Any]) -> Optional[Any]:
        teams = match.get("teams")
        if isinstance(teams, dict) and teams.get("home") and teams.get("away"):
            h = re.sub(r"[^a-z0-9]+", " ", (teams.get("home", {}).get("name") or "").lower()).strip()
            a = re.sub(r"[^a-z0-9]+", " ", (teams.get("away", {}).get("name") or "").lower()).strip()
            h_words = frozenset([w for w in h.split() if w not in ("fc", "ac", "cf", "sc", "as", "the") and len(w) > 1])
            a_words = frozenset([w for w in a.split() if w not in ("fc", "ac", "cf", "sc", "as", "the") and len(w) > 1])
            if h_words and a_words:
                return frozenset([h_words, a_words])
        return None

    @staticmethod
    def get_canonical_silo(m: Dict[str, Any]) -> str:
        """
        Determines the canonical sport silo for a match, reconciling
        inconsistencies between _silo tags and category strings.
        """
        s = (m.get("_silo") or "").lower().strip()
        if s:
            if s in ("soccer", "football"): return "football"
            if s in ("basket", "basketball"): return "basketball"
            if s in ("volleyball", "volley"): return "volley"
            if s in ("motor-sports", "motorsport", "motori"): return "motor-sports"
            if s in ("fight", "combattimento", "boxe", "boxing", "mma", "ufc"): return "fight"
            return s
        cat = (m.get("category") or "").lower().strip()
        if cat in ("soccer", "football"): return "football"
        if cat in ("basket", "basketball"): return "basketball"
        if cat in ("volleyball", "volley"): return "volley"
        if cat in ("motor-sports", "motorsport", "motori"): return "motor-sports"
        if cat in ("fight", "combattimento", "boxe", "boxing", "mma", "ufc"): return "fight"
        if cat in ("baseball",): return "baseball"
        if cat in ("hockey",): return "hockey"
        if cat in ("american-football", "nfl", "cfl"): return "american-football"
        if cat in ("tennis",): return "tennis"
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

    def merge_and_deduplicate(
        self,
        primary_matches: List[Dict[str, Any]],
        secondary_matches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Merges duplicate events into a single card, combining their streams and best metadata."""
        merged_list: List[Dict[str, Any]] = [dict(m) for m in primary_matches]

        for m2 in secondary_matches:
            m2_id = m2.get("id")
            m2_tokens = self._clean_tokens(m2.get("title", ""))
            m2_teams = self._get_teams_key(m2)
            m2_date = m2.get("date") or 0
            m2_words = set(m2_tokens.split()) if m2_tokens else set()
            m2_silo = self.get_canonical_silo(m2)

            matched_idx = -1

            # 0. Direct ID matching: identical ID is 100% the exact same match!
            if m2_id:
                for idx, existing in enumerate(merged_list):
                    if existing.get("id") == m2_id:
                        matched_idx = idx
                        break

            # 1. Similarity and fuzzy matching within the same silo
            if matched_idx < 0:
                for idx, existing in enumerate(merged_list):
                    ex_silo = self.get_canonical_silo(existing)
                    if ex_silo != m2_silo:
                        continue

                    ex_teams = self._get_teams_key(existing)
                    ex_tokens = self._clean_tokens(existing.get("title", ""))
                    ex_date = existing.get("date") or 0
                    ex_words = set(ex_tokens.split()) if ex_tokens else set()

                    # Date Compatibility: Must coincide 100% (same time, or within 45m for broadcast pre-game offsets)
                    if ex_date and m2_date:
                        if ex_date != m2_date and abs(ex_date - m2_date) > 45 * 60 * 1000:
                            continue

                    # Title & Team Similarity (>= 70% similarity or subset with at least 2 common words)
                    is_same = False
                    if m2_teams and ex_teams and m2_teams == ex_teams:
                        is_same = True
                    elif m2_tokens and ex_tokens:
                        if m2_tokens == ex_tokens:
                            is_same = True
                        else:
                            common = m2_words & ex_words
                            union = m2_words | ex_words
                            sim = len(common) / len(union) if union else 0.0
                            is_subset = len(common) >= 2 and (m2_words.issubset(ex_words) or ex_words.issubset(m2_words))
                            if sim >= 0.70 or is_subset:
                                is_same = True

                    if is_same:
                        matched_idx = idx
                        break

            if matched_idx >= 0:
                existing = merged_list[matched_idx]
                existing_sources = existing.setdefault("sources", [])
                existing_ids = {str(s.get("id")) for s in existing_sources if isinstance(s, dict)}

                for s in m2.get("sources", []):
                    if isinstance(s, dict) and str(s.get("id")) not in existing_ids:
                        existing_sources.append(s)
                        existing_ids.add(str(s.get("id")))

                if not existing.get("poster") and m2.get("poster"):
                    existing["poster"] = m2["poster"]
                if not existing.get("date") and m2.get("date"):
                    existing["date"] = m2["date"]
                if not existing.get("competition") and m2.get("competition"):
                    existing["competition"] = m2["competition"]
                if not existing.get("_competition") and m2.get("_competition"):
                    existing["_competition"] = m2["_competition"]
                if not existing.get("_silo") and m2.get("_silo"):
                    existing["_silo"] = m2["_silo"]
            else:
                merged_list.append(dict(m2))

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

    @staticmethod
    def is_real_match(m: Dict[str, Any]) -> bool:
        date_ms = m.get("date") or 0
        if date_ms <= 0:
            return False
        title = (m.get("title") or "").lower()
        if any(w in title for w in ("schedule", "channel", "24/7", "tv shows", "big brother", "live camera")):
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
                home = (teams.get("home", {}).get("name") or "").lower() if isinstance(teams, dict) else ""
                away = (teams.get("away", {}).get("name") or "").lower() if isinstance(teams, dict) else ""
                combined_check = f"{title} {home} {away}"
                return bool(re.search(r"\b(italia|italy|azzurr[ie])\b", combined_check))
            return True

        # 2. Calcio Internazionale: Top European leagues, UEFA cups, and National team tournaments
        if cat == "calcio_estero":
            if genre in ("Nazionali e Amichevoli", "Europei Under 21 e Nazionali Giovanili"):
                teams = match.get("teams") or {}
                home = (teams.get("home", {}).get("name") or "").lower() if isinstance(teams, dict) else ""
                away = (teams.get("away", {}).get("name") or "").lower() if isinstance(teams, dict) else ""
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
            if not force and self._cached_matches and (now - self._last_sync_time < CATALOG_SYNC_INTERVAL):
                return

            logger.info("Starting background sports schedule sync...")
            try:
                # 1. Fetch live matches from StreamedAPI, DaddyLiveAPI, and official ESPN & OCB registries concurrently
                tasks = [
                    streamed_api.get_all_matches(force_refresh=True),
                    daddylive_api.get_matches(force=True),
                    espn_service.get_official_events(),
                    ocblacktop_service.get_official_sessions(),
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                streamed_raw = results[0] if isinstance(results[0], list) else []
                daddylive_raw = results[1] if isinstance(results[1], list) else []
                espn_events = results[2] if isinstance(results[2], list) else []
                ocb_sessions = results[3] if isinstance(results[3], list) else []

                streamed_matches = [m for m in streamed_raw if self.is_real_match(m)]
                daddylive_matches = [m for m in daddylive_raw if self.is_real_match(m)]

                # Merge streamed and daddylive matches silo-by-silo into combined fresh
                combined_fresh = self.merge_and_deduplicate_by_silos(streamed_matches, daddylive_matches)

                # 2. Pull all active matches from DB to retain existing enriched posters
                all_db_matches = [m for m in db_service.get_active_matches() if self.is_real_match(m)]
                all_matches = self.merge_and_deduplicate_by_silos(combined_fresh, all_db_matches) if all_db_matches else combined_fresh

                # 3. Persist merged matches in DB
                if all_matches:
                    db_service.save_matches(all_matches)

                # Purge matches older than retention window from DB (6h if ENABLE_REPLAYS=False, 72h if True)
                db_service.purge_expired_matches()

                # 4. Reconcile against official ESPN registry (names, exact UTC time, official catalog/genre)
                if espn_events:
                    espn_service.reconcile_matches(all_matches, espn_events, self._clean_tokens, self._get_teams_key)

                # 4b. Reconcile motorsport events against official Orange Cat Blacktop session registry
                if ocb_sessions:
                    ocblacktop_service.reconcile_matches(all_matches, ocb_sessions)

                # 5. Pre-classify every match into catalog and genre (if not already officially classified by ESPN or OCB)
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
                filtered_matches = []
                for m in all_matches:
                    d = m.get("date") or 0
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
                    db_service.delete_matches_by_ids(ids_to_purge)
                    logger.info("Purged %d concluded events from SQLite (ENABLE_REPLAYS=%s).", len(ids_to_purge), ENABLE_REPLAYS)

                all_matches = filtered_matches

                self._cached_matches = all_matches
                self._last_sync_time = time.time()
                logger.info("Background sports sync complete. %d active events indexed in RAM.", len(all_matches))

                # 7. Fallback poster enrichment from TheSportsDB (non-blocking, auto-retrying)
                thesportsdb_service.start_background_enrichment(all_matches)

                # 8. Dynamic 16:9 poster generation for Tennis events lacking artwork
                tennis_poster_service.start_background_enrichment(all_matches)
            except Exception as e:
                logger.error("Error during background sports schedule sync: %s", e, exc_info=True)

    def start_background_sync(self):
        """Starts the periodic background sync task."""
        if self._is_running:
            return
        self._is_running = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        self._background_task = loop.create_task(self._background_loop())
        logger.info("CatalogService background sync scheduled every %d seconds.", CATALOG_SYNC_INTERVAL)

    def stop_background_sync(self):
        """Stops the periodic background sync task."""
        self._is_running = False
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()
            logger.info("CatalogService background sync stopped.")

    async def _background_loop(self):
        """Periodic background loop running every CATALOG_SYNC_INTERVAL seconds."""
        while self._is_running:
            try:
                await self.sync_matches()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Unexpected error in background sync loop: %s", e)

            try:
                await asyncio.sleep(CATALOG_SYNC_INTERVAL)
            except asyncio.CancelledError:
                break

    async def ensure_synced(self):
        """Ensures that at least one sync has completed before serving requests."""
        if not self._cached_matches:
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
                home = (teams.get("home", {}).get("name") or "").lower() if isinstance(teams, dict) else ""
                away = (teams.get("away", {}).get("name") or "").lower() if isinstance(teams, dict) else ""
                if q not in m_title and q not in home and q not in away:
                    continue

            meta_item = self.build_meta_item(m, item_genre, user_tz=user_tz, base_url=base_url)
            filtered.append(meta_item)

        # 5. Sort matches chronologically:
        # Priority 0: LIVE matches now (diff <= 0 and diff >= -live_window)
        # Priority 1: IMMINENT & UPCOMING (diff > 0) -> SORTED CLOSEST FIRST (ASCENDING DATE)
        # Priority 2: CONCLUDED REPLAYS (diff < -live_window) -> SORTED MOST RECENT FIRST
        # Priority 3: No date
        now_ms = time.time() * 1000

        def sort_priority(item: Dict[str, Any]) -> Tuple[int, float]:
            d = item.get("_date_ms") or 0
            if not d:
                return (3, 0.0)
            diff = (d - now_ms) / 60000
            live_window = item.get("_live_window", 240)
            if -live_window <= diff <= 0:
                return (0, -float(d))  # Live now: most recently started first
            elif diff > 0:
                return (1, float(d))   # Upcoming: CLOSEST TO START FIRST
            else:
                return (2, -float(d))  # Concluded: most recently concluded first

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

        # 7. Pagination & Schema Cleanup
        page = filtered[skip : skip + limit]
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

        # 0. Check in-memory cached matches first (instant!)
        match = None
        if self._cached_matches:
            for m in self._cached_matches:
                if m.get("id") == clean_id or clean_id in str(m.get("id", "")):
                    match = m
                    break

        # 1. Check Streamed
        if not match:
            match = await streamed_api.find_match_by_slug_and_id(clean_id)

        # 2. Check DaddyLive
        if not match:
            dl_matches = await daddylive_api.get_matches()
            for m in dl_matches:
                if m.get("id") == clean_id or clean_id in str(m.get("id", "")):
                    match = m
                    break

        # 3. Check SQLite DB
        if not match:
            match = db_service.get_match_by_id(clean_id)

        if not match:
            return None

        genre = match.get("_genre")
        if not genre:
            _, genre = genre_classifier.classify(match)
        meta = self.build_meta_item(match, genre, user_tz=user_tz, base_url=base_url)
        meta["background"] = meta.get("poster")
        return meta

catalog_service = CatalogService()
