import asyncio
import logging
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple
from app.services.doh_client import doh_client

logger = logging.getLogger("easysports.dailymotion")


class DailymotionService:
    """
    Extracts sports highlights from Dailymotion with a strict 7-day freshness filter
    and resolves direct .m3u8 HLS master streams via yt-dlp with TLS impersonation.
    """

    def __init__(self):
        self._api_base = "https://api.dailymotion.com"
        # In-memory cache for resolved stream URLs (video_id -> (timestamp, stream_url))
        self._manifest_cache: Dict[str, Tuple[float, str]] = {}

    def clean_search_query(self, title: str) -> str:
        """Cleans match title into effective search terms for highlights."""
        cleaned = title
        for sep in [" vs ", " vs. ", " - ", " v "]:
            if sep in cleaned:
                parts = cleaned.split(sep, 1)
                t1 = parts[0].strip()
                t2 = parts[1].strip()
                return f"{t1} {t2} highlights"

        return f"{cleaned} highlights"

    async def search_videos(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Searches Dailymotion for candidate highlight videos."""
        encoded_q = urllib.parse.quote_plus(query)
        url = f"{self._api_base}/videos?search={encoded_q}&fields=id,title,duration,created_time&limit={limit}"
        try:
            data = await doh_client.get_json(url)
            if isinstance(data, dict) and "list" in data:
                return data["list"]
        except Exception as e:
            logger.warning("Dailymotion search failed for query '%s': %s", query, e)

        return []

    def _extract_dm_stream_sync(self, video_id: str) -> Optional[str]:
        """
        Uses yt-dlp with TLS impersonation (curl-cffi) to bypass Cloudflare anti-bot
        protections and extract direct signed HLS .m3u8 streams from Dailymotion.
        """
        url = f"https://www.dailymotion.com/video/{video_id}"
        try:
            import yt_dlp
            from yt_dlp.networking.impersonate import ImpersonateTarget
            try:
                target = ImpersonateTarget.from_str("chrome")
            except Exception:
                target = None

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "source_address": "0.0.0.0",
                "socket_timeout": 12,
            }
            if target:
                ydl_opts["impersonate"] = target

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                formats = info.get("formats", [])
                hls_fmts = [f for f in formats if "m3u8" in f.get("url", "")]
                if hls_fmts:
                    return hls_fmts[-1].get("url")
                if formats:
                    return formats[-1].get("url")
        except Exception as e:
            logger.warning("yt-dlp extraction failed for Dailymotion %s: %s", video_id, e)
        return None

    async def resolve_stream_url(self, video_id: str) -> Optional[str]:
        """
        Resolves the fresh direct HLS stream URL with a 1-hour in-memory cache
        to prevent duplicate executions on consecutive player requests.
        """
        now = time.time()
        cached = self._manifest_cache.get(video_id)
        if cached and (now - cached[0] < 3600):
            return cached[1]

        stream_url = await asyncio.to_thread(self._extract_dm_stream_sync, video_id)
        if stream_url:
            self._manifest_cache[video_id] = (now, stream_url)

        return stream_url

    async def resolve_and_proxy_manifest(self, video_id: str) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
        """Backward-compatible helper returning (content_bytes, media_type, target_url)."""
        stream_url = await self.resolve_stream_url(video_id)
        if stream_url:
            return None, "application/vnd.apple.mpegurl", stream_url
        return None, None, None

    async def get_highlight_streams(self, match_title: str, base_url: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Searches for highlight videos matching the match title, discards any video
        older than 7 days, and returns Stremio stream items pointing to our resolver.
        """
        search_query = self.clean_search_query(match_title)
        videos = await self.search_videos(search_query, limit=30)
        if not videos:
            return []

        # Strict 7-day freshness filter: discard videos published more than 7 days ago!
        now = time.time()
        one_week_seconds = 7 * 86400  # 604800s

        fresh_candidates = []
        for v in videos:
            created_ts = v.get("created_time", 0)
            # If created_time is known and older than 1 week, skip it completely!
            if created_ts and (now - created_ts > one_week_seconds):
                continue

            # Duration filter: between 90s (1.5m) and 1200s (20m)
            dur = v.get("duration", 0)
            if 90 <= dur <= 1200:
                fresh_candidates.append(v)

        if not fresh_candidates:
            return []

        streams = []
        for vid in fresh_candidates[:2]:
            vid_id = vid.get("id")
            title = vid.get("title", "Highlights")
            duration_mins = vid.get("duration", 0) // 60
            dur_str = f" ({duration_mins} min)" if duration_mins else ""

            endpoint_path = f"/dailymotion/stream/{vid_id}.m3u8"
            stream_url = f"{base_url.rstrip('/')}{endpoint_path}" if base_url else endpoint_path

            streams.append({
                "name": "🎬 Sintesi & Gol (Dailymotion)",
                "title": f"{title}{dur_str}",
                "url": stream_url,
            })

        return streams


dailymotion_service = DailymotionService()