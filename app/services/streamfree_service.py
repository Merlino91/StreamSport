import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.config import (
    ENABLE_STREAMFREE,
    STREAMFREE_DIRECTORY_URL,
    STREAMFREE_PRIMARY_MIRROR,
    STREAMFREE_FALLBACK_MIRRORS,
    STREAMFREE_CACHE_TTL,
)
from app.services.doh_client import doh_client
from app.services.canonical_competition_service import (
    detect_canonical_competition,
    get_canonical_display_name,
    get_canonical_silo,
)
from app.utils.demographics import extract_demographics

logger = logging.getLogger("streamsport.streamfree")


class StreamFreeService:
    """
    Direct ingestion and stream resolver for StreamFree (streamfree.top / strmfree.pk / strmfree.st).
    Features:
    - Mirror discovery via dynamic directory and fallback mirrors.
    - Cloudflare DoH transport to bypass regional/ISP DNS blocking (AGCOM Piracy Shield).
    - Cache management matching DaddyLive cadence (1 hour default, 15 min when imminent events < 5).
    - Extraction of direct 1080p FHD HLS streams with signed tokens (_0x).
    - 24/7 linear channel mapping (Sky Sports F1, Sky Sports Tennis, NFL RedZone, Willow Cricket).
    """

    CATEGORY_SILO_MAP: Dict[str, Tuple[str, str]] = {
        "soccer": ("football", "calcio"),
        "basketball": ("basketball", "basket"),
        "hockey": ("hockey", "hockey"),
        "racing": ("motor-sports", "motori"),
        "tennis": ("tennis", "tennis"),
        "football": ("american-football", "football"),
        "baseball": ("baseball", "baseball"),
        "combat": ("combattimento", "combattimento"),
        "cricket": ("altri_sport", "cricket"),
    }

    STREAMFREE_CANONICAL_CHANNELS: Dict[str, Dict[str, str]] = {
        "sky sport f1": {
            "key": "skyf1",
            "name": "Sky Sports F1 UK",
            "category": "racing",
            "lang": "Inglese",
        },
        "sky sport tennis": {
            "key": "skysportstennis",
            "name": "Sky Sports Tennis UK",
            "category": "tennis",
            "lang": "Inglese",
        },
        "redzone": {
            "key": "redzone",
            "name": "NFL RedZone US",
            "category": "football",
            "lang": "Inglese",
        },
        "nfl redzone": {
            "key": "redzone",
            "name": "NFL RedZone US",
            "category": "football",
            "lang": "Inglese",
        },
        "willow": {
            "key": "willow",
            "name": "Willow Cricket",
            "category": "cricket",
            "lang": "Inglese",
        },
        "willow cricket": {
            "key": "willow",
            "name": "Willow Cricket",
            "category": "cricket",
            "lang": "Inglese",
        },
        "sky sport cricket": {
            "key": "skysportscricket",
            "name": "Sky Sports Cricket UK",
            "category": "cricket",
            "lang": "Inglese",
        },
    }

    def __init__(self):
        self._cache: List[Dict[str, Any]] = []
        self._cache_time: float = 0.0
        self._cache_ttl: int = STREAMFREE_CACHE_TTL
        self._last_content_hash: str = ""
        self._active_mirror: str = STREAMFREE_PRIMARY_MIRROR
        self._last_mirror_check: float = 0.0
        self._lock = asyncio.Lock()
        self._resolved_streams_cache: Dict[str, Tuple[List[Dict[str, Any]], float]] = {}
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/html, */*",
        }

    async def get_active_mirror(self, force: bool = False) -> str:
        """
        Dynamically detects the currently active and healthy StreamFree mirror domain.
        Uses the dynamic directory API (https://strmfree.link/api/domains) and fallback
        mirror probes via DoH. Caches the active mirror for 6 hours.
        """
        now = time.time()
        if not force and self._active_mirror and (now - self._last_mirror_check < 21600):
            return self._active_mirror

        transport = doh_client.create_transport(verify=False)

        # 1. Primary check: Query dynamic domains directory
        try:
            async with httpx.AsyncClient(
                transport=transport,
                headers=self.headers,
                timeout=5.0,
                follow_redirects=True,
            ) as client:
                resp = await client.get(STREAMFREE_DIRECTORY_URL)
                if resp.status_code == 200:
                    domains = resp.json()
                    if isinstance(domains, list) and domains:
                        # Verify that the first returned domain is responsive
                        candidate = domains[0].strip().lower()
                        self._active_mirror = candidate
                        self._last_mirror_check = now
                        logger.info("StreamFree active mirror discovered from directory: %s", self._active_mirror)
                        return self._active_mirror
        except Exception as e:
            logger.debug("StreamFree directory check failed, trying fallback mirrors: %s", e)

        # 2. Secondary check: Probe fallback mirrors in order
        candidates = [STREAMFREE_PRIMARY_MIRROR] + [
            m for m in STREAMFREE_FALLBACK_MIRRORS if m != STREAMFREE_PRIMARY_MIRROR
        ]
        for mirror in candidates:
            try:
                async with httpx.AsyncClient(
                    transport=transport,
                    headers=self.headers,
                    timeout=4.0,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(f"https://{mirror}/streams")
                    if resp.status_code == 200:
                        self._active_mirror = mirror
                        self._last_mirror_check = now
                        logger.info("StreamFree active mirror verified via probe: %s", mirror)
                        return mirror
            except Exception:
                continue

        # 3. Default fallback
        self._active_mirror = STREAMFREE_PRIMARY_MIRROR
        self._last_mirror_check = now
        return self._active_mirror

    async def get_matches(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Fetches sports events schedule from StreamFree /streams endpoint.
        Uses identical cadence to DaddyLive:
        - 1 hour default TTL
        - Fast invalidation (15 min) if fewer than 5 upcoming events remain
        - MD5 content hash checking
        """
        if not ENABLE_STREAMFREE:
            return []

        now = time.time()
        if not force and self._cache and (now - self._cache_time < self._cache_ttl):
            return self._cache

        async with self._lock:
            if not force and self._cache and (now - self._cache_time < self._cache_ttl):
                return self._cache

            raw_json_str, active_domain = await self._fetch_streams_raw()
            if not raw_json_str:
                logger.warning("StreamFree: Could not fetch /streams from any mirror; using cached (%d)", len(self._cache))
                return self._cache

            new_content_hash = hashlib.md5(raw_json_str.encode("utf-8")).hexdigest()
            if not force and new_content_hash == self._last_content_hash and self._cache:
                self._cache_time = now
                logger.debug("StreamFree schedule unchanged (hash %s)", new_content_hash[:8])
                return self._cache

            try:
                data = json.loads(raw_json_str)
                matches = self.parse_streams_data(data, active_domain)
                if matches:
                    self._cache = matches
                    self._cache_time = now
                    self._last_content_hash = new_content_hash

                # Cache invalidation check: adjust TTL based on upcoming events
                now_ms = time.time() * 1000
                future_count = sum(1 for m in self._cache if (m.get("date") or 0) > now_ms)
                if future_count < 5:
                    self._cache_ttl = 900  # 15 minutes
                else:
                    self._cache_ttl = 3600  # 1 hour

                logger.info(
                    "Ingested %d matches from StreamFree (TTL: %ds, active domain: %s)",
                    len(self._cache),
                    self._cache_ttl,
                    active_domain,
                )
                return self._cache
            except Exception as e:
                logger.error("Failed to parse StreamFree streams JSON: %s", e, exc_info=True)
                return self._cache

    async def _fetch_streams_raw(self) -> Tuple[Optional[str], str]:
        """
        Fetches /streams JSON payload with automatic mirror failover via DoH.
        Returns (raw_json_str, active_mirror).
        """
        active_mirror = await self.get_active_mirror()
        candidates = [active_mirror] + [
            m for m in STREAMFREE_FALLBACK_MIRRORS if m != active_mirror
        ]

        transport = doh_client.create_transport(verify=False)
        for mirror in candidates:
            url = f"https://{mirror}/streams"
            try:
                async with httpx.AsyncClient(
                    transport=transport,
                    headers=self.headers,
                    timeout=8.0,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200 and "streams" in resp.text:
                        self._active_mirror = mirror
                        return resp.text, mirror
            except Exception as e:
                logger.debug("Failed to fetch StreamFree streams from %s: %s", mirror, e)

        return None, active_mirror

    def parse_streams_data(self, data: Dict[str, Any], domain: str) -> List[Dict[str, Any]]:
        """
        Parses StreamFree /streams JSON into canonical StreamSport match dictionaries.
        Posters/thumbnails are intentionally excluded per instruction.
        """
        streams_by_cat = data.get("streams", {})
        if not isinstance(streams_by_cat, dict):
            return []

        now_ms = time.time() * 1000
        matches: List[Dict[str, Any]] = []
        seen_ids = set()

        for cat_raw, items in streams_by_cat.items():
            if not isinstance(items, list):
                continue

            silo_info = self.CATEGORY_SILO_MAP.get(cat_raw.lower(), ("altri_sport", cat_raw))
            silo_id, _ = silo_info

            for item in items:
                if not isinstance(item, dict):
                    continue

                stream_key = item.get("stream_key") or item.get("id")
                if not stream_key:
                    continue

                raw_name = (item.get("name") or "").strip()
                if not raw_name:
                    continue

                # Check timestamp
                ts = item.get("match_timestamp")
                if ts:
                    try:
                        date_ms = int(float(ts)) * 1000
                    except (ValueError, TypeError):
                        date_ms = 0
                else:
                    date_ms = 0

                # Discard events scheduled more than 3 days ahead or past concluded events (> 4 hours ago)
                if date_ms:
                    if date_ms > (now_ms + 3 * 24 * 3600 * 1000):
                        continue
                    if (now_ms - date_ms) > (4 * 3600 * 1000):
                        continue

                # 24/7 channels or standalone linear broadcasts without a timestamp
                is_channel_entry = not bool(date_ms) or any(
                    k in raw_name.lower() for k in ("channel", "24/7", "redzone")
                )

                league = (item.get("league") or "").strip()

                # Parse teams if " vs " present
                teams = None
                if " vs " in raw_name.lower():
                    parts = re.split(r"\s+vs\.?\s+", raw_name, flags=re.IGNORECASE, maxsplit=1)
                    if len(parts) == 2:
                        home_raw = parts[0].strip()
                        away_raw = parts[1].strip()
                        teams = {
                            "home": {"name": home_raw},
                            "away": {"name": away_raw},
                        }

                # Canonical competition detection
                canonical_comp = detect_canonical_competition(
                    f"{league} : {raw_name}", silo_hint=silo_id
                )
                comp_display = get_canonical_display_name(canonical_comp) or league or cat_raw.title()

                # Demographic cues
                is_women, is_youth = extract_demographics(f"{league} {raw_name}")

                match_id = f"streamfree-{stream_key}"
                if match_id in seen_ids:
                    continue
                seen_ids.add(match_id)

                match_dict: Dict[str, Any] = {
                    "id": match_id,
                    "title": raw_name,
                    "category": silo_id,
                    "_silo": silo_id,
                    "canonical_competition": canonical_comp,
                    "competition": comp_display,
                    "_competition": comp_display,
                    "date": date_ms or int(now_ms),  # Fallback to current time if linear channel
                    "poster": None,  # Deliberately None per requirement
                    "teams": teams,
                    "_provider": "streamfree",
                    "_stream_key": stream_key,
                    "sources": [
                        {
                            "source": "streamfree",
                            "id": stream_key,
                            "channel": league or "StreamFree",
                            "category": cat_raw,
                            "quality": "1080p FHD",
                            "lang": "Inglese",
                        }
                    ],
                }

                if is_women:
                    match_dict["is_women"] = True
                if is_youth:
                    match_dict["is_youth"] = True
                if is_channel_entry:
                    match_dict["is_24_7"] = True

                matches.append(match_dict)

        return matches

    def get_channel_streams(self, canonical_key: str) -> List[Dict[str, Any]]:
        """
        Returns registered StreamFree 24/7 stream sources for a canonical channel key.
        Provides high-bitrate English complementary feeds for Sky Sports F1, Tennis, etc.
        """
        if not canonical_key:
            return []

        key_norm = canonical_key.lower().strip()
        info = self.STREAMFREE_CANONICAL_CHANNELS.get(key_norm)
        if not info:
            return []

        return [
            {
                "source": "streamfree",
                "id": info["key"],
                "name": info["name"],
                "channel": info["name"],
                "category": info["category"],
                "quality": "1080p FHD",
                "lang": info.get("lang", "Inglese"),
                "is_24_7": True,
            }
        ]

    async def resolve_stream_urls(
        self, stream_key: str, category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Resolves direct playable HLS manifest URLs for a StreamFree stream key.
        Performs:
        1. Query /api/v1/sources/{stream_key} to get source qualities and embed URLs.
        2. Query /get-stream-key/{stream_key} to determine backend server routing.
        3. Fetch embed page to extract signed token parameters (_t, _e, _n).
        4. Assemble signed m3u8 playlist URL with required Referer/Origin headers.
        Caches resolved URLs for 5 minutes.
        """
        if not stream_key:
            return []

        now = time.time()
        cached = self._resolved_streams_cache.get(stream_key)
        if cached and (now - cached[1] < 300):
            return cached[0]

        domain = await self.get_active_mirror()
        transport = doh_client.create_transport(verify=False)

        cat_hint = category or "soccer"
        # Check if known channel category exists
        for info in self.STREAMFREE_CANONICAL_CHANNELS.values():
            if info["key"] == stream_key:
                cat_hint = info["category"]
                break

        resolved: List[Dict[str, Any]] = []

        try:
            async with httpx.AsyncClient(
                transport=transport,
                headers=self.headers,
                timeout=8.0,
                follow_redirects=True,
            ) as client:
                # 1. Get sources list
                sources_url = f"https://{domain}/api/v1/sources/{stream_key}"
                r_src = await client.get(sources_url)
                if r_src.status_code != 200:
                    logger.debug("StreamFree sources API returned status %d for %s", r_src.status_code, stream_key)
                    return []

                src_data = r_src.json()
                sources_list = src_data.get("sources", [])
                if not isinstance(sources_list, list) or not sources_list:
                    return []

                # 2. Get server routing key
                server_url = f"https://{domain}/get-stream-key/{stream_key}"
                r_srv = await client.get(server_url)
                server_name = "externalcdn"
                if r_srv.status_code == 200:
                    srv_data = r_srv.json()
                    server_name = srv_data.get("server_name") or "externalcdn"

                # 3. Process each live source (prioritizing live=True)
                for src in sources_list:
                    if not isinstance(src, dict):
                        continue
                    if not src.get("live", True) and len(sources_list) > 1:
                        # Skip dead/inactive qualities if multiple options exist
                        continue

                    embed_url = src.get("url")
                    if not embed_url:
                        continue

                    variant = embed_url.split("?")[0].split("/")[-1]
                    quality_raw = (src.get("quality") or "").strip().upper()
                    if "1080" in variant or "FHD" in quality_raw or "1080" in quality_raw:
                        quality_label = "1080p FHD"
                    elif "720" in variant or "720" in quality_raw:
                        quality_label = "720p HD"
                    elif "540" in variant or "SD" in quality_raw or "540" in quality_raw:
                        quality_label = "540p SD"
                    else:
                        quality_label = quality_raw or "Live"

                    # Fetch embed HTML with proper player Referer
                    player_referer = f"https://{domain}/player/{cat_hint}/{stream_key}"
                    r_emb = await client.get(
                        embed_url,
                        headers={
                            **self.headers,
                            "Referer": player_referer,
                        },
                    )
                    if r_emb.status_code != 200:
                        continue

                    # Extract signed tokens (_0x)
                    tokens = {}
                    m = re.search(r"const\s+_0x\s*=\s*(\{.*?\});", r_emb.text)
                    if m:
                        try:
                            tokens = json.loads(m.group(1))
                        except Exception:
                            tokens = {}

                    qual_key = "1080p" if "1080p" in variant else ("540p" if "540p" in variant else "720p")
                    tok = tokens.get(qual_key) or tokens.get(quality_raw) or {}

                    # Assemble server path
                    if server_name == "externalcdn":
                        path = f"/live-externalcdn/{variant}/index.m3u8"
                    elif server_name != "origin":
                        path = f"/live-cdn/{variant}/index.m3u8"
                    else:
                        path = f"/live/{variant}/index.m3u8"

                    if tok and tok.get("_t"):
                        m3u8_url = f"https://{domain}{path}?_t={tok['_t']}&_e={tok['_e']}&_n={tok['_n']}"
                    else:
                        m3u8_url = f"https://{domain}{path}"

                    resolved.append({
                        "quality": quality_label,
                        "variant": variant,
                        "m3u8_url": m3u8_url,
                        "referer": embed_url,
                        "origin": f"https://{domain}",
                        "server_name": server_name,
                        "live": src.get("live", True),
                    })

        except Exception as e:
            logger.warning("Error resolving StreamFree stream for %s: %s", stream_key, e)

        if resolved:
            self._resolved_streams_cache[stream_key] = (resolved, now)

        return resolved


streamfree_service = StreamFreeService()
