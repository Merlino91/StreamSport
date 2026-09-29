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

    # Supported Vavoo country groups for sports broadcasting
    TVVOO_COUNTRY_GROUPS: List[str] = ["Italy", "Spain", "Germany", "France", "United Kingdom"]

    # Known aliases to normalize common broadcaster labels from TV schedules
    CHANNEL_ALIASES: Dict[str, str] = {
        # --- ITALY ---
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

        # --- SPAIN ---
        "movistar laliga": "movistar laliga",
        "laliga tv por movistar plus": "movistar laliga",
        "movistar liga de campeones": "movistar liga campeones",
        "movistar liga campeones": "movistar liga campeones",
        "movistar campeones": "movistar liga campeones",
        "movistar deportes": "movistar deportes",
        "movistar deporte 1": "movistar deportes",
        "movistar golf": "movistar golf",
        "movistar vamos": "movistar vamos",
        "movistar #vamos": "movistar vamos",
        "dazn 1 es": "dazn 1 es",
        "dazn 1 esp": "dazn 1 es",
        "dazn 1 spain": "dazn 1 es",
        "dazn 2 es": "dazn 2 es",
        "dazn 2 esp": "dazn 2 es",
        "dazn 2 spain": "dazn 2 es",
        "dazn 3 es": "dazn 3 es",
        "dazn 3 spain": "dazn 3 es",
        "dazn 4 es": "dazn 4 es",
        "dazn 4 spain": "dazn 4 es",
        "dazn spain": "dazn 1 es",
        "dazn laliga": "dazn laliga",
        "gol play": "gol play",
        "gol": "gol play",
        "teledeporte": "teledeporte",
        "tve teledeporte": "teledeporte",
        "bein sports la liga": "bein sports la liga",

        # --- GERMANY ---
        "sky sport bundesliga": "sky sport bundesliga",
        "sky bundesliga": "sky sport bundesliga",
        "sky sport top event": "sky sport top event",
        "sky top event": "sky sport top event",
        "sky sport premier league": "sky sport premier league de",
        "sky premier league de": "sky sport premier league de",
        "sky sport f1 de": "sky sport f1 de",
        "sky sport f1 germany": "sky sport f1 de",
        "sky sport tennis de": "sky sport tennis de",
        "dazn 1 de": "dazn 1 de",
        "dazn 1 germany": "dazn 1 de",
        "dazn 2 de": "dazn 2 de",
        "dazn 2 germany": "dazn 2 de",
        "dazn germany": "dazn 1 de",
        "magenta sport": "magenta sport",
        "magentatv de": "magenta sport",
        "magentatv": "magenta sport",
        "sport1": "sport1 de",
        "sport1 de": "sport1 de",
        "sport1 germany": "sport1 de",

        # --- FRANCE ---
        "canal+ sport": "canal plus sport",
        "canal sport": "canal plus sport",
        "canal+ foot": "canal plus foot",
        "canal foot": "canal plus foot",
        "canal+ 360": "canal plus 360",
        "canal 360": "canal plus 360",
        "canal+": "canal plus",
        "canal plus": "canal plus",
        "bein sports 1": "bein sports 1 fr",
        "bein sports 1 france": "bein sports 1 fr",
        "bein sports hd 1 france": "bein sports 1 fr",
        "bein sports 2": "bein sports 2 fr",
        "bein sports 2 france": "bein sports 2 fr",
        "bein sports 3": "bein sports 3 fr",
        "bein sports 3 france": "bein sports 3 fr",
        "bein sports max 4": "bein sports max 4 fr",
        "rmc sport 1": "rmc sport 1",
        "rmc sport 2": "rmc sport 2",

        # --- UNITED KINGDOM ---
        "sky sports main event": "sky sports main event",
        "sky main event": "sky sports main event",
        "sky sports premier league": "sky sports premier league uk",
        "sky sports football": "sky sports football uk",
        "sky sports f1 uk": "sky sports f1 uk",
        "sky sports cricket": "sky sports cricket uk",
        "sky sports action": "sky sports action uk",
        "sky sports arena uk": "sky sports arena uk",
        "sky sports nfl": "sky sports nfl",
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
        "bbc one": "bbc one",
        "bbc two": "bbc two",
        "itv 1": "itv 1",
        "itv 4": "itv 4",

        # --- WORLD ---
        "euroleague tv": "euroleague tv",
        "nba tv": "nba tv",
    }

    # Display names formatted nicely with proper capitalization and country flag
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

        # Spain
        "movistar laliga": "🇪🇸 Movistar LaLiga",
        "movistar liga campeones": "🇪🇸 Movistar Liga de Campeones",
        "movistar deportes": "🇪🇸 Movistar Deportes",
        "movistar golf": "🇪🇸 Movistar Golf",
        "movistar vamos": "🇪🇸 Movistar #Vamos",
        "dazn 1 es": "🇪🇸 DAZN 1 ES",
        "dazn 2 es": "🇪🇸 DAZN 2 ES",
        "dazn 3 es": "🇪🇸 DAZN 3 ES",
        "dazn 4 es": "🇪🇸 DAZN 4 ES",
        "dazn laliga": "🇪🇸 DAZN LaLiga",
        "gol play": "🇪🇸 Gol Play",
        "teledeporte": "🇪🇸 Teledeporte",
        "bein sports la liga": "🇪🇸 beIN Sports LaLiga",

        # Germany
        "sky sport bundesliga": "🇩🇪 Sky Sport Bundesliga",
        "sky sport top event": "🇩🇪 Sky Sport Top Event",
        "sky sport premier league de": "🇩🇪 Sky Sport Premier League",
        "sky sport f1 de": "🇩🇪 Sky Sport F1 DE",
        "sky sport tennis de": "🇩🇪 Sky Sport Tennis DE",
        "dazn 1 de": "🇩🇪 DAZN 1 DE",
        "dazn 2 de": "🇩🇪 DAZN 2 DE",
        "magenta sport": "🇩🇪 Magenta Sport",
        "sport1 de": "🇩🇪 Sport1 DE",

        # France
        "canal plus sport": "🇫🇷 Canal+ Sport",
        "canal plus foot": "🇫🇷 Canal+ Foot",
        "canal plus 360": "🇫🇷 Canal+ 360",
        "canal plus": "🇫🇷 Canal+",
        "bein sports 1 fr": "🇫🇷 beIN Sports 1",
        "bein sports 2 fr": "🇫🇷 beIN Sports 2",
        "bein sports 3 fr": "🇫🇷 beIN Sports 3",
        "bein sports max 4 fr": "🇫🇷 beIN Sports Max 4",
        "rmc sport 1": "🇫🇷 RMC Sport 1",
        "rmc sport 2": "🇫🇷 RMC Sport 2",

        # United Kingdom
        "sky sports main event": "🇬🇧 Sky Sports Main Event",
        "sky sports premier league uk": "🇬🇧 Sky Sports Premier League",
        "sky sports football uk": "🇬🇧 Sky Sports Football",
        "sky sports f1 uk": "🇬🇧 Sky Sports F1",
        "sky sports cricket uk": "🇬🇧 Sky Sports Cricket",
        "sky sports action uk": "🇬🇧 Sky Sports Action",
        "sky sports arena uk": "🇬🇧 Sky Sports Arena",
        "sky sports nfl": "🇬🇧 Sky Sports NFL",
        "tnt sports 1": "🇬🇧 TNT Sports 1",
        "tnt sports 2": "🇬🇧 TNT Sports 2",
        "tnt sports 3": "🇬🇧 TNT Sports 3",
        "tnt sports 4": "🇬🇧 TNT Sports 4",
        "bbc one": "🇬🇧 BBC One",
        "bbc two": "🇬🇧 BBC Two",
        "itv 1": "🇬🇧 ITV 1",
        "itv 4": "🇬🇧 ITV 4",

        # World
        "euroleague tv": "🌍 EuroLeague TV",
        "nba tv": "🌍 NBA TV",
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
        # Remove flag emojis
        s = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", s)
        # Remove server tags like .c, .s, .b, .p
        s = re.sub(r"\s*\.[a-z0-9]{1,3}\b", "", s)
        # Remove bracketed tags like [live during events only]
        s = re.sub(r"\[[^\]]*\]", "", s)
        # Remove parentheses tags
        s = re.sub(r"\([^)]*\)", "", s)
        # Remove quality markers
        s = re.sub(r"\b(fhd|uhd|4k|sd|hd)\b", "", s)
        # Normalize punctuation preserving # and +
        s = re.sub(r"[^a-z0-9#+]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    def get_canonical_key(self, channel_name: str) -> Optional[str]:
        """Maps a given channel name to a canonical key if recognized."""
        cleaned = self.cleanup_channel_name(channel_name)
        if not cleaned:
            return None
        # Exact alias match
        if cleaned in self.CHANNEL_ALIASES:
            return self.CHANNEL_ALIASES[cleaned]
        # Cleaned without symbols (+ or #)
        cleaned_no_sym = re.sub(r"[#+]", " ", cleaned).strip()
        cleaned_no_sym = re.sub(r"\s+", " ", cleaned_no_sym)
        if cleaned_no_sym in self.CHANNEL_ALIASES:
            return self.CHANNEL_ALIASES[cleaned_no_sym]
        # Partial match
        for alias, canonical in self.CHANNEL_ALIASES.items():
            if alias == cleaned or alias == cleaned_no_sym:
                return canonical
        for alias, canonical in self.CHANNEL_ALIASES.items():
            if len(alias) >= 4 and (alias in cleaned or cleaned in alias):
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
                            r_cat = await client.post(self.CATALOG_URL, headers=cat_headers, json=cat_body)
                            if r_cat.status_code != 200:
                                break
                            data = r_cat.json()
                            items = data.get("items", [])
                            if not items:
                                break
                            for it in items:
                                it["_country_group"] = group
                            all_raw_items.extend(items)
                            cursor = data.get("nextCursor")
                            if not cursor:
                                break
            except Exception as e:
                logger.error("Error fetching Vavoo multi-country catalog: %s", e)
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
                    "country": it.get("_country_group", ""),
                }

                if canonical not in grouped:
                    grouped[canonical] = []

                grouped[canonical].append(channel_entry)

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

    def get_channel_streams(self, channel_name: str) -> List[Dict[str, Any]]:
        """
        Finds all available TvVoo stream targets for a requested TV channel.
        Returns a list of dicts with 'canonical', 'url', 'display_name', 'tag', 'country'.
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


tvvoo_service = TvVooService()
