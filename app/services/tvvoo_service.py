import asyncio
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional
import httpx

from app.config import ENABLE_TVVOO, TVVOO_CACHE_INTERVAL

logger = logging.getLogger("streamsport.tvvoo_service")


class TvVooService:
    """
    Manages live Italian sports channels from the Vavoo / TvVoo network.
    Performs dynamic session authentication ping and retrieves direct play URLs
    for Italian sports broadcasters (Sky Sport, DAZN, Eurosport, Rai Sport, etc.).
    Refreshes channel mappings every 6 hours (configurable via TVVOO_CACHE_INTERVAL).
    """

    PING_URL = "https://www.vypn.net/api/app/ping"
    CATALOG_URL = "https://vavoo.to/mediahubmx-catalog.json"
    RESOLVE_URL = "https://vavoo.to/mediahubmx-resolve.json"

    # Known aliases to normalize common broadcaster labels from TV schedules
    CHANNEL_ALIASES: Dict[str, str] = {
        "sky sport 1": "sky sport uno",
        "sky sport 1 hd": "sky sport uno",
        "sky 1": "sky sport uno",
        "sky sport uno": "sky sport uno",
        "sky sport calcio": "sky sport calcio",
        "sky sport calcio hd": "sky sport calcio",
        "sky calcio": "sky sport calcio",
        "sky sport tennis": "sky sport tennis",
        "sky tennis": "sky sport tennis",
        "sky sport arena": "sky sport arena",
        "sky arena": "sky sport arena",
        "sky sport f1": "sky sport f1",
        "sky f1": "sky sport f1",
        "sky sports f1": "sky sport f1",
        "sky sport motogp": "sky sport motogp",
        "sky sport moto gp": "sky sport motogp",
        "sky motogp": "sky sport motogp",
        "sky sport nba": "sky sport nba",
        "sky nba": "sky sport nba",
        "sky sport 24": "sky sport 24",
        "sky sports 24": "sky sport 24",
        "sky sport golf": "sky sport golf",
        "sky sport max": "sky sport max",
        "sky sport mix": "sky sport mix",
        "sky sport serie a": "sky sport serie a",
        "dazn": "dazn 1",
        "dazn 1": "dazn 1",
        "dazn 1 hd": "dazn 1",
        "dazn 2": "dazn 2",
        "eurosport": "eurosport 1",
        "eurosport 1": "eurosport 1",
        "eurosport 1 hd": "eurosport 1",
        "eurosport 2": "eurosport 2",
        "eurosport 2 hd": "eurosport 2",
        "rai sport": "rai sport",
        "rai sport +": "rai sport",
        "rai sport+": "rai sport",
        "rai sport + hd": "rai sport",
        "rai sport hd": "rai sport",
        "rai sport 1": "rai sport",
        "sportitalia": "sportitalia",
        "sportitalia plus": "sportitalia plus",
        "sportitalia solocalcio": "sportitalia solocalcio",
        "supertennis": "supertennis",
        "supertennis hd": "supertennis",
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
    }

    # Display names formatted nicely with proper capitalization
    CANONICAL_DISPLAY_NAMES: Dict[str, str] = {
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
    }

    def __init__(self):
        self._channels_by_canonical: Dict[str, List[Dict[str, Any]]] = {}
        self._last_sync_time: float = 0.0
        self._sync_lock = asyncio.Lock()
        self._addon_sig: Optional[str] = None
        self._sig_time: float = 0.0

    @staticmethod
    def cleanup_channel_name(name: str) -> str:
        """Cleans and normalizes channel name string for dictionary matching."""
        if not name:
            return ""
        s = name.lower()
        # Remove server tags like .c, .s, .p
        s = re.sub(r"\s*\.[a-z0-9]{1,3}\b", "", s)
        # Remove bracketed tags like [live during events only]
        s = re.sub(r"\[[^\]]*\]", "", s)
        # Remove parentheses tags
        s = re.sub(r"\([^)]*\)", "", s)
        # Remove quality markers
        s = re.sub(r"\b(hd|fhd|uhd|4k|sd|it|ita|italy)\b", "", s)
        # Normalize whitespace and punctuation
        s = re.sub(r"[^a-z0-9]+", " ", s)
        return s.strip()

    def get_canonical_key(self, channel_name: str) -> Optional[str]:
        """Maps a given channel name to a canonical key if recognized."""
        cleaned = self.cleanup_channel_name(channel_name)
        if not cleaned:
            return None
        # Exact alias match
        if cleaned in self.CHANNEL_ALIASES:
            return self.CHANNEL_ALIASES[cleaned]
        # Partial match
        for alias, canonical in self.CHANNEL_ALIASES.items():
            if alias in cleaned or cleaned in alias:
                return canonical
        return None

    async def get_vavoo_signature(self, client_ip: Optional[str] = None) -> Optional[str]:
        """Obtains session authorization signature via Vavoo ping endpoint."""
        now = time.time()
        if self._addon_sig and (now - self._sig_time) < 1800:
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
            "process": "app",
            "firstAppStart": int(now * 1000) - 86400000,
            "lastAppStart": int(now * 1000),
            "ipLocation": client_ip,
            "adblockEnabled": True,
            "migrationApplied": False,
            "migrationTargetInstalled": False,
            "proxy": {"supported": ["ss"], "engine": "Mu", "ssVersion": "2022", "enabled": False, "autoServer": True, "id": ""},
            "iap": {"supported": False, "error": ""},
        }
        headers = {
            "user-agent": "electron-fetch/1.0 electron (+https://github.com/arantes555/electron-fetch)",
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
                        logger.info("Successfully acquired new Vavoo session signature.")
                        return sig
        except Exception as e:
            logger.warning("Failed to obtain Vavoo signature: %s", e)
        return None

    async def sync_channels(self, force: bool = False) -> Dict[str, List[Dict[str, Any]]]:
        """
        Synchronizes all Italian sports channels from the live Vavoo catalog.
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
            cursor: Any = 0

            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    while True:
                        cat_body = {
                            "language": "de",
                            "region": "AT",
                            "catalogId": "iptv",
                            "id": "iptv",
                            "adult": False,
                            "search": "",
                            "sort": "name",
                            "filter": {"group": "Italy"},
                            "cursor": cursor,
                            "clientVersion": "3.1.0",
                        }
                        r_cat = await client.post(self.CATALOG_URL, headers=cat_headers, json=cat_body)
                        if r_cat.status_code != 200:
                            break
                        data = r_cat.json()
                        items = data.get("items", [])
                        if not items:
                            break
                        all_raw_items.extend(items)
                        cursor = data.get("nextCursor")
                        if not cursor:
                            break
            except Exception as e:
                logger.error("Error fetching Vavoo Italian catalog: %s", e)
                return self._channels_by_canonical

            # Group and map channels to canonical names
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for it in all_raw_items:
                raw_name = it.get("name", "")
                url = it.get("url") or it.get("play") or ""
                if not url:
                    continue

                canonical = self.get_canonical_key(raw_name)
                if not canonical:
                    continue

                # Determine server/mirror tag (.c or .s)
                tag = "c" if ".c" in raw_name.lower() else ("s" if ".s" in raw_name.lower() else "live")
                display_name = self.CANONICAL_DISPLAY_NAMES.get(canonical, canonical.title())

                channel_entry = {
                    "canonical": canonical,
                    "display_name": display_name,
                    "url": url,
                    "raw_name": raw_name,
                    "tag": tag,
                }

                if canonical not in grouped:
                    grouped[canonical] = []

                # Ensure server .c comes before server .s
                if tag == "c":
                    grouped[canonical].insert(0, channel_entry)
                else:
                    grouped[canonical].append(channel_entry)

            if grouped:
                self._channels_by_canonical = grouped
                self._last_sync_time = time.time()
                logger.info(
                    "TvVoo sync complete. Indexed %d canonical Italian sports channels (%d total stream URLs).",
                    len(grouped),
                    sum(len(v) for v in grouped.values()),
                )

            return self._channels_by_canonical

    async def ensure_synced(self):
        """Ensures that channel mappings are populated in RAM."""
        if not self._channels_by_canonical:
            await self.sync_channels()

    def get_channel_streams(self, channel_name: str) -> List[Dict[str, Any]]:
        """
        Finds all available TvVoo stream targets for a requested TV channel.
        Returns a list of dicts with 'name', 'url', 'display_name', 'tag'.
        """
        canonical = self.get_canonical_key(channel_name)
        if not canonical:
            return []
        return list(self._channels_by_canonical.get(canonical, []))

    def has_channel(self, channel_name: str) -> bool:
        """Returns True if the requested channel exists and has at least one valid stream."""
        canonical = self.get_canonical_key(channel_name)
        if not canonical:
            return False
        return len(self._channels_by_canonical.get(canonical, [])) > 0


tvvoo_service = TvVooService()
