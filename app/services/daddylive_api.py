import asyncio
import datetime
import html
import json
import logging
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import hashlib
from bs4 import BeautifulSoup
import httpx

from app.services.doh_client import doh_client
from app.services.canonical_competition_service import (
    detect_canonical_competition,
    get_canonical_display_name,
    get_canonical_silo,
    is_redzone_event,
    is_simulcast_event,
    get_simulcast_info,
)

logger = logging.getLogger("streamsport.daddylive")

_MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Sept": 9, "Oct": 10, "Nov": 11, "Dec": 12
}

_ORDINAL_RX = re.compile(r"(\d{1,2})\s*(?:st|nd|rd|th)\b", re.IGNORECASE)


class DaddyLiveAPI:
    """
    Scrapes events natively from DaddyLive schedule (index.php) using DoHClient.
    Bypasses ISP DNS blocking (AGCOM 172.16.0.2 in Italy) via Cloudflare DoH (1.1.1.1).
    Provides 24/7 linear channel mapping and multi-player fallback (Player 1, 2, 5).
    """

    MIRROR_DOMAINS: List[str] = [
        "thedaddy.pk",
        "thedaddy.to",
        "dlhd.dad",
        "dlhd.so",
        "daddylive.pk",
    ]
    DEFAULT_DOMAIN: str = "thedaddy.pk"

    DADDYLIVE_EXCLUDED_CATEGORIES = (
        "tv show", "tv shows", "upcoming events", "upcoming event"
    )

    DADDYLIVE_CANONICAL_CHANNELS: Dict[str, Dict[str, str]] = {
        # Linear Sky Sport IT
        "sky sport uno": {"id": "461", "name": "Sky Sport UNO Italy"},
        "sky sport calcio": {"id": "870", "name": "Sky Sport Calcio Italy"},
        "sky sport tennis": {"id": "576", "name": "Sky Sport Tennis Italy"},
        "sky sport arena": {"id": "462", "name": "Sky Sport Arena Italy"},
        "sky sport basket": {"id": "875", "name": "Sky Sport Basket Italy"},
        "sky sport max": {"id": "460", "name": "Sky Sport MAX Italy"},
        "sky sport f1": {"id": "577", "name": "Sky Sport F1 Italy"},
        "sky sport motogp": {"id": "575", "name": "Sky Sport MotoGP Italy"},
        "sky sport golf": {"id": "574", "name": "Sky Sports Golf Italy"},
        "sky sport 24": {"id": "869", "name": "Sky Sport 24 Italy"},
        # Sky Calcio Contemporanea (LCN 251-254)
        "sky sport 251": {"id": "871", "name": "Sky Calcio 1 (251) Italy"},
        "sky sport 252": {"id": "872", "name": "Sky Calcio 2 (252) Italy"},
        "sky sport 253": {"id": "873", "name": "Sky Calcio 3 (253) Italy"},
        "sky sport 254": {"id": "874", "name": "Sky Calcio 4 (254) Italy"},
        # DAZN & Eurosport
        "dazn 1": {"id": "877", "name": "DAZN ZONA Italy"},
        "eurosport 1": {"id": "878", "name": "EuroSport 1 Italy"},
        "eurosport 2": {"id": "879", "name": "EuroSport 2 Italy"},
        # Rai & Mediaset FTA
        "rai sport": {"id": "882", "name": "Rai Sport Italy"},
        "rai 1": {"id": "850", "name": "Rai 1 Italy"},
        "rai 2": {"id": "851", "name": "Rai 2 Italy"},
        "rai 3": {"id": "852", "name": "Rai 3 Italy"},
        "rai 4": {"id": "853", "name": "Rai 4 Italy"},
        "italia 1": {"id": "854", "name": "Italia 1 Italy"},
        "20 mediaset": {"id": "857", "name": "20 Mediaset Italy"},
    }

    PLAYER_FALLBACK_ORDER: List[Tuple[str, str]] = [
        ("Player 1", "watch.php?id={id}"),
        ("Player 1 Alt", "stream/stream-{id}.php"),
        ("Player 2", "cast/stream-{id}.php"),
        ("Player 3", "watch/stream-{id}.php"),
        ("Player 4", "plus/stream-{id}.php"),
        ("Player 5", "casting/stream-{id}.php"),
        ("Player 6", "player/stream-{id}.php"),
    ]

    def __init__(self):
        self._cache: List[Dict[str, Any]] = []
        self._cache_time: float = 0.0
        self._cache_ttl: int = 3600  # 1 hour default
        self._last_content_hash: str = ""
        self._active_domain: str = self.DEFAULT_DOMAIN
        self._last_domain_check: float = 0.0
        self._active_player_cache: Dict[str, Tuple[str, float]] = {}
        self._fallback_lock: Optional[asyncio.Lock] = None
        self._player6_cache: Dict[str, Tuple[Optional[str], float]] = {}
        self._player6_lock: Optional[asyncio.Lock] = None

    def _get_fallback_lock(self) -> asyncio.Lock:
        if self._fallback_lock is None:
            self._fallback_lock = asyncio.Lock()
        return self._fallback_lock

    def _get_player6_lock(self) -> asyncio.Lock:
        if self._player6_lock is None:
            self._player6_lock = asyncio.Lock()
        return self._player6_lock

    async def get_active_domain(self) -> str:
        """
        Dynamically detects the latest online DaddyLive/DLHD mirror domain
        using the status checker API and direct probe fallback.
        Caches for 6 hours; defaults to 'thedaddy.pk'.
        """
        now = time.time()
        if now - self._last_domain_check < 21600 and self._active_domain:
            return self._active_domain

        # 1. Primary check: DaddyLive status checker API
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'}
            status_url = "https://daddylive.pk/check_status.php?url=https://dlhd.pk"
            async with httpx.AsyncClient(timeout=4.0, headers=headers, follow_redirects=True) as client:
                res = await client.get(status_url)
                if res.status_code == 200:
                    data = res.json()
                    if data.get('status') == 'online':
                        final_url = data.get('final_url') or ""
                        from urllib.parse import urlparse
                        netloc = urlparse(final_url).netloc.lower()
                        if netloc and "dlive.sx" not in netloc and "dlhd.sx" not in netloc:
                            self._active_domain = netloc
                            self._last_domain_check = now
                            logger.info("DaddyLive active domain confirmed: %s", self._active_domain)
                            return self._active_domain
        except Exception as e:
            logger.debug("DaddyLive domain check fallback: %s", e)

        # 2. Secondary check: Probe candidate mirror domains directly
        headers = {'User-Agent': 'Mozilla/5.0'}
        for mirror in self.MIRROR_DOMAINS:
            try:
                async with httpx.AsyncClient(timeout=3.0, headers=headers, follow_redirects=True) as client:
                    res = await client.get(f"https://{mirror}/")
                    if res.status_code < 400:
                        self._active_domain = mirror
                        self._last_domain_check = now
                        logger.info("DaddyLive active domain confirmed via probe: %s", mirror)
                        return mirror
            except Exception:
                continue

        # 3. Final fallback: default primary mirror
        self._active_domain = self.DEFAULT_DOMAIN
        self._last_domain_check = now
        return self._active_domain

    def get_channel_streams(self, canonical_key: str) -> List[Dict[str, Any]]:
        """
        Returns registered DaddyLive 24/7 stream sources for a canonical channel key.
        Provides a single clean channel source; player fallback (Player 1..6) is handled
        automatically on demand.
        """
        if not canonical_key:
            return []
        key = canonical_key.strip().lower()
        info = self.DADDYLIVE_CANONICAL_CHANNELS.get(key)
        if not info:
            return []
        ch_id = info["id"]
        ch_name = info["name"]
        return [
            {
                "source": "dlhd",
                "id": ch_id,
                "name": ch_name,
                "country": "Italy",
            }
        ]

    async def resolve_active_stream_url(
        self,
        ch_id: str,
        ep_url: str,
        ep_pass: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Optional[str]:
        """
        Tests DaddyLive player endpoints in cascade through EasyProxy:
        1. Player 1 (watch.php?id=...)
        2. Player 1 Alt (stream/stream-{id}.php)
        3. Player 2 (cast/stream-{id}.php)
        4. Player 3 (watch/stream-{id}.php)
        5. Player 4 (plus/stream-{id}.php)
        6. Player 5 (casting/stream-{id}.php)
        7. Player 6 (player/stream-{id}.php)
        Returns the first working EasyProxy URL and caches it for 300 seconds.
        """
        now = time.time()
        cached = self._active_player_cache.get(ch_id)
        if cached and cached[1] > now:
            return cached[0]

        lock = self._get_fallback_lock()
        async with lock:
            cached = self._active_player_cache.get(ch_id)
            if cached and cached[1] > now:
                return cached[0]

            dl_domain = await self.get_active_domain()
            base_ep = ep_url.rstrip("/")
            close_client = False
            if client is None:
                client = httpx.AsyncClient(timeout=4.5, verify=False)
                close_client = True

            try:
                for player_label, path_template in self.PLAYER_FALLBACK_ORDER:
                    rel_path = path_template.format(id=ch_id)
                    d_url = f"https://{dl_domain}/{rel_path}"
                    encoded_d = urllib.parse.quote(d_url, safe="")
                    probe_url = f"{base_ep}/extractor/video.m3u8?host=dlstreams&d={encoded_d}&redirect_stream=true"
                    if ep_pass:
                        probe_url += f"&api_password={urllib.parse.quote(ep_pass, safe='')}"

                    try:
                        r = await client.get(probe_url, follow_redirects=False, timeout=4.0)
                        if r.status_code in (200, 302, 307):
                            logger.info(
                                "DaddyLive: Channel %s active on %s (EasyProxy returned %d)",
                                ch_id, player_label, r.status_code
                            )
                            self._active_player_cache[ch_id] = (probe_url, now + 300)
                            return probe_url
                    except Exception as probe_err:
                        logger.debug("DaddyLive: Probe for %s on %s failed: %s", ch_id, player_label, probe_err)

                logger.warning("DaddyLive: All 7 players failed probe for channel %s on EasyProxy; using Player 1 default", ch_id)
                default_url = f"{base_ep}/extractor/video.m3u8?host=dlstreams&d={urllib.parse.quote(f'https://{dl_domain}/watch.php?id={ch_id}', safe='')}&redirect_stream=true"
                if ep_pass:
                    default_url += f"&api_password={urllib.parse.quote(ep_pass, safe='')}"
                return default_url
            finally:
                if close_client:
                    await client.aclose()

    async def check_player6_direct_active(self, ch_id: str) -> bool:
        """
        Lightweight check to verify whether Player 6 (WideIPTV Direct CDN) is active
        and playable for the given DaddyLive channel.
        """
        stream_url = await self.resolve_player6_direct(ch_id)
        return bool(stream_url)

    async def resolve_player6_direct(self, ch_id: str) -> Optional[str]:
        """
        Extracts and resolves unencrypted WideIPTV HLS stream URL from Player 6
        (https://thedaddy.pk/player/stream-{id}.php).
        Returns the direct playable M3U8 URL (e.g. https://cdn.wideiptv.top/.../index.m3u8?token=...)
        which uses refererMode 'omit' and token with 'no_check_ip' parameter (0% VPS video bandwidth).
        Results are cached in RAM for 240 seconds.
        """
        if not ch_id or not str(ch_id).isalnum():
            return None

        now = time.time()
        cached = self._player6_cache.get(ch_id)
        if cached and cached[1] > now:
            return cached[0]

        lock = self._get_player6_lock()
        async with lock:
            cached = self._player6_cache.get(ch_id)
            if cached and cached[1] > now:
                return cached[0]

            dl_domain = await self.get_active_domain()
            page_url = f"https://{dl_domain}/player/stream-{ch_id}.php"

            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": f"https://{dl_domain}/",
                }
                content, _ = await doh_client.get_raw(page_url, headers=headers, timeout=5.0)
                if not content:
                    self._player6_cache[ch_id] = (None, now + 15)
                    return None

                html_text = content.decode("utf-8", errors="ignore")
                m = re.search(r'wideiptv\.top/daddy\.php\?stream=([a-zA-Z0-9_-]+)', html_text)
                if not m:
                    m = re.search(r'wideiptv\.top/player/([a-zA-Z0-9_-]+)', html_text)
                if not m:
                    self._player6_cache[ch_id] = (None, now + 15)
                    return None

                slug = m.group(1)
                player_url = f"https://wideiptv.top/player/{slug}"
                p_headers = {
                    "User-Agent": headers["User-Agent"],
                    "Referer": f"https://wideiptv.top/daddy.php?stream={slug}",
                }
                p_content, _ = await doh_client.get_raw(player_url, headers=p_headers, timeout=5.0)
                if not p_content:
                    self._player6_cache[ch_id] = (None, now + 15)
                    return None

                p_html = p_content.decode("utf-8", errors="ignore")
                s_match = re.search(r'streamUrl:\s*"((?:\\/|[^"])+)"', p_html)
                if not s_match:
                    self._player6_cache[ch_id] = (None, now + 15)
                    return None

                stream_url = s_match.group(1).replace(r"\/", "/")
                logger.info("DaddyLive: Channel %s resolved via Player 6 WideIPTV Direct: %s", ch_id, stream_url[:70])
                self._player6_cache[ch_id] = (stream_url, now + 240)
                return stream_url
            except Exception as e:
                logger.debug("DaddyLive: Player 6 resolution failed for %s: %s", ch_id, e)
                self._player6_cache[ch_id] = (None, now + 15)
                return None

    def _extract_channel_id(self, href: str) -> Optional[str]:
        """Extracts channel id from URL patterns."""
        if not href:
            return None
        m = re.search(r"[?&]id=(\d+)", href)
        if m:
            return m.group(1)
        m = re.search(r"stream-(\d+)\.php", href)
        if m:
            return m.group(1)
        m = re.search(r"watchextra\.php\?id=(\d+)", href)
        if m:
            return f"extra_{m.group(1)}"
        m = re.search(r"watchstreamed\.php\?id=(\d+)", href)
        if m:
            return f"streamed_{m.group(1)}"
        return None

    def _clean_day(self, day: str) -> str:
        s = re.sub(r"\s*-\s*Schedule Time UK GMT\s*$", "", day, flags=re.IGNORECASE)
        s = s.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2009", " ").replace("\u200b", "")
        s = _ORDINAL_RX.sub(r"\1", s)
        return re.sub(r"\s+", " ", s).strip()

    def _parse_datetime(self, day_str: str, time_str: str) -> int:
        """Parses day string and time string into UTC epoch ms."""
        clean = self._clean_day(day_str)
        parts = clean.split()
        month = daynum = year = None

        if len(parts) >= 4:
            if parts[1] in _MONTHS:
                month = _MONTHS.get(parts[1])
                try: daynum = int(parts[2])
                except: pass
                try: year = int(parts[3])
                except: pass
            elif parts[2] in _MONTHS:
                try: daynum = int(parts[1])
                except: pass
                month = _MONTHS.get(parts[2])
                try: year = int(parts[3])
                except: pass

        now = datetime.datetime.now(datetime.timezone.utc)
        month = month or now.month
        daynum = daynum or now.day
        year = year or now.year

        try:
            h, m = map(int, time_str.split(":"))
        except Exception:
            h, m = 0, 0

        # Broadcast day runs from 06:00 to 05:59 next day.
        # Events scheduled at 00:00 - 05:59 belong to the following calendar morning!
        day_offset = 1 if h < 6 else 0
        base_date = datetime.date(year, month, daynum)
        if day_offset:
            base_date += datetime.timedelta(days=day_offset)

        # DaddyLive schedule times are explicitly published in UK GMT / UTC ("Schedule Time UK GMT")
        dt = datetime.datetime(base_date.year, base_date.month, base_date.day, h, m, tzinfo=datetime.timezone.utc)
        return int(dt.timestamp() * 1000)

    def _clean_title(self, raw_title: str) -> str:
        # Unescape HTML entities (&amp; -> &)
        t = html.unescape(raw_title or "")

        if is_simulcast_event(t):
            info = get_simulcast_info(t)
            if info:
                return info["title"]

        # 1. Strip time and live markers
        t = re.sub(r"^\s*🔴\s*Inizio\s*:\s*\d{1,2}:\d{2}\s*[-:]?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"^\s*\d{1,2}:\d{2}\s*(?:[:\-–]\s*)?", "", t)

        # 2. Extract competitor match if separated by colon (e.g. "🎾 ATP - Singles: 🇦🇷 Sebastian Baez vs 🇺🇸 Jenson Brooksby")
        if ":" in t:
            prefix_part, match_part = t.rsplit(":", 1)
            # Strip leading decorative emojis from prefix (🎾, ⚽, 🏀, etc.) but preserve flag emojis
            prefix_clean = re.sub(r"^[\s\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U00002600-\U000027BF]+", "", prefix_part).strip()
            prefix_clean = re.sub(r"\s+", " ", prefix_clean).strip()
            match_clean = re.sub(r"\s+", " ", match_part).strip()

            # If match has competitors ('vs', ' - ', ' v ', or ' / ')
            if any(sep in match_clean for sep in (" vs ", " - ", " v ", " / ")):
                # If prefix is a well-known league (Serie A, Premier League, etc.), drop the prefix to keep title clean
                if any(l in prefix_clean.lower() for l in ("serie a", "serie b", "premier league", "liga", "bundesliga", "ligue 1")):
                    return match_clean
                # For tennis or cup tournaments, put competitors first: "Competitors (Tournament)"
                if prefix_clean:
                    return f"{match_clean} ({prefix_clean})"
                return match_clean
            return f"{match_clean} ({prefix_clean})" if prefix_clean else match_clean

        # Fallback: remove leading decorative icons
        t = re.sub(r"^[\s\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U00002600-\U000027BF]+", "", t).strip()
        return re.sub(r"\s+", " ", t).strip()

    @classmethod
    def map_daddylive_category_to_silo(cls, cat_raw: str, clean_title: str) -> Optional[Tuple[str, str]]:
        """
        Maps a DaddyLive raw category string and title to (silo_id, sport_category).
        Keeps all sports categories in memory; only non-sports are excluded.
        """
        cat_lower = (cat_raw or "").lower()
        if any(k in cat_lower for k in cls.DADDYLIVE_EXCLUDED_CATEGORIES):
            return None

        title_lower = (clean_title or "").lower()

        # 1. Football (Soccer)
        if any(k in cat_lower for k in (
            "soccer", "championship", "league one", "league two", "national league", "mls", "usl",
            "youth league", "champions league", "europa league", "conference league",
            "serie a", "serie b", "serie c", "la liga", "bundesliga", "ligue 1", "premier league",
            "eredivisie", "coppa italia", "fa cup", "copa del rey", "copa libertadores", "copa sudamericana"
        )):
            return "football", "football"
        if "football" in cat_lower and "american" not in cat_lower and "am." not in cat_lower and "cfl" not in cat_lower and "college football" not in cat_lower:
            return "football", "football"

        # 2. Tennis
        if "tennis" in cat_lower:
            return "tennis", "tennis"

        # 3. Motorsport
        if "motor" in cat_lower or "formula" in cat_lower or "motogp" in cat_lower:
            return "motor-sports", "motor-sports"

        # 4. Basketball
        if any(k in cat_lower for k in ("basket", "nba", "wnba", "eurocup", "euroleague", "aba")):
            return "basketball", "basketball"

        # 5. Combat / Fight
        if any(k in cat_lower for k in ("boxing", "mma", "ufc", "wrestling")):
            return "fight", "fight"

        # 6. Baseball
        if "baseball" in cat_lower or "mlb" in cat_lower:
            return "baseball", "baseball"

        # 7. American Football (NFL, NCAA FBS, CFL, UFL, College Football)
        if any(k in cat_lower for k in ("am. football", "american football", "cfl", "nfl", "ufl", "college football", "ncaaf", "ncaa")):
            return "american-football", "american-football"

        # 8. Hockey (NHL, KHL, ECHL, European Leagues, Field Hockey)
        if "hockey" in cat_lower or "hockey" in title_lower or any(k in cat_lower for k in ("nhl", "khl", "echl", "shl", "ohl", "ushl")):
            return "hockey", "hockey"

        # 9. Volleyball
        if "volleyball" in cat_lower or "volley" in cat_lower:
            return "volley", "volleyball"

        # 10. Altri Sport
        if "golf" in cat_lower or "golf" in title_lower:
            return "altri_sport", "golf"
        if "rugby" in cat_lower:
            return "altri_sport", "rugby"
        if "cricket" in cat_lower:
            return "altri_sport", "cricket"
        if "darts" in cat_lower:
            return "altri_sport", "darts"
        if ("cycling" in cat_lower or "ciclismo" in title_lower) and "hockey" not in title_lower:
            return "altri_sport", "cycling"
        if "handball" in cat_lower or "pallamano" in title_lower:
            return "altri_sport", "handball"
        if "futsal" in cat_lower:
            return "altri_sport", "futsal"
        if "floorball" in cat_lower:
            return "altri_sport", "floorball"
        if "water polo" in cat_lower or "pallanuoto" in title_lower:
            return "altri_sport", "water polo"
        if "squash" in cat_lower:
            return "altri_sport", "squash"
        if "afl" in cat_lower or "aussie" in cat_lower:
            return "altri_sport", "afl"
        if "billiards" in cat_lower or "snooker" in cat_lower or "snooker" in title_lower:
            return "altri_sport", "billiards"

        return "altri_sport", "other"

    async def _fetch_native_html(self) -> Optional[str]:
        """
        Fetches raw schedule HTML from DaddyLive with automatic mirror failover via DoHClient.
        Bypasses ISP DNS blocking (AGCOM 172.16.0.2 in Italy) using Cloudflare DoH (1.1.1.1).
        """
        for mirror in self.MIRROR_DOMAINS:
            url = f"https://{mirror}/index.php"
            try:
                raw_bytes, ct = await doh_client.get_raw(url, timeout=8.0)
                if raw_bytes and len(raw_bytes) > 5000:
                    html_str = raw_bytes.decode("utf-8", errors="ignore")
                    if "schedule__category" in html_str or "schedule__dayTitle" in html_str:
                        self._active_domain = mirror
                        logger.debug("Successfully fetched DaddyLive schedule from %s (%d bytes)", mirror, len(raw_bytes))
                        return html_str
            except Exception as e:
                logger.debug("DaddyLive schedule fetch failed for %s: %s", mirror, e)
                continue
        logger.warning("DaddyLive schedule fetch failed across all %d mirrors", len(self.MIRROR_DOMAINS))
        return None

    def parse_html_schedule(self, html_str: str) -> List[Dict[str, Any]]:
        """Parses DaddyLive index.php HTML into standard match dictionaries."""
        soup = BeautifulSoup(html_str, "html.parser")
        sections = soup.find_all("section")
        matches: List[Dict[str, Any]] = []
        now_ms = int(time.time() * 1000)

        for sec in sections:
            day_title_el = sec.find(class_=lambda c: c and "dayTitle" in c)
            if not day_title_el:
                continue
            day_str = day_title_el.get_text(strip=True)

            categories = sec.find_all(class_=lambda c: c and "schedule__category" in c)
            for cat in categories:
                cat_meta_el = cat.find(class_=lambda c: c and "meta" in c)
                cat_raw = cat_meta_el.get_text(strip=True) if cat_meta_el else ""
                cat_lower = cat_raw.lower()

                # Skip non-sports categories & duplicate live section
                if any(k in cat_lower for k in self.DADDYLIVE_EXCLUDED_CATEGORIES):
                    continue

                events = cat.find_all(class_=lambda c: c and "schedule__event" in c)
                for ev in events:
                    title_el = ev.find(class_=lambda c: c and "eventTitle" in c)
                    raw_title = title_el.get_text(strip=True) if title_el else ""
                    if not raw_title:
                        continue

                    time_el = ev.find(class_=lambda c: c and "time" in c)
                    time_str = "00:00"
                    if time_el:
                        time_str = time_el.get("data-time") or time_el.get_text(strip=True) or "00:00"

                    channels_el = ev.find(class_=lambda c: c and "channels" in c)
                    sources = []
                    if channels_el:
                        for a in channels_el.find_all("a", href=True):
                            ch_name = a.get_text(strip=True)
                            href = a.get("href", "").strip()
                            ch_id = self._extract_channel_id(href)
                            if (
                                ch_id
                                and ch_id not in ("0", "00")
                                and "channel not listed" not in ch_name.lower()
                                and "just ask in chat" not in ch_name.lower()
                            ):
                                sources.append({
                                    "source": "dlhd",
                                    "id": ch_id,
                                    "name": ch_name
                                })

                    if not sources:
                        continue

                    date_ms = self._parse_datetime(day_str, time_str)

                    # Discard events scheduled more than 3 days ahead
                    if date_ms and date_ms > (now_ms + 3 * 24 * 3600 * 1000):
                        continue

                    clean_title = self._clean_title(raw_title)

                    # Detect women's event flag
                    is_women = bool(re.search(r"\b(women(?:['’]s)?|femminile|femenil|damen|ladies|wta|wnba|uwcl)\b", f"{cat_raw} {raw_title}", re.IGNORECASE))

                    # Check simulcast / whip-around show
                    sim_info = get_simulcast_info(raw_title, cat_raw) or get_simulcast_info(clean_title, cat_raw)
                    teams = None
                    if sim_info:
                        clean_title = sim_info["title"]
                        silo_id = sim_info["silo"]
                        category = sim_info["silo"]
                        canon = sim_info["canonical"]
                        comp_disp = sim_info["competition"]
                    else:
                        if " vs " in clean_title:
                            title_for_teams = re.sub(r"\s*\([^)]*\)$", "", clean_title)
                            parts = title_for_teams.split(" vs ", 1)
                            home_raw = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", parts[0]).rsplit(":", 1)[-1].strip()
                            away_raw = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", parts[1]).split("|", 1)[0].strip()
                            teams = {
                                "home": {"name": home_raw},
                                "away": {"name": away_raw}
                            }
                        silo_info = self.map_daddylive_category_to_silo(cat_raw, clean_title)
                        if not silo_info:
                            continue
                        silo_id, category = silo_info
                        canon = detect_canonical_competition(f"{cat_raw} : {raw_title}", silo_hint=silo_id)
                        if canon and category in ("other", "unknown", ""):
                            canon_silo = get_canonical_silo(canon)
                            if canon_silo:
                                silo_id = canon_silo
                                category = "football" if canon_silo == "football" else canon_silo
                        comp_disp = get_canonical_display_name(canon) or cat_raw

                    slug = re.sub(r"[^a-z0-9]+", "-", clean_title.lower()).strip("-")[:60]
                    match_id = f"dlhd-{slug}-{str(date_ms)[-6:]}"

                    match_dict: Dict[str, Any] = {
                        "id": match_id,
                        "title": clean_title,
                        "category": category,
                        "canonical_competition": canon,
                        "competition": comp_disp,
                        "_competition": comp_disp,
                        "_silo": silo_id,
                        "date": date_ms,
                        "poster": None,
                        "popular": False,
                        "teams": teams,
                        "sources": sources,
                    }
                    if is_women:
                        match_dict["is_women"] = True

                    matches.append(match_dict)

        return matches

    async def get_matches(self, force: bool = False) -> List[Dict[str, Any]]:
        now = time.time()
        if not force and self._cache and (now - self._cache_time < self._cache_ttl):
            return self._cache

        html_str = await self._fetch_native_html()
        if not html_str:
            logger.warning("DaddyLive: Failed to fetch HTML schedule from all mirrors; using cached events (%d)", len(self._cache))
            return self._cache

        new_content_hash = hashlib.md5(html_str.encode("utf-8")).hexdigest()
        if not force and new_content_hash == self._last_content_hash and self._cache:
            self._cache_time = now
            logger.debug("DaddyLive schedule unchanged (hash %s)", new_content_hash[:8])
            return self._cache

        matches = self.parse_html_schedule(html_str)
        if matches:
            self._cache = matches
            self._cache_time = now
            self._last_content_hash = new_content_hash

        # Cache Invalidation: check upcoming events for today
        now_ms = time.time() * 1000
        future_count = sum(1 for m in self._cache if (m.get("date") or 0) > now_ms)
        if future_count < 5:
            self._cache_ttl = 900  # 15 minutes
        else:
            self._cache_ttl = 3600  # 1 hour

        logger.info("Parsed %d matches from DaddyLive native schedule (TTL: %ds, hash: %s)", len(self._cache), self._cache_ttl, self._last_content_hash[:8])
        return self._cache


daddylive_api = DaddyLiveAPI()
