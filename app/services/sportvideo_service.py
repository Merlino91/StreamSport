import asyncio
from datetime import datetime, timezone
import hashlib
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import httpx

logger = logging.getLogger("easysports.sportvideo")

SPORT_URL_MAP = {
    "football": "football.html",
    "soccer": "football.html",
    "calcio": "football.html",
    "basketball": "basketball.html",
    "basket": "basketball.html",
    "american-football": "americanfootball.html",
    "nfl": "americanfootball.html",
    "baseball": "baseball.html",
    "mlb": "baseball.html",
    "hockey": "icehockey.html",
    "ice-hockey": "icehockey.html",
    "nhl": "icehockey.html",
    "rugby": "rugby.html",
    "motor-sports": "other.html",
    "motorsport": "other.html",
    "fight": "other.html",
    "mma": "other.html",
    "boxing": "other.html",
    "tennis": "other.html",
    "other": "other.html",
}

PUBLIC_TRACKERS = [
    "tracker:https://tracker.nekomi.cn:443/announce",
    "tracker:https://tracker.zhuqiy.com:443/announce",
    "tracker:https://tracker.pmman.tech:443/announce",
    "tracker:http://tracker.opentrackr.org:1337/announce",
    "tracker:udp://open.stealth.si:80/announce",
]

