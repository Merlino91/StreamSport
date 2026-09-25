import asyncio
import json
import logging
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple
from app.services.doh_client import doh_client

logger = logging.getLogger("easysports.youtube")

PREFERRED_CHANNELS = [
    # Official Italian broadcasters and leagues
    "serie a", "lega serie a",
    "sport mediaset", "mediaset infinity", "mediaset", "pressing",
    "sky sport", "sky sport serie a", "sky sport football", "sky sport 24",
    "dazn italia", "dazn",
    "prime video sport",
    "rai sport", "rai",
    "figc vivo azzurro - nazionale italiana di calcio",
    # European leagues official
    "premier league", "laliga ea sports", "laliga", "bundesliga", "ligue 1", "uefa",
]

COMMON_PREFIXES = r'\b(ac|as|fc|ss|us|cf|hellas|calcio|sporting|club)\b'


class YouTubeService:
    """
    Searches YouTube for official match highlights, prioritizing official
    Italian and international broadcaster/league channels (Serie A, Mediaset,
    Sky Sport, DAZN, etc.) and generates native Stremio stream items.
    """

    def __init__(self):
        self._search_url = "https://www.youtube.com/results?search_query={query}"
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        # In-memory cache for resolved stream manifests (video_id -> (timestamp, content, redirect))
        self._manifest_cache: Dict[str, Tuple[float, Optional[str], Optional[str]]] = {}

    def extract_teams(self, title: str) -> List[str]:
        """Extracts individual team names from match title."""
        cleaned = re.sub(r'\[.*?\]', '', title).strip()
        for sep in [" vs ", " vs. ", " - ", " v "]:
            if sep in cleaned:
                parts = cleaned.split(sep, 1)
                return [parts[0].strip(), parts[1].strip()]
        return [cleaned]

    def clean_search_query(self, title: str) -> str:
        """Cleans match title to optimize YouTube highlight search results in Italian."""
        teams = self.extract_teams(title)
        if len(teams) >= 2:
            return f"{teams[0]} {teams[1]} sintesi gol"
        return f"{teams[0]} highlights"

    def _parse_duration(self, length_text: str) -> int:
        """Parses YouTube length text (e.g. '3:15' or '1:02:40') into seconds."""
        if not length_text:
            return 0
        parts = length_text.split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except ValueError:
            return 0
        return 0

    def _parse_age_days(self, pub_text: str) -> float:
        """Parses publishedTimeText (e.g. '15 ore fa', '2 giorni fa', '3 mesi fa') into approximate age in days."""
        if not pub_text:
            return 0.0
        text = pub_text.lower().strip()

        # Check years
        if any(w in text for w in ["anno", "anni", "year", "years"]):
            match = re.search(r'(\d+)', text)
            years = int(match.group(1)) if match else 1
            return years * 365.0

        # Check months
        if any(w in text for w in ["mese", "mesi", "month", "months"]):
            match = re.search(r'(\d+)', text)
            months = int(match.group(1)) if match else 1
            return months * 30.0

        # Check weeks
        if any(w in text for w in ["settimana", "settimane", "week", "weeks"]):
            match = re.search(r'(\d+)', text)
            weeks = int(match.group(1)) if match else 1
            return weeks * 7.0

        # Check days
        if any(w in text for w in ["giorno", "giorni", "day", "days"]):
            match = re.search(r'(\d+)', text)
            days = int(match.group(1)) if match else 1
            return float(days)

        # Check hours or minutes (extremely fresh)
        if any(w in text for w in ["ora", "ore", "hour", "hours", "minut", "second"]):
            return 0.2

        return 0.0

    def _score_video(self, video: Dict[str, Any], teams: List[str]) -> int:
        """Scores a video based on channel priority, freshness, duration, and title relevance."""
        title = video.get("title", "").lower()
        owner = video.get("channel", "").lower()
        duration_secs = video.get("duration_secs", 0)
        pub_text = video.get("pub_text", "")
        age_days = video.get("age_days", 0.0)

        score = 0

        # Freshness filter: since matches in EasySports are cached for at most 72h (3 days),
        # videos published months or years ago are from past seasons or previous encounters!
        if pub_text:
            if age_days <= 3.0:
                score += 80  # Published within 3 days (matches concluded match cache)
            elif age_days <= 7.0:
                score += 40  # Published within a week
            elif age_days <= 14.0:
                score -= 60  # 2 weeks old
            else:
                # Discard older than 2 weeks (months or years ago)
                score -= 400

        # Preferred channel bonus
        for idx, pref in enumerate(PREFERRED_CHANNELS):
            if pref in owner:
                score += (150 - idx * 5)
                break

        # Team name verification
        if len(teams) >= 2:
            t1_core = re.sub(COMMON_PREFIXES, '', teams[0].lower()).strip()
            t2_core = re.sub(COMMON_PREFIXES, '', teams[1].lower()).strip()
            # If both team core names appear in title, big boost
            t1_found = (t1_core and t1_core in title) or (teams[0].lower() in title)
            t2_found = (t2_core and t2_core in title) or (teams[1].lower() in title)

            if t1_found and t2_found:
                score += 80
            elif t1_found or t2_found:
                score += 20
            else:
                # If neither team is in the title, heavily penalize
                score -= 60

            # Club channel bonus
            if (t1_core and t1_core in owner) or (t2_core and t2_core in owner):
                score += 70

        # Italian highlights keywords bonus
        if any(k in title for k in ["sintesi", "gol", "coppa italia", "serie a", "highlights"]):
            score += 25

        # Highlights duration sweet spot (90s to 15m)
        if 90 <= duration_secs <= 900:
            score += 40
        elif 900 < duration_secs <= 1800:
            score += 10
        elif duration_secs > 1800:
            # Over 30 mins: likely full reaction/podcast
            score -= 50
        elif 0 < duration_secs < 60:
            # Under 60s: YouTube Shorts
            score -= 50

        return score

    async def search_highlight_videos(self, match_title: str, limit: int = 2) -> List[Dict[str, Any]]:
        """
        Searches YouTube and returns up to `limit` best ranked highlight videos
        prioritizing official broadcasters and leagues.
        """
        query = self.clean_search_query(match_title)
        url = self._search_url.format(query=urllib.parse.quote_plus(query))
        teams = self.extract_teams(match_title)

        try:
            data, _ = await doh_client.get_raw(url, timeout=6.0)
            if not data:
                return []

            html = data.decode("utf-8", errors="ignore")
            videos: List[Dict[str, Any]] = []

            # 1. Try parsing structured ytInitialData
            match_data = re.search(r'var ytInitialData = ({.*?});</script>', html) or re.search(r'ytInitialData\s*=\s*({.*?});', html)
            if match_data:
                try:
                    payload = json.loads(match_data.group(1))

                    def find_renderers(obj):
                        if isinstance(obj, dict):
                            if "videoRenderer" in obj:
                                yield obj["videoRenderer"]
                            for v in obj.values():
                                yield from find_renderers(v)
                        elif isinstance(obj, list):
                            for item in obj:
                                yield from find_renderers(item)

                    for r in find_renderers(payload):
                        vid = r.get("videoId")
                        title = r.get("title", {}).get("runs", [{}])[0].get("text", "")
                        owner = r.get("ownerText", {}).get("runs", [{}])[0].get("text", "")
                        length = r.get("lengthText", {}).get("simpleText", "")
                        pub = r.get("publishedTimeText", {}).get("simpleText", "")
                        if vid and title and re.match(r"^[a-zA-Z0-9_-]{11}$", vid):
                            d_sec = self._parse_duration(length)
                            age_d = self._parse_age_days(pub)
                            videos.append({
                                "video_id": vid,
                                "title": title,
                                "channel": owner or "YouTube",
                                "length": length,
                                "duration_secs": d_sec,
                                "pub_text": pub,
                                "age_days": age_d,
                            })
                except Exception as ex:
                    logger.debug("Failed parsing ytInitialData: %s", ex)

            # 2. Fallback to regex extraction if ytInitialData gave nothing
            if not videos:
                vids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
                seen_vids = set()
                for v in vids:
                    if v not in seen_vids:
                        seen_vids.add(v)
                        videos.append({
                            "video_id": v,
                            "title": f"{match_title} Highlights",
                            "channel": "YouTube",
                            "length": "",
                            "duration_secs": 300,
                            "pub_text": "",
                            "age_days": 0.0,
                        })

            if not videos:
                return []

            # Hard filter: discard videos older than 14 days if publication time is known
            fresh_videos = [
                v for v in videos
                if not (v.get("pub_text") and v.get("age_days", 0.0) > 14.0)
            ]
            candidates_pool = fresh_videos if fresh_videos else videos

            # Score and rank all discovered videos
            for v in candidates_pool:
                v["score"] = self._score_video(v, teams)

            filtered = [v for v in candidates_pool if v["score"] > 0]
            candidates = filtered if filtered else candidates_pool
            candidates.sort(key=lambda x: x["score"], reverse=True)

            # Deduplicate by video_id
            unique_results: List[Dict[str, Any]] = []
            seen_ids = set()
            for cand in candidates:
                if cand["video_id"] not in seen_ids:
                    seen_ids.add(cand["video_id"])
                    unique_results.append(cand)
                    if len(unique_results) >= limit:
                        break

            return unique_results
        except Exception as e:
            logger.warning("YouTube highlight search failed for '%s': %s", match_title, e)

        return []

    async def search_highlight_video(self, match_title: str) -> Optional[Dict[str, str]]:
        """Backward-compatible helper returning the single best highlight video."""
        results = await self.search_highlight_videos(match_title, limit=1)
        if results:
            return {"video_id": results[0]["video_id"], "title": results[0]["title"]}
        return None

    def _extract_master_m3u8_sync(self, video_id: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Synchronous yt-dlp master HLS playlist generation executed in threadpool.
        Returns (hls_master_content, redirect_url).
        Prioritizes H.264 video renditions for 100% device & audio decoder compatibility.
        """
        if not video_id or not re.match(r"^[a-zA-Z0-9_-]{11}$", video_id):
            return None, None

        try:
            import yt_dlp
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": False,
                "source_address": "0.0.0.0",
                "socket_timeout": 15,
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android", "web"],
                    }
                },
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                formats = info.get("formats", [])

                # 1. Extract HLS video renditions
                video_formats = [f for f in formats if f.get("protocol") == "m3u8_native" and f.get("vcodec") != "none"]
                video_formats.sort(key=lambda x: x.get("height") or x.get("width") or 0, reverse=True)

                # Prefer H.264 (avc1) for universal hardware playback and audio sync
                h264_videos = [f for f in video_formats if f.get("vcodec", "").startswith("avc1")]
                f_video = h264_videos[0] if h264_videos else (video_formats[0] if video_formats else None)

                # 2. Extract HLS audio rendition
                audio_formats = [f for f in formats if f.get("protocol") == "m3u8_native" and f.get("vcodec") == "none"]
                audio_formats.sort(key=lambda x: x.get("tbr") or x.get("abr") or 0, reverse=True)
                f_audio = audio_formats[0] if audio_formats else None

                if f_video and f_audio:
                    v_url = f_video["url"]
                    a_url = f_audio["url"]
                    res = f_video.get("resolution") or f"{f_video.get('width', 1920)}x{f_video.get('height', 1080)}"
                    master_m3u8 = (
                        "#EXTM3U\n"
                        "#EXT-X-VERSION:3\n"
                        f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Italian Audio",DEFAULT=YES,AUTOSELECT=YES,URI="{a_url}"\n'
                        f'#EXT-X-STREAM-INF:BANDWIDTH=4500000,RESOLUTION={res},CODECS="avc1.64002a,mp4a.40.2",AUDIO="audio"\n'
                        f"{v_url}\n"
                    )
                    return master_m3u8, None

                # Fallback 1: Direct single HLS stream
                if f_video:
                    return None, f_video["url"]

                # Fallback 2: Progressive MP4 (video + audio muxed)
                prog_formats = [
                    f for f in formats
                    if f.get("vcodec") != "none" and f.get("acodec") != "none" and f.get("url")
                ]
                if prog_formats:
                    prog_formats.sort(key=lambda x: x.get("height") or 0, reverse=True)
                    return None, prog_formats[0]["url"]

        except Exception as e:
            logger.warning("yt-dlp stream extraction failed for %s: %s", video_id, e)
        return None, None

    async def resolve_stream_manifest(self, video_id: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Returns (content_str, redirect_url) with 1-hour in-memory cache to prevent
        duplicate yt-dlp executions when Stremio probes the stream.
        """
        now = time.time()
        cached = self._manifest_cache.get(video_id)
        if cached and (now - cached[0] < 3600):
            return cached[1], cached[2]

        content, redirect = await asyncio.to_thread(self._extract_master_m3u8_sync, video_id)
        if content or redirect:
            self._manifest_cache[video_id] = (now, content, redirect)

        return content, redirect

    async def resolve_stream_url(self, video_id: str) -> Optional[str]:
        """Backward compatibility for direct stream resolution."""
        content, redirect = await self.resolve_stream_manifest(video_id)
        return redirect or None

    async def get_highlight_streams(self, match_title: str, base_url: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Returns Stremio stream items for the top official highlight videos.
        """
        videos = await self.search_highlight_videos(match_title, limit=2)
        if not videos:
            return []

        streams = []
        for idx, vid in enumerate(videos):
            vid_id = vid["video_id"]
            title = vid.get("title", f"{match_title} Highlights")
            channel = vid.get("channel", "YouTube")

            endpoint_path = f"/youtube/stream/{vid_id}.m3u8"
            stream_url = f"{base_url.rstrip('/')}{endpoint_path}" if base_url else endpoint_path
            yt_watch_url = f"https://www.youtube.com/watch?v={vid_id}"

            label = f"🎬 Sintesi Ufficiale ({channel} HD)" if idx == 0 else f"🎬 Sintesi ({channel} HD)"

            streams.append({
                "name": label,
                "title": f"{title} (1080p)",
                "ytId": vid_id,
                "url": stream_url,
                "externalUrl": yt_watch_url,
            })

        return streams


youtube_service = YouTubeService()