from __future__ import annotations
import asyncio
from dataclasses import dataclass
import ipaddress
import logging
import re
import time
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional, Set
import httpx

from app.config import ENABLE_TVVOO, TVVOO_CACHE_INTERVAL

logger = logging.getLogger("streamsport.tvvoo_service")


def sanitize_ip(ip_str: Optional[str]) -> Optional[str]:
    """
    Validates and returns normalized public/routable IPv4 or IPv6 string,
    or None if invalid, spoofed, private (LAN/Docker), loopback, or non-global.
    """
    if not ip_str or not isinstance(ip_str, str):
        return None
    candidate = ip_str.split(",")[0].strip()
    try:
        ip = ipaddress.ip_address(candidate)
        # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:93.184.216.34 -> 93.184.216.34)
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped

        # Discard private LAN, loopback, link-local, multicast, reserved or non-global IPs
        if (
            not ip.is_global
            or ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return None

        return str(ip)
    except ValueError:
        return None


@dataclass
class LiveStreamSession:
    play_url: str
    client_ip: Optional[str]
    stream_url: str
    base_url: str
    valid_until: float  # Unix timestamp in seconds
    last_fetched_at: float
    last_manifest: Optional[str] = None
    last_manifest_time: float = 0.0
    refresh_task: Optional[asyncio.Task] = None


class LiveManifestManager:
    """
    No-Freeze Live Manifest Manager for Vavoo Streams.
    Acts as a zero-video-bandwidth manifest proxy:
    - Rewrites relative .ts/.m4s video chunks into absolute URLs pointing directly to Vavoo CDN edge servers (0% video bandwidth on our server).
    - Proactively renews temporary Vavoo tokens in the background when < 5 mins remain, without interrupting player playback.
    - Throttles rapid repeated manifest requests (< 1.5s).
    - Guards against player buffer stalls: on transient 401/403 or network errors, serves the recent manifest (< 10s) while asynchronously re-resolving tokens.
    - Enforces hard LRU bounds (MAX_SESSIONS = 1000) to prevent memory leaks from uncapped concurrent sessions.
    """

    MAX_SESSIONS: int = 1000

    def __init__(self, service: "TvVooService"):
        self.service = service
        self.sessions: Dict[str, LiveStreamSession] = {}
        self._in_flight: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._last_cleanup: float = 0.0
        # Persistent pooled client for live HLS upstream polling to prevent socket TIME_WAIT exhaustion
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(4.0, connect=2.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=30.0),
            headers={
                "User-Agent": "VAVOO/2.6",
                "Accept": "*/*",
            }
        )

    async def close(self):
        """Closes the underlying HTTP client."""
        if hasattr(self, "_client") and self._client and not self._client.is_closed:
            await self._client.aclose()

    def _get_key(self, play_url: str, client_ip: Optional[str]) -> str:
        valid_ip = sanitize_ip(client_ip)
        return f"{play_url}:{valid_ip or 'default'}"

    def cleanup(self) -> None:
        """Periodic cleanup of sessions stale for > 45 minutes or exceeding max capacity."""
        now = time.time()
        if (now - self._last_cleanup < 300) and len(self.sessions) <= self.MAX_SESSIONS:
            return
        self._last_cleanup = now
        stale_keys = [k for k, s in list(self.sessions.items()) if (now - s.last_fetched_at) > 2700]  # 45 minutes
        for k in stale_keys:
            self.sessions.pop(k, None)

        # Enforce LRU cap if sessions exceed MAX_SESSIONS
        if len(self.sessions) > self.MAX_SESSIONS:
            excess = len(self.sessions) - self.MAX_SESSIONS
            oldest_keys = sorted(self.sessions.keys(), key=lambda k: self.sessions[k].last_fetched_at)[:excess]
            for k in oldest_keys:
                self.sessions.pop(k, None)

    async def get_live_manifest(self, play_url: str, client_ip: Optional[str] = None) -> Optional[str]:
        key = self._get_key(play_url, client_ip)
        now = time.time()
        task_to_await = None

        async with self._lock:
            self.cleanup()
            session = self.sessions.get(key)
            if session and session.valid_until > now:
                session.last_fetched_at = now
                if (session.valid_until - now) < 300 and (not session.refresh_task or session.refresh_task.done()):
                    # Proactive non-blocking background token renewal when < 5 mins left
                    session.refresh_task = asyncio.create_task(self._refresh_session(session, play_url, client_ip))
            else:
                # Initial resolution or token renewal required; coalesce concurrent requests
                if key in self._in_flight and not self._in_flight[key].done():
                    task_to_await = self._in_flight[key]
                else:
                    task_to_await = asyncio.create_task(self._refresh_session(session, play_url, client_ip))
                    self._in_flight[key] = task_to_await

        if task_to_await:
            try:
                session = await task_to_await
            finally:
                async with self._lock:
                    if self._in_flight.get(key) is task_to_await:
                        self._in_flight.pop(key, None)

        if not session:
            return None

        async with self._lock:
            session.last_fetched_at = now

        # 3. Fast throttle for rapid repeated manifest requests (< 1.5 seconds)
        if session.last_manifest and (now - session.last_manifest_time) < 1.5:
            return session.last_manifest

        # 4. Fetch upstream playlist
        try:
            raw_m3u8 = await self._fetch_upstream(session.stream_url)
        except Exception as exc:
            logger.warning("[LiveManifest] Upstream fetch issue (%s) for %s", exc, play_url[:80])
            # If we have an existing recent manifest (< 25 seconds), serve immediately to prevent player buffer stall!
            if session.last_manifest and (now - session.last_manifest_time) < 25.0:
                if any(err in str(exc).lower() for err in ("401", "403", "auth")):
                    asyncio.create_task(self._refresh_session(session, play_url, client_ip))
                return session.last_manifest

            # Synchronous refresh
            session = await self._refresh_session(session, play_url, client_ip)
            if not session:
                return None
            raw_m3u8 = await self._fetch_upstream(session.stream_url)

        rewritten = self._rewrite_manifest(raw_m3u8, session.base_url)
        async with self._lock:
            session.last_manifest = rewritten
            session.last_manifest_time = time.time()
        return rewritten

    async def _fetch_upstream(self, stream_url: str) -> str:
        res = await self._client.get(stream_url)
        if res.status_code in (401, 403):
            raise RuntimeError(f"Upstream auth error: {res.status_code}")
        if res.status_code != 200:
            raise RuntimeError(f"Upstream error: {res.status_code}")
        return res.text

    async def _refresh_session(self, current: Optional[LiveStreamSession], play_url: str, client_ip: Optional[str]) -> Optional[LiveStreamSession]:
        resolved = await self.service.resolve_clean_stream_url(play_url, client_ip)
        if not resolved or not resolved.get("url"):
            logger.warning("[LiveManifest] Failed to resolve Vavoo stream URL for %s", play_url[:80])
            return current

        stream_url = resolved["url"]
        last_slash = stream_url.rfind("/")
        base_url = stream_url[:last_slash + 1] if last_slash != -1 else stream_url
        valid_until = resolved.get("valid_until", time.time() + 1200)

        updated = LiveStreamSession(
            play_url=play_url,
            client_ip=client_ip,
            stream_url=stream_url,
            base_url=base_url,
            valid_until=valid_until,
            last_fetched_at=time.time(),
            last_manifest=current.last_manifest if current else None,
            last_manifest_time=current.last_manifest_time if current else 0.0,
            refresh_task=None,
        )
        key = self._get_key(play_url, client_ip)
        async with self._lock:
            self.sessions[key] = updated
            if len(self.sessions) > self.MAX_SESSIONS:
                excess = len(self.sessions) - self.MAX_SESSIONS
                oldest_keys = sorted(self.sessions.keys(), key=lambda k: self.sessions[k].last_fetched_at)[:excess]
                for k in oldest_keys:
                    self.sessions.pop(k, None)
        logger.info("[LiveManifest] Session refreshed for %s (valid for ~%ds)", play_url[:60], int(valid_until - time.time()))
        return updated

    def _rewrite_manifest(self, raw: str, base_url: str) -> str:
        lines = raw.splitlines()
        out_lines = []
        for line in lines:
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#") or trimmed.startswith("http://") or trimmed.startswith("https://"):
                out_lines.append(line)
            else:
                out_lines.append(urllib.parse.urljoin(base_url, trimmed))
        return "\n".join(out_lines)


