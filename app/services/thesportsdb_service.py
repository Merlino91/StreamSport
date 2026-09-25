from __future__ import annotations
import asyncio
import datetime
import logging
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.services.db_service import db_service

logger = logging.getLogger("streamsport.thesportsdb")

THESPORTSDB_API_BASE = "https://www.thesportsdb.com/api/v1/json/3"

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
    Fallback service for fetching sports event posters (strThumb) from TheSportsDB.
    Includes SQLite persistent caching, API throttling, rate-limit detection,
    and automatic retry scheduling upon rate-limiting.
    """

    def __init__(self):
        self._min_interval: float = 1.2  # Throttling interval for web search and API requests
        self._blocked_until: float = 0.0
        self._rate_limit_reason: str = ""
        self._enrichment_task: Optional[asyncio.Task] = None
        self._is_enriching: bool = False

    def parse_retry_delay(self, text: str, headers: Optional[Dict[str, str]] = None, default_seconds: int = 300) -> int:
        """
        Extracts retry wait time in seconds from headers or response body text
        (e.g., 'Retry-After: 480', 'Riprova tra 8m', 'Retry in 5 minutes').
        """
        if headers:
            # Check standard HTTP header
            retry_after = headers.get("retry-after") or headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                return int(retry_after)

        if not text:
            return default_seconds

        # 1. Seconds pattern: "retry in 120s", "riprova tra 45 secondi"
        m_sec = re.search(r"(?:riprova|retry)\s+(?:in|tra)\s*(\d+)\s*(?:s|sec|secondi|seconds)\b", text, re.I)
        if m_sec:
            return int(m_sec.group(1))

        # 2. Minutes pattern: "riprova tra 8m", "riprova tra 8 minuti", "retry in 8 min"
        m_min = re.search(r"(?:riprova|retry)\s+(?:in|tra)\s*(\d+)\s*(?:m|min|minuti|minutes)\b", text, re.I)
        if m_min:
            return int(m_min.group(1)) * 60

        # 3. Generic minutes: "8m", "8 min", "8 minuti"
        m_any_min = re.search(r"\b(\d+)\s*(?:m|min|minuti|minutes)\b", text, re.I)
        if m_any_min:
            return int(m_any_min.group(1)) * 60

        # 4. Generic seconds: "120s"
        m_any_sec = re.search(r"\b(\d+)\s*(?:s|sec|secondi|seconds)\b", text, re.I)
        if m_any_sec:
            return int(m_any_sec.group(1))

        return default_seconds

    def clean_event_query(
        self, title: str, teams: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Cleans match title and extracts (query, home_name, away_name).
        Strips broadcaster prefixes, timestamps, and league annotations.
        """
        home = ""
        away = ""
        if teams and isinstance(teams, dict):
            home = (teams.get("home", {}) or {}).get("name", "").strip()
            away = (teams.get("away", {}) or {}).get("name", "").strip()

        if home and away:
            clean_home = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", home).strip()
            clean_away = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", away).strip()
            return f"{clean_home} vs {clean_away}", clean_home, clean_away

        t = title or ""
        # Strip timestamps at start: "24-09 20:45 "
        t = re.sub(r"^\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{2})?\s*:?\s*", "", t)
        # Strip category/league prefix: "ITA D1 : ", "Italy - Serie A : "
        t = re.sub(r"^[^:]+:\s*", "", t)
        # Strip parenthesized notes: "(NBL)", "(One Day International)"
        t = re.sub(r"\([^)]*\)", "", t)
        # Strip bracket notes: "[HD]"
        t = re.sub(r"\[[^\]]*\]", "", t)
        # Strip flag emojis
        t = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", t)
        t = t.strip()

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
        """Parses TheSportsDB web search results (/browse?s=...) into event dicts."""
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
            slug = match.group(2)
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

    def get_candidate_queries(self, query: str, home: Optional[str] = None, away: Optional[str] = None) -> List[str]:
        """Generates fuzzy search variants (normalizations, inverted order, token simplifications)."""
        candidates = [query]

        # 1. Spelling normalization (e.g. Olympiakos -> Olympiacos)
        if "olympiakos" in query.lower():
            candidates.append(re.sub(r"\bolympiakos\b", "Olympiacos", query, flags=re.I))

        # 2. Inverted Away vs Home
        if home and away:
            inv = f"{away} vs {home}"
            if inv.lower() not in [c.lower() for c in candidates]:
                candidates.append(inv)
            if "olympiakos" in inv.lower():
                candidates.append(re.sub(r"\bolympiakos\b", "Olympiacos", inv, flags=re.I))

            # 3. Clean secondary suffixes from home / away (e.g. "Zalgiris Kaunas" -> "Zalgiris")
            suffix_clean = lambda s: re.sub(r"\b(kaunas|bc|sk|fc|baskets|basket|club|baloncesto|basketbol)\b", "", s, flags=re.I).strip()
            h_clean = suffix_clean(home)
            a_clean = suffix_clean(away)
            if (h_clean != home or a_clean != away) and h_clean and a_clean:
                cleaned_q = f"{h_clean} vs {a_clean}"
                if cleaned_q.lower() not in [c.lower() for c in candidates]:
                    candidates.append(cleaned_q)
                cleaned_inv = f"{a_clean} vs {h_clean}"
                if cleaned_inv.lower() not in [c.lower() for c in candidates]:
                    candidates.append(cleaned_inv)

        return candidates

    async def search_event_thumb(
        self,
        query: str,
        home: Optional[str] = None,
        away: Optional[str] = None,
        date_ms: Optional[int] = None,
        category: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[int]]:
        """
        Queries TheSportsDB for an event matching the query or home/away teams.
        Uses intelligent web search engine (/browse?s=...) with fuzzy matching,
        falling back to JSON API (searchevents.php).
        """
        now = time.time()
        if now < self._blocked_until:
            wait_remaining = int(self._blocked_until - now)
            return None, wait_remaining

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
        }

        date_str = None
        if date_ms and date_ms > 0:
            try:
                date_str = datetime.datetime.fromtimestamp(
                    date_ms / 1000, tz=datetime.timezone.utc
                ).strftime("%Y-%m-%d")
            except Exception:
                date_str = None

        candidates = self.get_candidate_queries(query, home, away)

        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Smart Web Browse Search (full-text search engine across all sports and team variants)
            for cand in candidates:
                encoded_browse = urllib.parse.quote_plus(cand)
                browse_url = f"https://www.thesportsdb.com/browse?s={encoded_browse}"
                try:
                    res = await client.get(browse_url, headers=headers)
                    if res.status_code == 429 or "retry" in res.text.lower() or "too many requests" in res.text.lower():
                        delay = self.parse_retry_delay(res.text, dict(res.headers))
                        return None, delay
                    if res.status_code == 200:
                        text = res.text.strip()
                        events = []
                        if text.startswith("{") or text.startswith("["):
                            try:
                                data = res.json()
                                events = data.get("event") or data.get("events") or []
                            except Exception:
                                pass
                        elif "<b>Events</b>" in text:
                            events = self.parse_browse_events(text)

                        if events:
                            # Filter by sport if category is provided
                            if category:
                                cat_k = category.lower()
                                expected_sports = CATEGORY_SPORT_MAP.get(cat_k)
                                if expected_sports:
                                    events = [
                                        ev for ev in events
                                        if not ev.get("strSport") or ev.get("strSport").lower() in expected_sports
                                    ]

                            # Check date match
                            best_thumb = None
                            if date_str:
                                for ev in events:
                                    if ev.get("dateEvent") == date_str and ev.get("strThumb"):
                                        best_thumb = ev.get("strThumb")
                                        break
                            if not best_thumb and date_str:
                                try:
                                    target_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                                    for ev in events:
                                        ev_d = ev.get("dateEvent")
                                        if ev_d and ev.get("strThumb"):
                                            ev_dt = datetime.datetime.strptime(ev_d, "%Y-%m-%d").date()
                                            if abs((target_dt - ev_dt).days) <= 3:
                                                best_thumb = ev.get("strThumb")
                                                break
                                except Exception:
                                    pass
                            if not best_thumb and not date_str:
                                for ev in events:
                                    if ev.get("strThumb"):
                                        best_thumb = ev.get("strThumb")
                                        break

                            if best_thumb:
                                return self.format_thumb_url(best_thumb), None
                except Exception as e:
                    logger.debug("Error in TheSportsDB web browse for '%s': %s", cand, e)

        return None, None

    def start_background_enrichment(self, matches: List[Dict[str, Any]]) -> None:
        """
        Launches background poster enrichment for matches missing posters.
        If an enrichment task is already running, it continues uninterrupted.
        """
        if self._is_enriching and self._enrichment_task and not self._enrichment_task.done():
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()

        self._enrichment_task = loop.create_task(self._enrich_matches_loop(matches))

    async def _enrich_matches_loop(self, matches: List[Dict[str, Any]]) -> None:
        """
        Background task that checks SQLite cache first, then slowly queries TheSportsDB
        with throttling, handling blocks by automatically sleeping and retrying.
        """
        self._is_enriching = True
        try:
            now_ms = int(time.time() * 1000)
            cutoff_active = now_ms - (4 * 3600 * 1000)

            # Step 1: Immediate local cache resolution (0 network cost)
            # Filter strictly to active or upcoming team matches without posters.
            # Skip tennis (handled by tennis_poster_service) and individual sports without TSDB match thumbs.
            non_tsdb_cats = {"tennis", "motor-sports", "golf", "darts", "cycling"}
            missing = [
                m for m in matches
                if not m.get("poster")
                and (m.get("category") or "").lower() not in non_tsdb_cats
                and (m.get("_catalog") or "").lower() not in non_tsdb_cats
                and (m.get("date", 0) == 0 or m.get("date", 0) >= cutoff_active)
            ]
            # Prioritize live matches and matches starting soonest
            missing.sort(key=lambda x: x.get("date", 0))

            still_missing = []

            for m in missing:
                query_key, home, away = self.clean_event_query(m.get("title", ""), m.get("teams"))
                if not query_key or len(query_key) < 3:
                    continue

                cached = db_service.get_poster_from_cache(query_key)
                if cached:
                    status, thumb_url, checked_at = cached
                    if status == "found" and thumb_url:
                        m["poster"] = thumb_url
                        db_service.update_match_poster(m["id"], thumb_url)
                        continue
                    elif status == "not_found" and (time.time() - checked_at < 12 * 3600):
                        # Skip re-querying known missing events for 12 hours
                        continue

                # Skip generic non-match entries (e.g. channel roundups without teams or vs)
                if not home and not away and " vs " not in (m.get("title") or "").lower():
                    db_service.save_poster_to_cache(query_key, None, "not_found")
                    continue

                still_missing.append(m)

            if not still_missing:
                logger.debug("All %d matches already have posters or are cached.", len(matches))
                return

            logger.info("TheSportsDB fallback: %d matches need poster enrichment.", len(still_missing))

            # Step 2: Rate-limited API lookup loop with automatic retry
            idx = 0
            while idx < len(still_missing):
                m = still_missing[idx]
                if m.get("poster"):
                    idx += 1
                    continue

                query_key, home, away = self.clean_event_query(m.get("title", ""), m.get("teams"))

                # Check if we are currently rate-limited/blocked
                now = time.time()
                if now < self._blocked_until:
                    wait_sec = max(1, int(self._blocked_until - now))
                    resume_time = time.strftime("%H:%M:%S", time.localtime(self._blocked_until))
                    logger.info(
                        "TheSportsDB in attesa rate-limit. Pausa di %d secondi (ripresa prevista alle %s)...",
                        wait_sec,
                        resume_time,
                    )
                    await asyncio.sleep(wait_sec)
                    logger.info("Pausa rate-limit completata. Ripresa dell'arricchimento locandine.")

                thumb_url, retry_after = await self.search_event_thumb(
                    query_key,
                    home=home,
                    away=away,
                    date_ms=m.get("date"),
                    category=m.get("category"),
                )

                if retry_after:
                    # Rate limit encountered: add 10 seconds of safety margin to prevent early retries
                    wait_sec = retry_after + 10
                    self._blocked_until = time.time() + wait_sec
                    resume_time = time.strftime("%H:%M:%S", time.localtime(self._blocked_until))
                    logger.warning(
                        "TheSportsDB rate limit bloccato (richiesti %ds dal server). Pausa impostata a %d secondi (+10s margine di sicurezza). Ripresa automatica alle %s.",
                        retry_after,
                        wait_sec,
                        resume_time,
                    )
                    await asyncio.sleep(wait_sec)
                    logger.info("Pausa rate-limit completata. Riprovo automaticamente il match '%s'...", query_key)
                    # Retry the same index
                    continue

                if thumb_url:
                    logger.info("Found TheSportsDB thumb for '%s': %s", query_key, thumb_url)
                    db_service.save_poster_to_cache(query_key, thumb_url, "found")
                    m["poster"] = thumb_url
                    db_service.update_match_poster(m["id"], thumb_url)
                else:
                    db_service.save_poster_to_cache(query_key, None, "not_found")

                idx += 1
                # Respect free tier rate limit
                await asyncio.sleep(self._min_interval)

            logger.info("TheSportsDB fallback enrichment cycle finished.")
        except asyncio.CancelledError:
            logger.debug("TheSportsDB enrichment task cancelled.")
        except Exception as e:
            logger.warning("Error in TheSportsDB enrichment loop: %s", e)
        finally:
            self._is_enriching = False


thesportsdb_service = TheSportsDBService()
