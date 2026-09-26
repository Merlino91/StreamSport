import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from app.config import STREAMED_API_HOST, STREAMED_CACHE_TTL, STREAMED_FALLBACK_HOSTS
from app.services.doh_client import doh_client

logger = logging.getLogger("easysports.streamed")

STREAMED_SPORTS_MAP = {
    "football": "football",
    "basketball": "basketball",
    "tennis": "tennis",
    "motor-sports": "motor-sports",
    "american-football": "american-football",
    "baseball": "baseball",
    "hockey": "hockey",
    "fight": "fight",
    "rugby": "altri_sport",
    "golf": "altri_sport",
    "cricket": "altri_sport",
    "darts": "altri_sport",
    "afl": "altri_sport",
    "billiards": "altri_sport",
    "other": "altri_sport",
}

class StreamedAPI:
    """Client for the Streamed sports events and streams API."""

    def __init__(self):
        self._matches_cache: List[Dict[str, Any]] = []
        self._cache_timestamp: float = 0
        self._base_host = STREAMED_API_HOST
        self._hosts = [STREAMED_API_HOST] + [h for h in STREAMED_FALLBACK_HOSTS if h != STREAMED_API_HOST]
        # Short stream cache: (source, source_id) -> (streams, timestamp)
        self._stream_cache: Dict[str, Tuple[List[Dict[str, Any]], float]] = {}
        self._stream_cache_ttl = 15  # seconds

    async def get_all_matches(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Fetches matches across all sports disciplines using structured per-sport endpoints.
        Assigns the correct _silo to each match at the source.
        Falls back to alternate hosts or /api/matches/all if needed.
        """
        now = time.time()
        if not force_refresh and self._matches_cache and (now - self._cache_timestamp < STREAMED_CACHE_TTL):
            return self._matches_cache

        import asyncio

        for host in self._hosts:
            try:
                # 1. Primary: Fetch all sports concurrently using per-sport endpoints
                tasks = [
                    doh_client.get_json(f"https://{host}/api/matches/{sport}", host_header=host)
                    for sport in STREAMED_SPORTS_MAP.keys()
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                all_items: List[Dict[str, Any]] = []
                seen_ids = set()

                for sport, res in zip(STREAMED_SPORTS_MAP.keys(), results):
                    if isinstance(res, list):
                        silo = STREAMED_SPORTS_MAP[sport]
                        for m in res:
                            if isinstance(m, dict):
                                mid = m.get("id")
                                if mid and mid in seen_ids:
                                    continue
                                if mid:
                                    seen_ids.add(mid)
                                m["_silo"] = silo
                                if not m.get("category"):
                                    m["category"] = sport
                                all_items.append(m)

                if all_items:
                    self._matches_cache = all_items
                    self._cache_timestamp = now
                    self._base_host = host
                    logger.info("Fetched %d matches across %d sports from Streamed API (%s)", len(all_items), len(STREAMED_SPORTS_MAP), host)
                    return all_items

                # 2. Fallback to /api/matches/all on that host if per-sport returned empty
                fallback_data = await doh_client.get_json(f"https://{host}/api/matches/all", host_header=host)
                if isinstance(fallback_data, list) and len(fallback_data) > 0:
                    for m in fallback_data:
                        cat = (m.get("category") or "").lower()
                        m["_silo"] = STREAMED_SPORTS_MAP.get(cat, "altri_sport")
                    self._matches_cache = fallback_data
                    self._cache_timestamp = now
                    self._base_host = host
                    logger.info("Fetched %d matches via /api/matches/all fallback (%s)", len(fallback_data), host)
                    return fallback_data

            except Exception as e:
                logger.warning("Error fetching matches from %s: %s", host, e)

        # Fallback to expired cache if available on network error
        if self._matches_cache:
            logger.warning("Network error across all hosts, serving %d matches from expired cache", len(self._matches_cache))
            return self._matches_cache

        return []

    async def get_streams_for_source(self, source: str, source_id: str) -> List[Dict[str, Any]]:
        """
        Fetches the stream list for a given match source.
        Endpoint: /api/stream/{source}/{source_id}
        """
        cache_key = f"{source}:{source_id}"
        now = time.time()
        if cache_key in self._stream_cache:
            cached_streams, cached_time = self._stream_cache[cache_key]
            if now - cached_time < self._stream_cache_ttl:
                return cached_streams

        for host in self._hosts:
            url = f"https://{host}/api/stream/{source}/{source_id}"
            try:
                data = await doh_client.get_json(url, host_header=host)
                if isinstance(data, list):
                    self._stream_cache[cache_key] = (data, now)
                    return data
            except Exception as e:
                logger.warning("Error fetching streams from %s: %s", host, e)

        return []

    async def find_match_by_slug_and_id(self, slug_or_id: str) -> Optional[Dict[str, Any]]:
        """
        Finds a match in the active match list by full ID or suffix.
        """
        matches = await self.get_all_matches()
        # Direct ID match
        for m in matches:
            if m.get("id") == slug_or_id:
                return m

        # Match by ending ID number or substring
        for m in matches:
            mid = str(m.get("id", ""))
            if mid.endswith(slug_or_id) or slug_or_id in mid:
                return m

        return None

streamed_api = StreamedAPI()