class SportVideoService:
    """Scrapes clean full match replays from sport-video.org.ua with strict filtering."""

    def __init__(self):
        self._category_cache: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}
        self._infohash_cache: Dict[str, str] = {}
        self._cache_ttl = 1800  # 30 minutes

    def _normalize_name(self, text: str) -> str:
        s = text.lower()
        s = re.sub(r'\b(fc|cf|ac|as|ss|us|calcio|club|de|the|la|le)\b', '', s)
        s = re.sub(r'[^a-z0-9\s]', ' ', s)
        return ' '.join(s.split())

    def _extract_teams_from_query(self, title: str) -> Tuple[str, str]:
        separators = [" vs ", " vs. ", " - ", " v "]
        for sep in separators:
            if sep in title.lower():
                parts = re.split(re.escape(sep), title, maxsplit=1, flags=re.IGNORECASE)
                if len(parts) == 2:
                    return parts[0].strip(), parts[1].strip()
        return title.strip(), ""

    def _match_teams(self, team1: str, team2: str, candidate_title: str) -> bool:
        if not team1:
            return False
        cand_norm = self._normalize_name(candidate_title)
        cand_tokens = set(cand_norm.split())

        t1_tokens = [t for t in self._normalize_name(team1).split() if len(t) > 2]
        if not t1_tokens:
            t1_tokens = [t for t in self._normalize_name(team1).split() if t]

        t1_match = any(t in cand_tokens or any(t in c for c in cand_tokens) for t in t1_tokens)
        if not t1_match:
            return False

        if not team2:
            return True

        t2_tokens = [t for t in self._normalize_name(team2).split() if len(t) > 2]
        if not t2_tokens:
            t2_tokens = [t for t in self._normalize_name(team2).split() if t]

        t2_match = any(t in cand_tokens or any(t in c for c in cand_tokens) for t in t2_tokens)
        return t2_match

    def _parse_candidate_date(self, title: str) -> Optional[datetime]:
        match = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', title)
        if match:
            try:
                day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
                return datetime(year, month, day, tzinfo=timezone.utc)
            except Exception:
                pass
        return None

    def extract_info_hash(self, torrent_bytes: bytes) -> str:
        """Extracts the 40-character hex SHA1 info_hash from raw .torrent bencoded data."""
        info_marker = b'4:info'
        pos = torrent_bytes.find(info_marker)
        if pos == -1:
            return ""
        info_start = pos + len(info_marker)
        depth = 0
        i = info_start
        while i < len(torrent_bytes):
            c = torrent_bytes[i:i+1]
            if c == b'd' or c == b'l':
                depth += 1
                i += 1
            elif c == b'e':
                depth -= 1
                i += 1
                if depth == 0:
                    info_raw = torrent_bytes[info_start:i]
                    return hashlib.sha1(info_raw).hexdigest()
            elif c == b'i':
                i = torrent_bytes.find(b'e', i) + 1
            elif c.isdigit():
                colon = torrent_bytes.find(b':', i)
                if colon == -1:
                    break
                length = int(torrent_bytes[i:colon])
                i = colon + 1 + length
            else:
                i += 1
        return ""

    async def _fetch_category_matches(self, page_name: str) -> List[Dict[str, str]]:
        now = time.time()
        if page_name in self._category_cache:
            ts, cached = self._category_cache[page_name]
            if now - ts < self._cache_ttl:
                return cached

        url = f"https://www.sport-video.org.ua/{page_name}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        try:
            async with httpx.AsyncClient(headers=headers, timeout=8.0, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"sport-video.org.ua returned {resp.status_code} for {page_name}")
                    return []
                html = resp.text
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            return []

        raw_items = re.findall(
            r'<strong>\s*([A-Za-z0-9\.\- ]+vs[A-Za-z0-9\.\- ]+)\s*</strong>[\s\S]*?href=[\'"]\./([A-Z0-9]+\.html)[\'"]',
            html,
            re.IGNORECASE,
        )

        entries = []
        for title, link in raw_items:
            clean_title = ' '.join(title.split())
            page_link = f"https://www.sport-video.org.ua/{link}"
            entries.append({
                "title": clean_title,
                "url": page_link,
            })

        self._category_cache[page_name] = (now, entries)
        return entries

    async def _get_torrent_infohash(self, match_page_url: str) -> Optional[str]:
        if match_page_url in self._infohash_cache:
            return self._infohash_cache[match_page_url]

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=8.0, follow_redirects=True) as client:
                page_resp = await client.get(match_page_url)
                if page_resp.status_code != 200:
                    return None
                
                torrent_links = re.findall(r'href=[\'"]([^\'"]+\.torrent)[\'"]', page_resp.text)
                if not torrent_links:
                    return None

                torrent_rel = torrent_links[0].lstrip('./')
                torrent_url = f"https://www.sport-video.org.ua/{urllib.parse.quote(torrent_rel)}"

                t_resp = await client.get(torrent_url)
                if t_resp.status_code != 200:
                    return None

                info_hash = self.extract_info_hash(t_resp.content)
                if info_hash:
                    self._infohash_cache[match_page_url] = info_hash
                    return info_hash
        except Exception as e:
            logger.warning(f"Error extracting torrent infohash from {match_page_url}: {e}")

        return None

    async def get_replay_streams(
        self,
        event_title: str,
        category: Optional[str] = None,
        event_date_ms: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Finds clean sports replays for an event with strict matching.
        Returns Stremio streams with infoHash.
        """
        cat_key = (category or "football").lower()
        page_name = SPORT_URL_MAP.get(cat_key, "football.html")

        matches = await self._fetch_category_matches(page_name)
        if not matches:
            return []

        team1, team2 = self._extract_teams_from_query(event_title)
        if not team1:
            return []

        target_date = None
        if event_date_ms and event_date_ms > 0:
            target_date = datetime.fromtimestamp(event_date_ms / 1000, tz=timezone.utc)

        matched_entry = None
        for item in matches:
            cand_title = item["title"]
            if not self._match_teams(team1, team2, cand_title):
                continue

            if target_date:
                cand_date = self._parse_candidate_date(cand_title)
                if cand_date:
                    diff_days = abs((target_date - cand_date).total_seconds()) / 86400
                    if diff_days > 2.5:
                        logger.info(f"Skipping replay candidate due to date mismatch: '{cand_title}' (diff {diff_days:.1f} days)")
                        continue

            matched_entry = item
            break

        if not matched_entry:
            return []

        info_hash = await self._get_torrent_infohash(matched_entry["url"])
        if not info_hash:
            return []

        sources = list(PUBLIC_TRACKERS)
        sources.append(f"dht:{info_hash}")

        cand_title = matched_entry["title"]
        stream_item = {
            "name": "🎬 Full Match [Torrent]",
            "title": f"{cand_title}\nPartita Integrale • 1080p/720p 50fps • Sport-Video",
            "infoHash": info_hash,
            "sources": sources,
            "behaviorHints": {
                "bingeGroup": f"sportvideo-{info_hash[:8]}",
            },
        }
        return [stream_item]

sportvideo_service = SportVideoService()