class TvVooService:
    """
    Manages live international sports channels from the Vavoo / TvVoo network
    organized into strict national silos (Italy, Spain, Germany, France, United Kingdom)
    with official LCN channel numbering and strict numeric matching.
    Refreshes channel mappings every 6 hours (configurable via TVVOO_CACHE_INTERVAL).
    """

    PING_URL = "https://www.vypn.net/api/app/ping"
    DEFAULT_DOMAIN = "vavoo.to"
    DOMAIN_MIRRORS: List[str] = ["vavoo.to", "huhu.to", "kool.to", "oha.to"]

    # Supported Vavoo country groups for sports broadcasting
    TVVOO_COUNTRY_GROUPS: List[str] = ["Italy", "Spain", "Germany", "France", "United Kingdom"]

    # Country suffix mapping for recognizing geographic broadcasts
    COUNTRY_SUFFIX_MAP: Dict[str, str] = {
        "it": "Italy",
        "italy": "Italy",
        "italia": "Italy",
        "de": "Germany",
        "germany": "Germany",
        "deutschland": "Germany",
        "es": "Spain",
        "spain": "Spain",
        "espana": "Spain",
        "fr": "France",
        "france": "France",
        "uk": "United Kingdom",
        "gb": "United Kingdom",
    }

    # Unsupported countries that must NOT fall back to European/Italian channels
    UNSUPPORTED_COUNTRY_SUFFIXES: Set[str] = {
        "ca", "canada",
        "us", "usa",
        "br", "brazil", "brasil",
        "nl", "netherlands",
        "pl", "poland", "polska",
        "ro", "romania",
        "hr", "croatia",
        "rs", "serbia",
        "ba", "bih", "bosnia",
        "me", "mne", "montenegro",
        "si", "slovenia",
        "mk", "mkd", "macedonia",
        "gr", "greece",
        "cy", "cyprus",
        "il", "israel",
        "bg", "bulgaria",
        "al", "albania",
        "az", "azerbaijan",
        "pt", "portugal",
        "dk", "denmark",
        "se", "sweden",
        "no", "norway",
        "cz", "czech",
        "sk", "slovakia",
        "hu", "hungary",
        "tr", "turkey",
        "ru", "russia",
        "ua", "ukraine",
        "ar", "argentina",
        "mx", "mexico",
        "au", "australia",
        "nz", "new zealand",
        "za", "south africa",
    }

    # Direct mapping for Sky Italia Guida TV API (apid.sky.it) channel IDs
    SKY_CHANNEL_IDS: Dict[str, str] = {
        "9094": "sky sport 24",       # LCN 200
        "9097": "sky sport uno",      # LCN 201
        "9113": "sky sport calcio",   # LCN 202
        "11237": "sky sport tennis",  # LCN 203
        "9093": "sky sport arena",    # LCN 204
        "9116": "sky sport basket",   # LCN 205
        "9103": "sky sport max",      # LCN 206
        "9096": "sky sport f1",       # LCN 207
        "9102": "sky sport motogp",   # LCN 208
        "10254": "sky sport golf",    # LCN 209
        "11910": "sky sport legend",  # LCN 210
        "11909": "sky sport mix",     # LCN 211
        "6000": "supertennis",        # LCN 212
        "10013": "sky sport 4k",      # LCN 213
        "11402": "dazn 1",            # LCN 214
        "11401": "dazn 2",            # LCN 215
        "11849": "dazn 3",            # LCN 216
        "11850": "dazn 4",            # LCN 217
        "807": "rai sport",           # LCN 227
        "7587": "sport tv",           # LCN 228
        "9513": "milan tv",           # LCN 230
        "9893": "inter tv",           # LCN 232
        "11497": "sky sport 251",     # LCN 251
        "11496": "sky sport 252",     # LCN 252
        "11494": "sky sport 253",     # LCN 253
        "11490": "sky sport 254",     # LCN 254
        "11491": "sky sport 255",     # LCN 255
        "11492": "sky sport 256",     # LCN 256
        "11386": "sky sport 257",     # LCN 257
        "9046": "sky sport 258",      # LCN 258
        "8613": "sky sport 259",      # LCN 259
        "899": "rai 1",               # LCN 101
        "898": "rai 2",               # LCN 102
        "897": "rai 3",               # LCN 103
    }

    # Direct mapping for Tivùsat EPG API (services.tivulaguida.it) channel IDs
    TIVUSAT_CHANNEL_IDS: Dict[str, str] = {
        "42": "rai 1",                # Rai 1 HD
        "43": "rai 2",                # Rai 2 HD
        "44": "rai 3",                # Rai 3 HD
        "88": "rai sport",            # Rai Sport HD
        "145": "canale 5",            # Canale 5 HD
        "148": "italia 1",            # Italia 1 HD
        "249": "20 mediaset",         # 20 Mediaset HD
        "279": "tv8",                 # TV8 HD
        "280": "cielo",               # Cielo HD
        "295": "sportitalia solocalcio", # SOLOCALCIO (Sportitalia)
        "32": "dmax",                 # DMAX HD
    }

    # Country-specific alias mapping (Silos) including official LCN numbering
    COUNTRY_ALIASES: Dict[str, Dict[str, str]] = {
        "Italy": {
            # Sky Sport 24 (LCN 200)
            "sky sport 24": "sky sport 24",
            "sky sports 24": "sky sport 24",
            "sky sport24": "sky sport 24",
            "ss24": "sky sport 24",
            "sky sport 200": "sky sport 24",
            "sky 200": "sky sport 24",
            "200": "sky sport 24",

            # Sky Sport Uno (LCN 201)
            "sky sport uno": "sky sport uno",
            "sky sport 1": "sky sport uno",
            "sky sport 1 hd": "sky sport uno",
            "sky 1": "sky sport uno",
            "sky sport 201": "sky sport uno",
            "sky 201": "sky sport uno",
            "201": "sky sport uno",

            # Sky Sport Calcio (LCN 202)
            "sky sport calcio": "sky sport calcio",
            "sky sport calcio hd": "sky sport calcio",
            "sky calcio": "sky sport calcio",
            "sky sport 202": "sky sport calcio",
            "sky 202": "sky sport calcio",
            "202": "sky sport calcio",

            # Sky Sport Tennis (LCN 203)
            "sky sport tennis": "sky sport tennis",
            "sky sport tennis hd": "sky sport tennis",
            "sky tennis": "sky sport tennis",
            "sky sport 203": "sky sport tennis",
            "sky 203": "sky sport tennis",
            "203": "sky sport tennis",

            # Sky Sport Arena (LCN 204)
            "sky sport arena": "sky sport arena",
            "sky sport arena hd": "sky sport arena",
            "sky arena": "sky sport arena",
            "sky sport 204": "sky sport arena",
            "sky 204": "sky sport arena",
            "204": "sky sport arena",

            # Sky Sport Golf (LCN 205)
            "sky sport golf": "sky sport golf",
            "sky sport golf hd": "sky sport golf",
            "sky golf": "sky sport golf",
            "sky sport 205": "sky sport golf",
            "sky 205": "sky sport golf",
            "205": "sky sport golf",

            # Sky Sport Max (LCN 206)
            "sky sport max": "sky sport max",
            "sky sport max hd": "sky sport max",
            "sky max": "sky sport max",
            "sky sport 206": "sky sport max",
            "sky 206": "sky sport max",
            "206": "sky sport max",

            # Sky Sport F1 (LCN 207)
            "sky sport f1": "sky sport f1",
            "sky sport f1 hd": "sky sport f1",
            "sky f1": "sky sport f1",
            "sky sports f1": "sky sport f1",
            "sky sport 207": "sky sport f1",
            "sky 207": "sky sport f1",
            "207": "sky sport f1",

            # Sky Sport MotoGP (LCN 208)
            "sky sport motogp": "sky sport motogp",
            "sky sport moto gp": "sky sport motogp",
            "sky sport motogp hd": "sky sport motogp",
            "sky motogp": "sky sport motogp",
            "sky sport 208": "sky sport motogp",
            "sky 208": "sky sport motogp",
            "208": "sky sport motogp",

            # Sky Sport NBA (LCN 209)
            "sky sport nba": "sky sport nba",
            "sky sport nba hd": "sky sport nba",
            "sky nba": "sky sport nba",
            "sky sport 209": "sky sport nba",
            "sky 209": "sky sport nba",
            "209": "sky sport nba",

            # Sky Sport Mix & Serie A
            "sky sport mix": "sky sport mix",
            "sky sport serie a": "sky sport serie a",

            # Sky Sport Event Feed (Dedicated separate feed, NOT collapsed into Uno!)
            "sky sport": "sky sport",
            "sky sport it": "sky sport",
            "sky sport eventi": "sky sport",

            # Eurosport (LCN 210, 211)
            "eurosport": "eurosport 1",
            "eurosport 1": "eurosport 1",
            "eurosport 1 hd": "eurosport 1",
            "eurosport 210": "eurosport 1",
            "sky 210": "eurosport 1",
            "sky sport 210": "eurosport 1",
            "210": "eurosport 1",
            "eurosport 2": "eurosport 2",
            "eurosport 2 hd": "eurosport 2",
            "eurosport 211": "eurosport 2",
            "sky 211": "eurosport 2",
            "sky sport 211": "eurosport 2",
            "211": "eurosport 2",

            # SuperTennis (LCN 212) - Linear 24/7 Channel
            "supertennis": "supertennis",
            "super tennis": "supertennis",
            "supertennis hd": "supertennis",
            "sky super tennis": "supertennis",
            "supertennis 212": "supertennis",
            "sky 212": "supertennis",
            "sky sport 212": "supertennis",
            "212": "supertennis",

            # SuperTennis OTT Interactive Feeds (isolated from linear H24)
            "supertennis 1": "supertennis 1",
            "super tennis 1": "supertennis 1",
            "supertennis 2": "supertennis 2",
            "super tennis 2": "supertennis 2",
            "supertennis 3": "supertennis 3",
            "super tennis 3": "supertennis 3",
            "supertennis 4": "supertennis 4",
            "super tennis 4": "supertennis 4",

            # DAZN Italia (LCN 214, 215)
            # NOTE: Generic unnumbered 'dazn' / 'dazn it' represents the OTT streaming platform, NOT linear DAZN 1!
            "dazn 1": "dazn 1",
            "dazn 1 it": "dazn 1",
            "dazn 1 hd": "dazn 1",
            "zona dazn": "dazn 1",
            "zona dazn it": "dazn 1",
            "dazn zona": "dazn 1",
            "dazn zona it": "dazn 1",
            "zona dazn 214": "dazn 1",
            "dazn 214": "dazn 1",
            "sky 214": "dazn 1",
            "sky sport 214": "dazn 1",
            "214": "dazn 1",
            "dazn 2": "dazn 2",
            "dazn 2 it": "dazn 2",
            "dazn 2 italia": "dazn 2",
            "zona dazn 2": "dazn 2",
            "zona dazn 2 it": "dazn 2",
            "dazn zona 2": "dazn 2",
            "dazn zona 2 it": "dazn 2",
            "zona dazn 215": "dazn 2",
            "dazn 215": "dazn 2",
            "sky 215": "dazn 2",
            "sky sport 215": "dazn 2",
            "215": "dazn 2",
            "dazn 3": "dazn 3",
            "dazn 3 it": "dazn 3",
            "dazn 3 italia": "dazn 3",
            "zona dazn 3": "dazn 3",
            "zona dazn 216": "dazn 3",
            "dazn 216": "dazn 3",
            "sky 216": "dazn 3",
            "sky sport 216": "dazn 3",
            "216": "dazn 3",
            "dazn 4": "dazn 4",
            "dazn 4 it": "dazn 4",
            "dazn 4 italia": "dazn 4",
            "zona dazn 4": "dazn 4",
            "zona dazn 217": "dazn 4",
            "dazn 217": "dazn 4",
            "sky 217": "dazn 4",
            "sky sport 217": "dazn 4",
            "217": "dazn 4",

            # Sky Sport 4K (LCN 213) & Sky Sport Legend (LCN 210) & Mix (LCN 211)
            "sky sport 4k": "sky sport 4k",
            "sky 4k": "sky sport 4k",
            "sky sport 213": "sky sport 4k",
            "sky 213": "sky sport 4k",
            "213": "sky sport 4k",
            "sky sport legend": "sky sport legend",
            "sky legend": "sky sport legend",
            "sky sport 210": "sky sport legend",
            "sky 210": "sky sport legend",
            "sky sport mix 211": "sky sport mix",
            "sky 211": "sky sport mix",

            # Sky Sport Calcio Contemporanea (LCN 251 - 259) - Mappatura Difensiva & ID Ufficiali
            "sky sport 251": "sky sport 251",
            "sky sport calcio 251": "sky sport 251",
            "sky calcio 251": "sky sport 251",
            "sky calcio 1": "sky sport 251",
            "sky 251": "sky sport 251",
            "251": "sky sport 251",

            "sky sport 252": "sky sport 252",
            "sky sport calcio 252": "sky sport 252",
            "sky calcio 252": "sky sport 252",
            "sky calcio 2": "sky sport 252",
            "sky 252": "sky sport 252",
            "252": "sky sport 252",

            "sky sport 253": "sky sport 253",
            "sky sport calcio 253": "sky sport 253",
            "sky calcio 253": "sky sport 253",
            "sky calcio 3": "sky sport 253",
            "sky 253": "sky sport 253",
            "253": "sky sport 253",

            "sky sport 254": "sky sport 254",
            "sky sport calcio 254": "sky sport 254",
            "sky calcio 254": "sky sport 254",
            "sky calcio 4": "sky sport 254",
            "sky 254": "sky sport 254",
            "254": "sky sport 254",

            "sky sport 255": "sky sport 255",
            "sky sport calcio 255": "sky sport 255",
            "sky calcio 255": "sky sport 255",
            "sky calcio 5": "sky sport 255",
            "sky 255": "sky sport 255",
            "255": "sky sport 255",

            "sky sport 256": "sky sport 256",
            "sky sport calcio 256": "sky sport 256",
            "sky calcio 256": "sky sport 256",
            "sky calcio 6": "sky sport 256",
            "sky 256": "sky sport 256",
            "256": "sky sport 256",

            "sky sport 257": "sky sport 257",
            "sky sport calcio 257": "sky sport 257",
            "sky calcio 257": "sky sport 257",
            "sky calcio 7": "sky sport 257",
            "sky 257": "sky sport 257",
            "257": "sky sport 257",

            "sky sport 258": "sky sport 258",
            "sky sport calcio 258": "sky sport 258",
            "sky calcio 258": "sky sport 258",
            "sky calcio 8": "sky sport 258",
            "sky 258": "sky sport 258",
            "258": "sky sport 258",

            "sky sport 259": "sky sport 259",
            "sky sport calcio 259": "sky sport 259",
            "sky calcio 259": "sky sport 259",
            "sky calcio 9": "sky sport 259",
            "sky 259": "sky sport 259",
            "259": "sky sport 259",

            # Club Channels (LCN 228, 230, 232)
            "sport tv": "sport tv",
            "sport tv 228": "sport tv",
            "sky 228": "sport tv",
            "228": "sport tv",
            "milan tv": "milan tv",
            "milan tv 230": "milan tv",
            "sky 230": "milan tv",
            "230": "milan tv",
            "inter tv": "inter tv",
            "inter tv 232": "inter tv",
            "sky 232": "inter tv",
            "232": "inter tv",

            # Mediaset & Rai 4K
            "20 mediaset": "20 mediaset",
            "canale 20": "20 mediaset",
            "mediaset 20": "20 mediaset",
            "20": "20 mediaset",
            "rai 4k": "rai 4k",

            # Rai Sport & Generalist FTA
            "rai sport": "rai sport",
            "rai sport +": "rai sport",
            "rai sport+": "rai sport",
            "rai sport + hd": "rai sport",
            "rai sport hd": "rai sport",
            "rai sport 1": "rai sport",
            "sportitalia": "sportitalia",
            "sportitalia plus": "sportitalia plus",
            "sportitalia solocalcio": "sportitalia solocalcio",
            "tv8": "tv8",
            "tv 8": "tv8",
            "cielo": "cielo",
            "canale 5": "canale 5",
            "canale 5 hd": "canale 5",
            "italia 1": "italia 1",
            "italia 1 hd": "italia 1",
            "rai 1": "rai 1",
            "rai 1 hd": "rai 1",
            "rai 2": "rai 2",
            "rai 2 hd": "rai 2",
            "rai 3": "rai 3",
            "rai 3 hd": "rai 3",

            # Amazon Prime Video IT
            "prime video": "prime video it",
            "prime video it": "prime video it",
            "prime video italia": "prime video it",
            "amazon prime": "prime video it",
            "amazon prime video": "prime video it",
            "amazon prime video it": "prime video it",
            "amazon prime video italia": "prime video it",
            "amazon prime sport": "prime video it",
        },

        "Germany": {
            # Magenta Sport (1..10 and receiver channels 301..310)
            # Unnumbered 'magenta sport' ghost feed disabled by directive
            "magenta sport 1": "magenta sport 1",
            "magenta sport 301": "magenta sport 1",
            "301": "magenta sport 1",
            "magenta sport 2": "magenta sport 2",
            "magenta sport 302": "magenta sport 2",
            "302": "magenta sport 2",
            "magenta sport 3": "magenta sport 3",
            "magenta sport 303": "magenta sport 3",
            "303": "magenta sport 3",
            "magenta sport 4": "magenta sport 4",
            "magenta sport 304": "magenta sport 4",
            "304": "magenta sport 4",
            "magenta sport 5": "magenta sport 5",
            "magenta sport 305": "magenta sport 5",
            "305": "magenta sport 5",
            "magenta sport 6": "magenta sport 6",
            "magenta sport 306": "magenta sport 6",
            "306": "magenta sport 6",
            "magenta sport 7": "magenta sport 7",
            "magenta sport 307": "magenta sport 7",
            "307": "magenta sport 7",
            "magenta sport 8": "magenta sport 8",
            "magenta sport 308": "magenta sport 8",
            "308": "magenta sport 8",
            "magenta sport 9": "magenta sport 9",
            "magenta sport 309": "magenta sport 9",
            "309": "magenta sport 9",
            "magenta sport 10": "magenta sport 10",
            "magenta sport 310": "magenta sport 10",
            "310": "magenta sport 10",

            # Sky Sport Bundesliga DE
            "sky sport bundesliga": "sky sport bundesliga",
            "sky bundesliga": "sky sport bundesliga",
            "sky sport bundesliga 1": "sky sport bundesliga 1",
            "sky sport bundesliga 2": "sky sport bundesliga 2",
            "sky sport bundesliga 3": "sky sport bundesliga 3",
            "sky sport bundesliga 4": "sky sport bundesliga 4",
            "sky sport bundesliga 5": "sky sport bundesliga 5",
            "sky sport bundesliga 6": "sky sport bundesliga 6",
            "sky sport bundesliga 7": "sky sport bundesliga 7",
            "sky sport bundesliga 8": "sky sport bundesliga 8",
            "sky sport bundesliga 9": "sky sport bundesliga 9",
            "sky sport bundesliga 10": "sky sport bundesliga 10",

            # Sky Sport DE
            "sky sport top event": "sky sport top event",
            "sky top event": "sky sport top event",
            "sky sport premier league": "sky sport premier league de",
            "sky sport premier league de": "sky sport premier league de",
            "sky premier league de": "sky sport premier league de",
            "sky sport 1": "sky sport 1 de",
            "sky sport 1 de": "sky sport 1 de",
            "sky sport 2": "sky sport 2 de",
            "sky sport 2 de": "sky sport 2 de",
            "sky sport 3": "sky sport 3 de",
            "sky sport 3 de": "sky sport 3 de",
            "sky sport 4": "sky sport 4 de",
            "sky sport 4 de": "sky sport 4 de",
            "sky sport 5": "sky sport 5 de",
            "sky sport 5 de": "sky sport 5 de",
            "sky sport 6": "sky sport 6 de",
            "sky sport 6 de": "sky sport 6 de",
            "sky sport 7": "sky sport 7 de",
            "sky sport 7 de": "sky sport 7 de",
            "sky sport 8": "sky sport 8 de",
            "sky sport 8 de": "sky sport 8 de",
            "sky sport 9": "sky sport 9 de",
            "sky sport 9 de": "sky sport 9 de",
            "sky sport 10": "sky sport 10 de",
            "sky sport 10 de": "sky sport 10 de",
            "sky sport 11": "sky sport 11 de",
            "sky sport 11 de": "sky sport 11 de",
            "sky sport f1": "sky sport f1 de",
            "sky sport f1 de": "sky sport f1 de",
            "sky sport f1 germany": "sky sport f1 de",
            "sky sport tennis": "sky sport tennis de",
            "sky sport tennis de": "sky sport tennis de",
            "sky sport golf": "sky sport golf de",
            "sky sport golf de": "sky sport golf de",
            "sky sport mix": "sky sport mix de",
            "sky sport mix de": "sky sport mix de",

            # DAZN DE
            "dazn 1": "dazn 1 de",
            "dazn 1 de": "dazn 1 de",
            "dazn 1 germany": "dazn 1 de",
            "dazn 2": "dazn 2 de",
            "dazn 2 de": "dazn 2 de",
            "dazn 2 germany": "dazn 2 de",
            "sport1": "sport1 de",
            "sport 1": "sport1 de",
            "sport1 de": "sport1 de",
            "sport 1 de": "sport1 de",
            "sport1 germany": "sport1 de",

            # Amazon Prime Video DE & DAZN NBA
            "amazon prime sport": "amazon prime sport de",
            "amazon pr me sport": "amazon prime sport de",
            "amazon prime": "amazon prime de",
            "amazon prime de": "amazon prime de",
            "amazon prime video": "amazon prime de",
            "amazon prime video 1": "amazon prime video 1 de",
            "amazon prime video 2": "amazon prime video 2 de",
            "germany box amazon prime": "amazon prime de",
            "dazn nba": "dazn nba de",
        },

        "Spain": {
            # Movistar LaLiga
            "movistar laliga": "movistar laliga",
            "movistar laliga 1": "movistar laliga 1",
            "movistar laliga 2": "movistar laliga 2",
            "movistar laliga 3": "movistar laliga 3",
            "movistar laliga 4": "movistar laliga 4",
            "laliga tv por movistar plus": "movistar laliga",

            # Movistar Liga de Campeones
            "movistar liga de campeones": "movistar liga campeones",
            "movistar liga campeones": "movistar liga campeones",
            "movistar campeones": "movistar liga campeones",
            "movistar liga campeones 1": "movistar liga campeones 1",
            "movistar liga campeones 2": "movistar liga campeones 2",
            "movistar liga campeones 3": "movistar liga campeones 3",
            "movistar liga campeones 4": "movistar liga campeones 4",
            "movistar liga campeones 5": "movistar liga campeones 5",
            "movistar liga campeones 6": "movistar liga campeones 6",
            "movistar liga campeones 7": "movistar liga campeones 7",
            "movistar liga campeones 8": "movistar liga campeones 8",
            "movistar liga campeones 9": "movistar liga campeones 9",
            "movistar liga campeones 10": "movistar liga campeones 10",

            # Movistar Deportes & Vamos
            "movistar deportes": "movistar deportes",
            "movistar deporte 1": "movistar deportes 1",
            "movistar deportes 1": "movistar deportes 1",
            "movistar deportes 2": "movistar deportes 2",
            "movistar deportes 3": "movistar deportes 3",
            "movistar deportes 4": "movistar deportes 4",
            "movistar deportes 5": "movistar deportes 5",
            "movistar golf": "movistar golf",
            "movistar vamos": "movistar vamos",
            "movistar #vamos": "movistar vamos",

            # DAZN ES
            "dazn 1": "dazn 1 es",
            "dazn 1 es": "dazn 1 es",
            "dazn 1 esp": "dazn 1 es",
            "dazn 1 spain": "dazn 1 es",
            "dazn 2": "dazn 2 es",
            "dazn 2 es": "dazn 2 es",
            "dazn 2 esp": "dazn 2 es",
            "dazn 2 spain": "dazn 2 es",
            "dazn 3": "dazn 3 es",
            "dazn 3 es": "dazn 3 es",
            "dazn 3 spain": "dazn 3 es",
            "dazn 4": "dazn 4 es",
            "dazn 4 es": "dazn 4 es",
            "dazn 4 spain": "dazn 4 es",
            "dazn laliga": "dazn laliga",
            "dazn laliga 1": "dazn laliga 1",
            "dazn laliga 2": "dazn laliga 2",
            "dazn f1": "dazn f1 es",
            "dazn f1 es": "dazn f1 es",
            "gol play": "gol play",
            "gol": "gol play",
            "teledeporte": "teledeporte",
            "tve teledeporte": "teledeporte",
            "bein sports la liga": "bein sports la liga",

            # Amazon Prime Video ES
            "prime video": "prime video es",
            "prime video es": "prime video es",
            "amazon prime": "prime video es",
            "amazon prime video": "prime video es",
            "amazon prime video es": "prime video es",
        },

        "France": {
            # Canal+
            "canal+ sport": "canal plus sport",
            "canal sport": "canal plus sport",
            "canal plus sport": "canal plus sport",
            "canal+ foot": "canal plus foot",
            "canal foot": "canal plus foot",
            "canal plus foot": "canal plus foot",
            "canal+ 360": "canal plus 360",
            "canal 360": "canal plus 360",
            "canal plus 360": "canal plus 360",
            "canal+": "canal plus",
            "canal plus": "canal plus",
            "canal+ live 1": "canal plus live 1",
            "canal plus live 1": "canal plus live 1",
            "canal+ live 2": "canal plus live 2",
            "canal plus live 2": "canal plus live 2",
            "canal+ live 3": "canal plus live 3",
            "canal plus live 3": "canal plus live 3",

            # beIN Sports FR
            "bein sports 1": "bein sports 1 fr",
            "bein sports 1 fr": "bein sports 1 fr",
            "bein sports 1 france": "bein sports 1 fr",
            "bein sports hd 1 france": "bein sports 1 fr",
            "bein sports 2": "bein sports 2 fr",
            "bein sports 2 fr": "bein sports 2 fr",
            "bein sports 2 france": "bein sports 2 fr",
            "bein sports 3": "bein sports 3 fr",
            "bein sports 3 fr": "bein sports 3 fr",
            "bein sports 3 france": "bein sports 3 fr",
            "bein sports max 4": "bein sports max 4 fr",
            "bein sports max 4 fr": "bein sports max 4 fr",
            "bein sports max 5": "bein sports max 5 fr",
            "bein sports max 5 fr": "bein sports max 5 fr",
            "bein sports max 6": "bein sports max 6 fr",
            "bein sports max 6 fr": "bein sports max 6 fr",
            "bein sports max 7": "bein sports max 7 fr",
            "bein sports max 7 fr": "bein sports max 7 fr",
            "bein sports max 8": "bein sports max 8 fr",
            "bein sports max 8 fr": "bein sports max 8 fr",
            "bein sports max 9": "bein sports max 9 fr",
            "bein sports max 9 fr": "bein sports max 9 fr",
            "bein sports max 10": "bein sports max 10 fr",
            "bein sports max 10 fr": "bein sports max 10 fr",

            # DAZN FR
            "dazn 1": "dazn 1 fr",
            "dazn 1 fr": "dazn 1 fr",
            "dazn 2": "dazn 2 fr",
            "dazn 2 fr": "dazn 2 fr",
            "dazn 3": "dazn 3 fr",
            "dazn 3 fr": "dazn 3 fr",
            "rmc sport 1": "rmc sport 1",
            "rmc sport 2": "rmc sport 2",

            # Amazon Prime Video FR (Ligue 1 & Roland Garros)
            "amazon prime 1": "amazon prime 1 fr",
            "amazon prime 1 +": "amazon prime 1 fr",
            "amazon prime 2": "amazon prime 2 fr",
            "amazon prime 2 +": "amazon prime 2 fr",
            "amazon prime 3": "amazon prime 3 fr",
            "amazon prime 3 +": "amazon prime 3 fr",
            "amazon prime 4": "amazon prime 4 fr",
            "amazon prime 4 +": "amazon prime 4 fr",
            "amazon prime 5": "amazon prime 5 fr",
            "amazon prime 5 +": "amazon prime 5 fr",
            "amazon prime 6": "amazon prime 6 fr",
            "amazon prime 7": "amazon prime 7 fr",
            "amazon prime 8": "amazon prime 8 fr",
            "amazon prime": "amazon prime 1 fr",
            "amazon prime video": "amazon prime 1 fr",
        },

        "United Kingdom": {
            # Sky Sports UK
            "sky sports main event": "sky sports main event",
            "sky main event": "sky sports main event",
            "sky sports premier league": "sky sports premier league uk",
            "sky sports football": "sky sports football uk",
            "sky sports f1": "sky sports f1 uk",
            "sky sports f1 uk": "sky sports f1 uk",
            "sky sports cricket": "sky sports cricket uk",
            "sky sports action": "sky sports action uk",
            "sky sports arena": "sky sports arena uk",
            "sky sports arena uk": "sky sports arena uk",
            "sky sports golf": "sky sports golf uk",
            "sky sports tennis": "sky sports tennis uk",
            "sky sports news": "sky sports news uk",
            "sky sports mix": "sky sports mix uk",
            "sky sports nfl": "sky sports nfl",

            # TNT Sports UK
            "tnt sports 1": "tnt sports 1",
            "tnt sport 1": "tnt sports 1",
            "bt sport 1": "tnt sports 1",
            "tnt sports 2": "tnt sports 2",
            "tnt sport 2": "tnt sports 2",
            "bt sport 2": "tnt sports 2",
            "tnt sports 3": "tnt sports 3",
            "tnt sport 3": "tnt sports 3",
            "bt sport 3": "tnt sports 3",
            "tnt sports 4": "tnt sports 4",
            "tnt sport 4": "tnt sports 4",
            "bt sport 4": "tnt sports 4",
            "tnt sports ultimate": "tnt sports ultimate",
            "bbc one": "bbc one",
            "bbc two": "bbc two",
            "itv 1": "itv 1",
            "itv 4": "itv 4",

            # Amazon Prime Video UK
            "amazon": "amazon prime uk",
            "amazon prime": "amazon prime uk",
            "amazon prime uk": "amazon prime uk",
            "amazon prime video": "amazon prime uk",
            "amazon prime video uk": "amazon prime uk",
            "prime video uk": "amazon prime uk",
        },

        "World": {
            "euroleague tv": "euroleague tv",
            "nba tv": "nba tv",
            "nba tv usa": "nba tv",
            "nba league pass": "nba league pass",
            "nba pass": "nba league pass",
        }
    }

    # Flat combined alias dictionary for global queries (country not specified)
    # Load foreign countries first, then Italy so Italy takes precedence on ambiguous global keys
    CHANNEL_ALIASES: Dict[str, str] = {}
    for _grp_name in ["World", "United Kingdom", "France", "Spain", "Germany", "Italy"]:
        if _grp_name in COUNTRY_ALIASES:
            CHANNEL_ALIASES.update(COUNTRY_ALIASES[_grp_name])

    # Display names formatted nicely with proper capitalization and country flags
    CANONICAL_DISPLAY_NAMES: Dict[str, str] = {
        # Italy
        "sky sport uno": "Sky Sport Uno",
        "sky sport calcio": "Sky Sport Calcio",
        "sky sport tennis": "Sky Sport Tennis",
        "sky sport arena": "Sky Sport Arena",
        "sky sport f1": "Sky Sport F1",
        "sky sport motogp": "Sky Sport MotoGP",
        "sky sport nba": "Sky Sport NBA",
        "sky sport 24": "Sky Sport 24",
        "sky sport golf": "Sky Sport Golf",
        "sky sport max": "Sky Sport Max",
        "sky sport mix": "Sky Sport Mix",
        "sky sport serie a": "Sky Sport Serie A",
        "sky sport": "Sky Sport",
        "dazn 1": "DAZN 1",
        "dazn 2": "DAZN 2",
        "eurosport 1": "Eurosport 1",
        "eurosport 2": "Eurosport 2",
        "rai sport": "Rai Sport",
        "sportitalia": "Sportitalia",
        "sportitalia plus": "Sportitalia Plus",
        "sportitalia solocalcio": "Sportitalia SoloCalcio",
        "supertennis": "SuperTennis",
        "tv8": "TV8",
        "cielo": "Cielo",
        "canale 5": "Canale 5",
        "italia 1": "Italia 1",
        "rai 1": "Rai 1",
        "rai 2": "Rai 2",
        "rai 3": "Rai 3",
        "rai 4k": "Rai 4K",
        "prime video it": "Prime Video",
        "sky sport 4k": "Sky Sport 4K",
        "sky sport legend": "Sky Sport Legend",
        "dazn 3": "DAZN 3",
        "dazn 4": "DAZN 4",
        "sky sport 251": "Sky Sport 251",
        "sky sport 252": "Sky Sport 252",
        "sky sport 253": "Sky Sport 253",
        "sky sport 254": "Sky Sport 254",
        "sky sport 255": "Sky Sport 255",
        "sky sport 256": "Sky Sport 256",
        "sky sport 257": "Sky Sport 257",
        "sky sport 258": "Sky Sport 258",
        "sky sport 259": "Sky Sport 259",
        "sport tv": "Sport TV",
        "milan tv": "Milan TV",
        "inter tv": "Inter TV",
        "20 mediaset": "20 Mediaset",

        # Germany
        "magenta sport 1": "🇩🇪 Magenta Sport 1",
        "magenta sport 2": "🇩🇪 Magenta Sport 2",
        "magenta sport 3": "🇩🇪 Magenta Sport 3",
        "magenta sport 4": "🇩🇪 Magenta Sport 4",
        "magenta sport 5": "🇩🇪 Magenta Sport 5",
        "magenta sport 6": "🇩🇪 Magenta Sport 6",
        "magenta sport 7": "🇩🇪 Magenta Sport 7",
        "magenta sport 8": "🇩🇪 Magenta Sport 8",
        "magenta sport 9": "🇩🇪 Magenta Sport 9",
        "magenta sport 10": "🇩🇪 Magenta Sport 10",
        "sky sport bundesliga": "🇩🇪 Sky Sport Bundesliga",
        "sky sport bundesliga 1": "🇩🇪 Sky Sport Bundesliga 1",
        "sky sport bundesliga 2": "🇩🇪 Sky Sport Bundesliga 2",
        "sky sport bundesliga 3": "🇩🇪 Sky Sport Bundesliga 3",
        "sky sport bundesliga 4": "🇩🇪 Sky Sport Bundesliga 4",
        "sky sport bundesliga 5": "🇩🇪 Sky Sport Bundesliga 5",
        "sky sport top event": "🇩🇪 Sky Sport Top Event",
        "sky sport premier league de": "🇩🇪 Sky Sport Premier League",
        "sky sport 1 de": "🇩🇪 Sky Sport 1 DE",
        "sky sport 2 de": "🇩🇪 Sky Sport 2 DE",
        "sky sport 3 de": "🇩🇪 Sky Sport 3 DE",
        "sky sport 4 de": "🇩🇪 Sky Sport 4 DE",
        "sky sport 5 de": "🇩🇪 Sky Sport 5 DE",
        "sky sport 6 de": "🇩🇪 Sky Sport 6 DE",
        "sky sport 7 de": "🇩🇪 Sky Sport 7 DE",
        "sky sport 8 de": "🇩🇪 Sky Sport 8 DE",
        "sky sport 9 de": "🇩🇪 Sky Sport 9 DE",
        "sky sport 10 de": "🇩🇪 Sky Sport 10 DE",
        "sky sport 11 de": "🇩🇪 Sky Sport 11 DE",
        "sky sport f1 de": "🇩🇪 Sky Sport F1 DE",
        "sky sport tennis de": "🇩🇪 Sky Sport Tennis DE",
        "sky sport golf de": "🇩🇪 Sky Sport Golf DE",
        "sky sport mix de": "🇩🇪 Sky Sport Mix DE",
        "dazn 1 de": "🇩🇪 DAZN 1 DE",
        "dazn 2 de": "🇩🇪 DAZN 2 DE",
        "sport1 de": "🇩🇪 Sport1 DE",
        "amazon prime sport de": "🇩🇪 Amazon Prime Sport",
        "amazon prime de": "🇩🇪 Amazon Prime Video",
        "amazon prime video 1 de": "🇩🇪 Amazon Prime Video 1",
        "amazon prime video 2 de": "🇩🇪 Amazon Prime Video 2",
        "dazn nba de": "🇩🇪 DAZN NBA",

        # Spain
        "movistar laliga": "🇪🇸 Movistar LaLiga",
        "movistar laliga 1": "🇪🇸 Movistar LaLiga 1",
        "movistar laliga 2": "🇪🇸 Movistar LaLiga 2",
        "movistar laliga 3": "🇪🇸 Movistar LaLiga 3",
        "movistar laliga 4": "🇪🇸 Movistar LaLiga 4",
        "movistar liga campeones": "🇪🇸 Movistar Liga de Campeones",
        "movistar liga campeones 1": "🇪🇸 Movistar Liga de Campeones 1",
        "movistar liga campeones 2": "🇪🇸 Movistar Liga de Campeones 2",
        "movistar liga campeones 3": "🇪🇸 Movistar Liga de Campeones 3",
        "movistar deportes": "🇪🇸 Movistar Deportes",
        "movistar deportes 1": "🇪🇸 Movistar Deportes 1",
        "movistar deportes 2": "🇪🇸 Movistar Deportes 2",
        "movistar golf": "🇪🇸 Movistar Golf",
        "movistar vamos": "🇪🇸 Movistar #Vamos",
        "dazn 1 es": "🇪🇸 DAZN 1 ES",
        "dazn 2 es": "🇪🇸 DAZN 2 ES",
        "dazn 3 es": "🇪🇸 DAZN 3 ES",
        "dazn 4 es": "🇪🇸 DAZN 4 ES",
        "dazn laliga": "🇪🇸 DAZN LaLiga",
        "dazn laliga 1": "🇪🇸 DAZN LaLiga 1",
        "dazn laliga 2": "🇪🇸 DAZN LaLiga 2",
        "dazn f1 es": "🇪🇸 DAZN F1 ES",
        "gol play": "🇪🇸 Gol Play",
        "teledeporte": "🇪🇸 Teledeporte",
        "bein sports la liga": "🇪🇸 beIN Sports LaLiga",
        "prime video es": "🇪🇸 Prime Video",

        # France
        "canal plus sport": "🇫🇷 Canal+ Sport",
        "canal plus foot": "🇫🇷 Canal+ Foot",
        "canal plus 360": "🇫🇷 Canal+ 360",
        "canal plus": "🇫🇷 Canal+",
        "canal plus live 1": "🇫🇷 Canal+ Live 1",
        "canal plus live 2": "🇫🇷 Canal+ Live 2",
        "bein sports 1 fr": "🇫🇷 beIN Sports 1",
        "bein sports 2 fr": "🇫🇷 beIN Sports 2",
        "bein sports 3 fr": "🇫🇷 beIN Sports 3",
        "bein sports max 4 fr": "🇫🇷 beIN Sports Max 4",
        "bein sports max 5 fr": "🇫🇷 beIN Sports Max 5",
        "bein sports max 6 fr": "🇫🇷 beIN Sports Max 6",
        "dazn 1 fr": "🇫🇷 DAZN 1 FR",
        "dazn 2 fr": "🇫🇷 DAZN 2 FR",
        "dazn 3 fr": "🇫🇷 DAZN 3 FR",
        "rmc sport 1": "🇫🇷 RMC Sport 1",
        "rmc sport 2": "🇫🇷 RMC Sport 2",
        "amazon prime 1 fr": "🇫🇷 Amazon Prime 1",
        "amazon prime 2 fr": "🇫🇷 Amazon Prime 2",
        "amazon prime 3 fr": "🇫🇷 Amazon Prime 3",
        "amazon prime 4 fr": "🇫🇷 Amazon Prime 4",
        "amazon prime 5 fr": "🇫🇷 Amazon Prime 5",
        "amazon prime 6 fr": "🇫🇷 Amazon Prime 6",
        "amazon prime 7 fr": "🇫🇷 Amazon Prime 7",
        "amazon prime 8 fr": "🇫🇷 Amazon Prime 8",

        # United Kingdom
        "sky sports main event": "🇬🇧 Sky Sports Main Event",
        "sky sports premier league uk": "🇬🇧 Sky Sports Premier League",
        "sky sports football uk": "🇬🇧 Sky Sports Football",
        "sky sports f1 uk": "🇬🇧 Sky Sports F1",
        "sky sports cricket uk": "🇬🇧 Sky Sports Cricket",
        "sky sports action uk": "🇬🇧 Sky Sports Action",
        "sky sports arena uk": "🇬🇧 Sky Sports Arena",
        "sky sports golf uk": "🇬🇧 Sky Sports Golf",
        "sky sports tennis uk": "🇬🇧 Sky Sports Tennis",
        "sky sports news uk": "🇬🇧 Sky Sports News",
        "sky sports mix uk": "🇬🇧 Sky Sports Mix",
        "sky sports nfl": "🇬🇧 Sky Sports NFL",
        "tnt sports 1": "🇬🇧 TNT Sports 1",
        "tnt sports 2": "🇬🇧 TNT Sports 2",
        "tnt sports 3": "🇬🇧 TNT Sports 3",
        "tnt sports 4": "🇬🇧 TNT Sports 4",
        "tnt sports ultimate": "🇬🇧 TNT Sports Ultimate",
        "bbc one": "🇬🇧 BBC One",
        "bbc two": "🇬🇧 BBC Two",
        "itv 1": "🇬🇧 ITV 1",
        "itv 4": "🇬🇧 ITV 4",
        "amazon prime uk": "🇬🇧 Amazon Prime Video",

        # World
        "euroleague tv": "🌍 EuroLeague TV",
        "nba tv": "🌍 NBA TV",
        "nba league pass": "🌍 NBA League Pass",
    }

    def __init__(self):
        self._channels_by_canonical: Dict[str, List[Dict[str, Any]]] = {}
        self._last_sync_time: float = 0.0
        self._sync_lock = asyncio.Lock()
        self._addon_sig: Optional[str] = None
        self._sig_time: float = 0.0
        self._sig_ip: Optional[str] = None
        self._active_domain: str = self.DEFAULT_DOMAIN
        self.manifest_manager = LiveManifestManager(self)

    @property
    def active_domain(self) -> str:
        return self._active_domain

    def get_catalog_url(self, domain: Optional[str] = None) -> str:
        d = domain or self._active_domain
        return f"https://{d}/mediahubmx-catalog.json"

    def get_resolve_url(self, domain: Optional[str] = None) -> str:
        d = domain or self._active_domain
        return f"https://{d}/mediahubmx-resolve.json"

    @staticmethod
    def cleanup_channel_name(name: str) -> str:
        """Cleans and normalizes channel name string for dictionary matching."""
        if not name:
            return ""
        s = name.lower()
        # Remove flag emojis
        s = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", s)
        # Remove server tags like .c, .s, .b, .p
        s = re.sub(r"\s*\.[a-z0-9]{1,3}\b", "", s)
        # Remove bracketed tags like [live during events only]
        s = re.sub(r"\[[^\]]*\]", "", s)
        # Remove parentheses tags
        s = re.sub(r"\([^)]*\)", "", s)
        # Remove quality markers and codecs (4K is preserved here for channel identity, stripped only in noise-word fallback)
        s = re.sub(r"\b(fhd|uhd|sd|hd\+?|hevc|raw|h\.?265)\b", "", s)
        # Normalize punctuation preserving # and +
        s = re.sub(r"[^a-z0-9#+]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    def get_canonical_key(self, channel_name: str, country: Optional[str] = None) -> Optional[str]:
        """
        Maps a given channel name to a canonical key using country silos and strict numeric matching.
        Prevents greedy partial match collisions across numbered channels.
        """
        cleaned = self.cleanup_channel_name(channel_name)
        if not cleaned:
            return None

        # Strict "Arena" protection:
        # Balkan and foreign Arena channels (e.g. Arena Sport 1..10, Arena Premium 1..5, Arena 1..10,
        # Arena Tennis, Arena Sport Croatia/Serbia, Arena Esport, Arena Fight, bare 'Arena') must NEVER
        # resolve to Italian "Sky Sport Arena" or UK "Sky Sports Arena", nor accidentally match other
        # numbered channels (such as 'sport 1' in 'arena sport 1' matching 'sport1 de').
        # Any legitimate Arena channel in our supported silos MUST explicitly contain 'sky'.
        if re.search(r"\barena\b", cleaned) and not re.search(r"\bsky\b", cleaned):
            return None

        # Check trailing country word/code (e.g. 'dazn ca', 'dazn de', 'dazn 1 de', 'bein sports 1 france')
        trailing_word_match = re.search(r"\b([a-z]{2,12})$", cleaned)
        if trailing_word_match:
            trailing_code = trailing_word_match.group(1)
            if trailing_code in self.UNSUPPORTED_COUNTRY_SUFFIXES:
                # Explicit non-supported foreign broadcast (e.g. Canada, USA, Brazil, Serbia, Croatia) -> NEVER match
                return None
            if country is None and trailing_code in self.COUNTRY_SUFFIX_MAP:
                detected_country = self.COUNTRY_SUFFIX_MAP[trailing_code]
                base_without_suffix = cleaned[:trailing_word_match.start()].strip()
                silo = self.COUNTRY_ALIASES.get(detected_country, {})
                if base_without_suffix in silo:
                    return silo[base_without_suffix]
                if cleaned in silo:
                    return silo[cleaned]
                country = detected_country

        # Build candidate dictionaries: prioritize country silo if specified or detected
        candidate_dicts: List[Dict[str, str]] = []
        if country and country in self.COUNTRY_ALIASES:
            # When country is specified or detected, ONLY search that country's silo (never contaminate with Italy)
            candidate_dicts.append(self.COUNTRY_ALIASES[country])
        else:
            # Fallback for country-agnostic queries: Italy first, then global aliases
            candidate_dicts.append(self.COUNTRY_ALIASES.get("Italy", {}))
            candidate_dicts.append(self.CHANNEL_ALIASES)

        for aliases_map in candidate_dicts:
            # 1. Exact match
            if cleaned in aliases_map:
                return aliases_map[cleaned]

            # 2. Match without symbols
            cleaned_no_sym = re.sub(r"[#+]", " ", cleaned).strip()
            cleaned_no_sym = re.sub(r"\s+", " ", cleaned_no_sym)
            if cleaned_no_sym in aliases_map:
                return aliases_map[cleaned_no_sym]

            # 3. Match with common noise words stripped (e.g., 'hd', 'uhd', 'fhd', '4k', 'tv', 'channel', 'canale', 'live')
            cleaned_no_noise = re.sub(r"\b(hd|uhd|fhd|4k|sd|tv|channel|canale|live)\b", " ", cleaned)
            cleaned_no_noise = re.sub(r"\s+", " ", cleaned_no_noise).strip()
            if cleaned_no_noise and cleaned_no_noise in aliases_map:
                return aliases_map[cleaned_no_noise]

            # 4. Strict Number Match
            # Only match aliases that share the EXACT same set of numbers as cleaned.
            nums_in_cleaned = set(re.findall(r"\b\d+\b", cleaned))
            if nums_in_cleaned:
                for alias, canonical in aliases_map.items():
                    nums_in_alias = set(re.findall(r"\b\d+\b", alias))
                    if nums_in_cleaned != nums_in_alias:
                        continue
                    # Must be a word-bounded substring (e.g. 'sky sport 1' in 'sky sport 1 bar')
                    # Never allow reverse matching (cleaned in alias) to prevent false matches
                    pattern = r"\b" + re.escape(alias) + r"\b"
                    if re.search(pattern, cleaned):
                        return canonical

        return None

    async def get_vavoo_signature(self, client_ip: Optional[str] = None) -> Optional[str]:
        """Obtains session authorization signature via Vavoo ping endpoint."""
        now = time.time()
        if self._addon_sig and (now - self._sig_time) < 1500 and (client_ip is None or self._sig_ip == client_ip):
            return self._addon_sig

        payload = {
            "token": "",
            "reason": "app-focus",
            "locale": "de",
            "theme": "dark",
            "metadata": {
                "device": {"type": "phone", "uniqueId": uuid.uuid4().hex[:16]},
                "os": {"name": "android", "version": "14", "abis": ["arm64-v8a"], "host": "android"},
                "app": {"platform": "android"},
                "version": {"package": "net.vypn.app", "binary": "1.4.1", "js": "1.4.1"},
            },
            "appFocusTime": 0,
            "playerActive": False,
            "playDuration": 0,
            "devMode": False,
            "hasAddon": True,
            "castConnected": False,
            "package": "net.vypn.app",
            "version": "1.4.1",
            "ipLocation": sanitize_ip(client_ip),
        }

        headers = {
            "user-agent": "okhttp/4.11.0",
            "accept": "application/json",
            "content-type": "application/json; charset=utf-8",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(self.PING_URL, headers=headers, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    sig = data.get("addonSig")
                    if sig:
                        self._addon_sig = sig
                        self._sig_time = now
                        self._sig_ip = sanitize_ip(client_ip)
                        logger.info("Successfully acquired new Vavoo session signature.")
                        return sig
        except Exception as e:
            logger.warning("Failed to obtain Vavoo signature: %s", e)
        return None

    async def sync_channels(self, force: bool = False) -> Dict[str, List[Dict[str, Any]]]:
        """
        Synchronizes sports channels across all supported country groups
        (Italy, Spain, Germany, France, United Kingdom) from the live Vavoo catalog.
        Caches in RAM for 6 hours unless forced.
        """
        if not ENABLE_TVVOO:
            return {}

        now = time.time()
        if not force and self._channels_by_canonical and (now - self._last_sync_time) < TVVOO_CACHE_INTERVAL:
            return self._channels_by_canonical

        async with self._sync_lock:
            # Double-check inside lock
            if not force and self._channels_by_canonical and (now - self._last_sync_time) < TVVOO_CACHE_INTERVAL:
                return self._channels_by_canonical

            sig = await self.get_vavoo_signature()
            if not sig:
                logger.warning("Cannot sync Vavoo channels without a valid session signature.")
                return self._channels_by_canonical

            cat_headers = {
                "user-agent": "okhttp/4.11.0",
                "accept": "application/json",
                "content-type": "application/json; charset=utf-8",
                "mediahubmx-signature": sig,
            }

            all_raw_items: List[Dict[str, Any]] = []

            # Determine mirror candidate order: start with default/active domain first
            candidate_domains = [self._active_domain]
            for mirror in self.DOMAIN_MIRRORS:
                if mirror not in candidate_domains:
                    candidate_domains.append(mirror)

            for domain in candidate_domains:
                catalog_url = self.get_catalog_url(domain)
                domain_items: List[Dict[str, Any]] = []
                domain_succeeded = True

                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        for group in self.TVVOO_COUNTRY_GROUPS:
                            cursor: Any = 0
                            while True:
                                cat_body = {
                                    "language": "de",
                                    "region": "AT",
                                    "catalogId": "iptv",
                                    "id": "iptv",
                                    "adult": False,
                                    "search": "",
                                    "sort": "name",
                                    "filter": {"group": group},
                                    "cursor": cursor,
                                    "clientVersion": "3.1.0",
                                }
                                r_cat = await client.post(catalog_url, headers=cat_headers, json=cat_body)
                                if r_cat.status_code != 200:
                                    logger.warning("Vavoo catalog query to %s group %s returned HTTP %s", domain, group, r_cat.status_code)
                                    domain_succeeded = False
                                    break
                                data = r_cat.json()
                                items = data.get("items", [])
                                if not items:
                                    break
                                for it in items:
                                    it["_country_group"] = group
                                domain_items.extend(items)
                                cursor = data.get("nextCursor")
                                if not cursor:
                                    break
                            if not domain_succeeded:
                                break
                except Exception as e:
                    logger.warning("Error fetching Vavoo catalog from %s: %s", domain, e)
                    domain_succeeded = False

                if domain_succeeded and domain_items:
                    all_raw_items = domain_items
                    if domain != self._active_domain:
                        logger.info("TvVoo fallback activated: switched active domain from %s to %s", self._active_domain, domain)
                        self._active_domain = domain
                    break
                else:
                    logger.warning("TvVoo mirror %s failed or returned empty items. Trying next fallback mirror...", domain)

            if not all_raw_items:
                logger.error("All TvVoo catalog mirrors (%s) failed to provide channels.", ", ".join(candidate_domains))
                return self._channels_by_canonical

            # Group and map channels to canonical names respecting country silos
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for it in all_raw_items:
                raw_name = it.get("name", "")
                url = it.get("url") or it.get("play") or ""
                country_group = it.get("_country_group", "")
                if not url:
                    continue

                canonical = self.get_canonical_key(raw_name, country=country_group)
                if not canonical:
                    canonical = self.get_canonical_key(raw_name)
                if not canonical:
                    continue

                # Determine server/mirror tag (.c, .s, or .b)
                raw_lower = raw_name.lower()
                tag = "c" if ".c" in raw_lower else ("s" if ".s" in raw_lower else ("b" if ".b" in raw_lower else "live"))
                display_name = self.CANONICAL_DISPLAY_NAMES.get(canonical, canonical.title())

                channel_entry = {
                    "canonical": canonical,
                    "display_name": display_name,
                    "url": url,
                    "raw_name": raw_name,
                    "tag": tag,
                    "country": country_group,
                }

                # Store under canonical key
                if canonical not in grouped:
                    grouped[canonical] = []
                grouped[canonical].append(channel_entry)

                # Also store under country-scoped key (e.g. "it:sky sport uno", "de:magenta sport 3")
                scoped_key = f"{country_group.lower()[:2]}:{canonical}"
                if scoped_key not in grouped:
                    grouped[scoped_key] = []
                grouped[scoped_key].append(channel_entry)

            # Sort entries for each canonical: tag .c first, then .s, then .b
            tag_order = {"c": 0, "s": 1, "b": 2, "live": 3}
            for k in grouped:
                grouped[k].sort(key=lambda x: tag_order.get(x.get("tag", "live"), 4))

            if grouped:
                self._channels_by_canonical = grouped
                self._last_sync_time = time.time()
                logger.info(
                    "TvVoo sync complete. Indexed %d canonical sports channels across 5 countries (%d total stream URLs).",
                    len(grouped),
                    sum(len(v) for v in grouped.values()),
                )

            return self._channels_by_canonical

    async def ensure_synced(self):
        """Ensures that channel mappings are populated in RAM."""
        if not self._channels_by_canonical:
            await self.sync_channels()

    @classmethod
    def get_canonical_from_sky_id(cls, channel_id: Any) -> Optional[str]:
        """Resolves canonical channel key directly from Sky Italia channel ID (apid.sky.it)."""
        return cls.SKY_CHANNEL_IDS.get(str(channel_id).strip())

    @classmethod
    def get_canonical_from_tivusat_id(cls, channel_id: Any) -> Optional[str]:
        """Resolves canonical channel key directly from Tivùsat channel ID (services.tivulaguida.it)."""
        return cls.TIVUSAT_CHANNEL_IDS.get(str(channel_id).strip())

    def get_channel_streams(
        self,
        channel_name: Optional[str] = None,
        country: Optional[str] = None,
        sky_id: Optional[Any] = None,
        tivusat_id: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        Finds all available TvVoo stream targets for a requested TV channel.
        Supports direct official API resolution via sky_id (apid.sky.it) or tivusat_id (services.tivulaguida.it),
        or text channel_name with optional country silo scoping.
        Returns a list of dicts with 'canonical', 'url', 'display_name', 'tag', 'country'.
        """
        canonical = None
        if sky_id:
            canonical = self.get_canonical_from_sky_id(sky_id)
            if not country:
                country = "Italy"
        elif tivusat_id:
            canonical = self.get_canonical_from_tivusat_id(tivusat_id)
            if not country:
                country = "Italy"
        elif channel_name:
            canonical = self.get_canonical_key(channel_name, country=country)

        if not canonical:
            return []

        # If country is provided, check scoped key first
        if country:
            scoped_key = f"{country.lower()[:2]}:{canonical}"
            if scoped_key in self._channels_by_canonical:
                return list(self._channels_by_canonical[scoped_key])

        return list(self._channels_by_canonical.get(canonical, []))

    def has_channel(
        self,
        channel_name: Optional[str] = None,
        country: Optional[str] = None,
        sky_id: Optional[Any] = None,
        tivusat_id: Optional[Any] = None,
    ) -> bool:
        """Returns True if the requested channel exists and has at least one valid stream."""
        return len(self.get_channel_streams(channel_name=channel_name, country=country, sky_id=sky_id, tivusat_id=tivusat_id)) > 0

    def enrich_matches_with_tvvoo(self, matches: List[Dict[str, Any]]) -> int:
        """
        Attaches TvVoo high-definition streams to matches that contain recognized sports channels
        (from DaddyLive, Streamed, or external listings) across Italy, Spain, Germany, France, UK, World.
        """
        if not self._channels_by_canonical or not matches:
            return 0

        enriched_count = 0
        for m in matches:
            sources = m.get("sources")
            if not isinstance(sources, list):
                continue

            existing_urls = {s.get("url") for s in sources if isinstance(s, dict) and s.get("url")}
            existing_ids = {str(s.get("id")) for s in sources if isinstance(s, dict)}

            new_tvvoo_streams: List[Dict[str, Any]] = []

            for s in list(sources):
                if not isinstance(s, dict):
                    continue
                # If this source is from DaddyLive or another external listing, check its channel name
                s_name = s.get("name") or ""
                if not s_name or s.get("source") == "tvvoo":
                    continue

                streams = self.get_channel_streams(s_name)
                for st in streams:
                    u = st.get("url")
                    c_id = st.get("canonical")
                    # DAZN Italia (DAZN 1, DAZN 2, DAZN 3, DAZN 4):
                    # Prohibit external listings (DaddyLive, Streamed, etc.) from attaching DAZN linear streams.
                    # DAZN in Italy is strictly authoritative through official Sky EPG (apid.sky.it)!
                    if c_id in ("dazn 1", "dazn 2", "dazn 3", "dazn 4") or (c_id and c_id.startswith("dazn") and st.get("country") == "Italy"):
                        continue

                    if u and u not in existing_urls and c_id not in existing_ids:
                        new_tvvoo_streams.append({
                            "source": "tvvoo",
                            "id": c_id,
                            "name": st["display_name"],
                            "url": u,
                            "tag": st.get("tag", "c"),
                            "country": st.get("country", ""),
                        })
                        existing_urls.add(u)
                        existing_ids.add(c_id)

            if new_tvvoo_streams:
                # Prepend TvVoo streams at index 0 of match sources
                for tv_s in reversed(new_tvvoo_streams):
                    sources.insert(0, tv_s)
                enriched_count += 1

        if enriched_count > 0:
            logger.info("TvVoo: agganciati flussi FHD diretti a %d match con emittenti internazionali.", enriched_count)
        return enriched_count

    async def resolve_clean_stream_url(self, play_url: str, client_ip: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Resolves a Vavoo play URL (https://vavoo.to/play/.../index.m3u8) to a direct playable stream URL.
        """
        if not play_url or "vavoo" not in play_url:
            return None

        valid_ip = sanitize_ip(client_ip)
        sig = await self.get_vavoo_signature(valid_ip)
        if not sig:
            return None

        headers = {
            "user-agent": "MediaHubMX/2",
            "accept": "application/json",
            "content-type": "application/json; charset=utf-8",
            "accept-encoding": "gzip",
            "mediahubmx-signature": sig,
        }
        if valid_ip:
            headers["x-forwarded-for"] = valid_ip
            headers["x-real-ip"] = valid_ip

        payload = {
            "language": "de",
            "region": "AT",
            "url": play_url,
            "clientVersion": "3.1.0",
        }

        candidate_domains = [self._active_domain] + [d for d in self.DOMAIN_MIRRORS if d != self._active_domain]
        for domain in candidate_domains:
            resolve_url = f"https://{domain}/mediahubmx-resolve.json"
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    res = await client.post(resolve_url, headers=headers, json=payload)
                    if res.status_code == 200:
                        data = res.json()
                        direct_url = None
                        if isinstance(data, list) and data and data[0].get("url"):
                            direct_url = str(data[0]["url"])
                        elif isinstance(data, dict) and data.get("url"):
                            direct_url = str(data["url"])

                        if direct_url:
                            return {
                                "url": direct_url,
                                "valid_until": time.time() + 1200,
                            }
            except Exception as e:
                logger.debug("Failed resolve via %s: %s", domain, e)

        return None

    async def get_live_manifest(self, play_url: str, client_ip: Optional[str] = None) -> Optional[str]:
        """Serves rewritten M3U8 manifest with auto token renewal (No-Freeze)."""
        return await self.manifest_manager.get_live_manifest(play_url, client_ip)


tvvoo_service = TvVooService()
