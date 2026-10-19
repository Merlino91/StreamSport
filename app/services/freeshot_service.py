"""
Freeshot Italian TV Channels Service.

Manages the mapping and stream resolution for Italian linear TV channels hosted
on the Freeshot / WideIPTV infrastructure.

Rules:
- Maps canonical Italian channel names (Sky Sport, DAZN, Eurosport, SuperTennis, Rai, Mediaset)
  to their tested Freeshot embed codes.
- Streams are dispatched through EasyProxy with host=freeshot and d={embed_code}.
- When displayed in Stremio/Nuvio, Freeshot sources are positioned immediately below TvVoo sources.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("streamsport.freeshot_service")

# Complete verified mapping of Italian channels on Freeshot (from freeshot.live/live-tv/italia)
# Key: canonical channel key (matching TvVoo canonical keys)
# Value: dict with embed code, display name, and category
FREESHOT_ITALIAN_CHANNELS: Dict[str, Dict[str, str]] = {
    # DAZN
    "dazn 1": {"code": "ZonaDAZN", "name": "Zona DAZN"},
    # Sky Sport
    "sky sport uno": {"code": "SkySportUnoItalia", "name": "Sky Sport Uno"},
    "sky sport calcio": {"code": "SkySportCalcioIT", "name": "Sky Sport Calcio"},
    "sky sport tennis": {"code": "SkySportTennisIT", "name": "Sky Sport Tennis"},
    "sky sport f1": {"code": "SkySportF1Italia", "name": "Sky Sport F1"},
    "sky sport motogp": {"code": "SkySportMotoGPIT", "name": "Sky Sport MotoGP"},
    "sky sport arena": {"code": "SkySportArenaIT", "name": "Sky Sport Arena"},
    "sky sport nba": {"code": "SkySportNBAIT", "name": "Sky Sport NBA"},
    "sky sport golf": {"code": "SkySportGolfIT", "name": "Sky Sport Golf"},
    "sky sport max": {"code": "SkySportMaxIT", "name": "Sky Sport Max"},
    "sky sport 24": {"code": "SkySport24IT", "name": "Sky Sport 24"},
    "sky sport legend": {"code": "SkySportLegendIT", "name": "Sky Sport Legend"},
    "sky sport mix": {"code": "SkySportMixIT", "name": "Sky Sport Mix"},
    # Eurosport
    "eurosport 1": {"code": "Eurosport1IT", "name": "Eurosport 1"},
    "eurosport 2": {"code": "Eurosport2IT", "name": "Eurosport 2"},
    # Tennis & Sport Nazionali
    "supertennis": {"code": "SuperTennisIT", "name": "SuperTennis"},
    "solocalcio": {"code": "SoloCalcio", "name": "SoloCalcio"},
    "sportitalia solocalcio": {"code": "SoloCalcio", "name": "SoloCalcio"},
    "sportitalia": {"code": "Sportitalia", "name": "Sportitalia"},
    "bike channel": {"code": "BikeIT", "name": "Bike Channel"},
    # Club Tematici Serie A
    "inter tv": {"code": "InterTV", "name": "Inter TV"},
    "milan tv": {"code": "MilanTV", "name": "Milan TV"},
    # Rai DTT
    "rai sport": {"code": "RaiSport", "name": "Rai Sport"},
    "rai 1": {"code": "Rai1IT", "name": "Rai 1"},
    "rai 2": {"code": "Rai2IT", "name": "Rai 2"},
    "rai 3": {"code": "Rai3IT", "name": "Rai 3"},
    "rai 4": {"code": "Rai4IT", "name": "Rai 4"},
    "rai news 24": {"code": "RaiNews24", "name": "Rai News 24"},
    # Mediaset & FTA Nazionali
    "canale 5": {"code": "Canale-5", "name": "Canale 5"},
    "italia 2": {"code": "ITALIA2TV", "name": "Italia 2"},
    "mediaset extra": {"code": "MediasetExtra", "name": "Mediaset Extra"},
    "la7": {"code": "La7", "name": "La7"},
    "la7d": {"code": "LA7d", "name": "La7d"},
}

# Lookup by Sky / Tivùsat official channel IDs
SKY_TO_FREESHOT_KEY: Dict[str, str] = {
    "9094": "sky sport 24",
    "9097": "sky sport uno",
    "9113": "sky sport calcio",
    "11237": "sky sport tennis",
    "9093": "sky sport arena",
    "9116": "sky sport nba",
    "9103": "sky sport max",
    "9096": "sky sport f1",
    "9102": "sky sport motogp",
    "10254": "sky sport golf",
    "11910": "sky sport legend",
    "11909": "sky sport mix",
    "6000": "supertennis",
    "11402": "dazn 1",
    "807": "rai sport",
    "9513": "milan tv",
    "9893": "inter tv",
    "899": "rai 1",
    "898": "rai 2",
    "897": "rai 3",
}

TIVUSAT_TO_FREESHOT_KEY: Dict[str, str] = {
    "42": "rai 1",
    "43": "rai 2",
    "44": "rai 3",
    "88": "rai sport",
    "145": "canale 5",
    "295": "solocalcio",
}


import asyncio
import re
import time
import httpx
from dataclasses import dataclass


@dataclass
class FreeshotLiveSession:
    channel_code: str
    master_m3u8: str
    sub_m3u8: str
    ts_base_url: str
    valid_until: float
    last_fetched_at: float
    last_manifest: Optional[str] = None
    last_manifest_time: float = 0.0
    refresh_task: Optional[asyncio.Task] = None


class FreeshotLiveManifestManager:
    """
    Internal No-Freeze Live Manifest Manager for Freeshot (WideIPTV / BlueTier).
    Acts as a zero-video-bandwidth manifest proxy:
    - Automatically resolves the latest WideIPTV token and streamUrl without web scraping.
    - Resolves master playlist into sub-manifest (mono.m3u8).
    - Rewrites relative .ts chunks into absolute URLs pointing directly to CDN edge servers (0% video bandwidth).
    - Continuously updates MEDIA-SEQUENCE and chunk list to prevent loops and stalls.
    - Proactively refreshes token in background when < 5 mins remain.
    """

    def __init__(self, service: "FreeshotService"):
        self.service = service
        self.sessions: Dict[str, FreeshotLiveSession] = {}
        self._lock = asyncio.Lock()
        self.base_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Referer": "https://freeshot.live/",
            "Origin": "https://freeshot.live",
        }
        self.cdn_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Referer": "https://wideiptv.top/",
            "Origin": "https://wideiptv.top",
        }

    def cleanup(self) -> None:
        now = time.time()
        stale_keys = [k for k, s in self.sessions.items() if (now - s.last_fetched_at) > 1800]
        for k in stale_keys:
            self.sessions.pop(k, None)

    async def get_live_manifest(self, channel_code: str) -> Optional[str]:
        self.cleanup()
        now = time.time()

        async with self._lock:
            session = self.sessions.get(channel_code)
            if not session or session.valid_until <= now:
                session = await self._refresh_session(session, channel_code)
            elif (session.valid_until - now) < 300 and (not session.refresh_task or session.refresh_task.done()):
                session.refresh_task = asyncio.create_task(self._refresh_session(session, channel_code))

        if not session:
            return None

        session.last_fetched_at = now

        # Throttling to prevent hammering upstream for rapid repeated requests (< 1.0s)
        if session.last_manifest and (now - session.last_manifest_time) < 1.0:
            return session.last_manifest

        # Fetch fresh sub-manifest (mono.m3u8) to keep MEDIA-SEQUENCE advancing
        try:
            raw_sub = await self._fetch_upstream(session.sub_m3u8)
        except Exception as exc:
            logger.warning("[FreeshotManifest] Upstream sub-manifest fetch issue (%s) for %s", exc, channel_code)
            if session.last_manifest and (now - session.last_manifest_time) < 10.0:
                return session.last_manifest

            # Synchronous token refresh on failure
            session = await self._refresh_session(session, channel_code)
            if not session:
                return None
            raw_sub = await self._fetch_upstream(session.sub_m3u8)

        rewritten = self._rewrite_manifest(raw_sub, session.ts_base_url)
        session.last_manifest = rewritten
        session.last_manifest_time = time.time()
        return rewritten

    async def _fetch_upstream(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=4.0) as client:
            res = await client.get(url, headers=self.cdn_headers)
            if res.status_code != 200:
                raise RuntimeError(f"Freeshot upstream error: {res.status_code}")
            return res.text

    async def _refresh_session(self, current: Optional[FreeshotLiveSession], channel_code: str) -> Optional[FreeshotLiveSession]:
        target_player = f"https://wideiptv.top/player/{channel_code}"
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(target_player, headers=self.base_headers)
                if resp.status_code != 200:
                    logger.warning("[FreeshotManifest] Player returned %d for %s", resp.status_code, channel_code)
                    return current

                match = re.search(r'streamUrl\s*:\s*"([^"]+)"', resp.text)
                if not match:
                    logger.warning("[FreeshotManifest] No streamUrl in player for %s", channel_code)
                    return current

                master_url = match.group(1).replace("\\", "").strip()

                # Fetch master playlist to discover sub-manifest (mono.m3u8)
                master_resp = await client.get(master_url, headers=self.cdn_headers)
                if master_resp.status_code != 200:
                    logger.warning("[FreeshotManifest] Master m3u8 returned %d for %s", master_resp.status_code, channel_code)
                    return current

                lines = [l.strip() for l in master_resp.text.splitlines() if l.strip() and not l.startswith("#")]
                if not lines:
                    logger.warning("[FreeshotManifest] Master m3u8 empty for %s", channel_code)
                    return current

                sub_line = lines[0]
                if sub_line.startswith("http://") or sub_line.startswith("https://"):
                    sub_url = sub_line
                else:
                    base_dir = master_url.rsplit("/", 1)[0]
                    sub_url = f"{base_dir}/{sub_line}"

                ts_base_url = sub_url.rsplit("/", 1)[0]
                valid_until = time.time() + 1200  # 20 minutes

                updated = FreeshotLiveSession(
                    channel_code=channel_code,
                    master_m3u8=master_url,
                    sub_m3u8=sub_url,
                    ts_base_url=ts_base_url,
                    valid_until=valid_until,
                    last_fetched_at=time.time(),
                    last_manifest=current.last_manifest if current else None,
                    last_manifest_time=current.last_manifest_time if current else 0.0,
                    refresh_task=None,
                )
                self.sessions[channel_code] = updated
                logger.info("[FreeshotManifest] Session refreshed for %s (valid for ~%ds)", channel_code, int(valid_until - time.time()))
                return updated
        except Exception as e:
            logger.warning("[FreeshotManifest] Session refresh error for %s: %s", channel_code, e)
            return current

    def _rewrite_manifest(self, raw: str, base_url: str) -> str:
        lines = raw.splitlines()
        out_lines = []
        for line in lines:
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#") or trimmed.startswith("http://") or trimmed.startswith("https://"):
                out_lines.append(line)
            else:
                sep = "" if base_url.endswith("/") else "/"
                out_lines.append(f"{base_url}{sep}{trimmed}")
        return "\n".join(out_lines) + "\n"


class FreeshotService:
    """Service providing Freeshot stream mapping, LiveManifestManager, and EasyProxy link generation."""

    def __init__(self):
        self.manifest_manager = FreeshotLiveManifestManager(self)

    async def get_live_manifest(self, channel_code: str) -> Optional[str]:
        """Returns rewritten no-freeze live manifest for a Freeshot channel code."""
        return await self.manifest_manager.get_live_manifest(channel_code)

    def get_channel_info(
        self,
        canonical_key: Optional[str] = None,
        sky_id: Optional[str] = None,
        tivusat_id: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        """Resolves a channel to its Freeshot embed info."""
        target_key = None
        if sky_id and str(sky_id) in SKY_TO_FREESHOT_KEY:
            target_key = SKY_TO_FREESHOT_KEY[str(sky_id)]
        elif tivusat_id and str(tivusat_id) in TIVUSAT_TO_FREESHOT_KEY:
            target_key = TIVUSAT_TO_FREESHOT_KEY[str(tivusat_id)]
        elif canonical_key:
            target_key = canonical_key.lower().strip()

        if not target_key:
            return None

        # Direct match or alias
        if target_key in FREESHOT_ITALIAN_CHANNELS:
            return FREESHOT_ITALIAN_CHANNELS[target_key]

        # Substring/fuzzy match
        for k, info in FREESHOT_ITALIAN_CHANNELS.items():
            if k in target_key or target_key in k:
                return info

        return None

    def has_channel(
        self,
        canonical_key: Optional[str] = None,
        sky_id: Optional[str] = None,
        tivusat_id: Optional[str] = None,
    ) -> bool:
        """Returns True if the channel is available on Freeshot."""
        return self.get_channel_info(canonical_key=canonical_key, sky_id=sky_id, tivusat_id=tivusat_id) is not None

    def get_stream_item(self, channel_code: str, display_name: str) -> Dict[str, Any]:
        """Returns a source dict for match sources."""
        return {
            "source": "freeshot",
            "id": channel_code,
            "name": display_name,
            "code": channel_code,
            "country": "Italy",
        }


freeshot_service = FreeshotService()

