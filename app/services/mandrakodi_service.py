import asyncio
import base64
import datetime
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Set
import urllib.parse
from zoneinfo import ZoneInfo
import httpx

from app.config import (
    ENABLE_MANDRAKODI,
    ENABLE_MANDRAKODI_AMSTAFF,
    ENABLE_MANDRAKODI_ACESTREAM,
    ENABLE_MANDRAKODI_SOCCERON,
    MANDRAKODI_PASSWORD,
    MANDRAKODI_DEVICE_ID,
    MANDRAKODI_CACHE_TTL,
)
from app.services.canonical_competition_service import (
    detect_canonical_competition,
    get_canonical_display_name,
)

logger = logging.getLogger("streamsport.mandrakodi")

BASE_API_URL = "https://test34344.herokuapp.com/filter.php"
XOR_KEY = b"my_secret_key"
ROME_TZ = ZoneInfo("Europe/Rome")

HEADER_MAP: Dict[str, Tuple[str, str, str, str]] = {
    "SERIE A": ("calcio_italiano", "football", "Serie A", "Italia"),
    "SERIE B": ("calcio_italiano", "football", "Serie B", "Italia"),
    "SERIE C": ("calcio_italiano", "football", "Serie C", "Italia"),
    "COPPA ITALIA": ("calcio_italiano", "football", "Coppa Italia", "Italia"),
    "LALIGA": ("calcio_estero", "football", "La Liga", "Spagna"),
    "PREMIER": ("calcio_estero", "football", "Premier League", "Regno Unito"),
    "CHAMPIONS": ("calcio_estero", "football", "Champions League", "Europa"),
    "EUROPA": ("calcio_estero", "football", "Europa League", "Europa"),
    "CONFERENCE": ("calcio_estero", "football", "Conference League", "Europa"),
    "BUNDESLIGA": ("calcio_estero", "football", "Bundesliga", "Germania"),
    "LIGUE 1": ("calcio_estero", "football", "Ligue 1", "Francia"),
    "TENNIS": ("tennis", "tennis", "ATP/WTA Tennis", "Internazionale"),
    "WIMBLEDON": ("tennis", "tennis", "Wimbledon", "Regno Unito"),
    "ROLAND": ("tennis", "tennis", "Roland Garros", "Francia"),
    "MOTO": ("motori", "motor-sports", "MotoGP / F1", "Internazionale"),
    "F1": ("motori", "motor-sports", "Formula 1", "Internazionale"),
    "FORMULA 1": ("motori", "motor-sports", "Formula 1", "Internazionale"),
    "BALONCESTO": ("basket", "basketball", "Liga ACB", "Spagna"),
    "BASKET": ("basket", "basketball", "Basket", "Internazionale"),
    "LNP": ("basket", "basketball", "LNP Serie A2", "Italia"),
    "NBA": ("basket", "basketball", "NBA", "USA"),
}

SKY_CHID_TO_CANONICAL: Dict[str, str] = {
    "skysport24": "sky sport 24",
    "skysportuno": "sky sport uno",
    "skysporttennis": "sky sport tennis",
    "skysportarena": "sky sport arena",
    "skysportbasket": "sky sport basket",
    "skysportmax": "sky sport max",
    "skysportf1": "sky sport f1",
    "skysportmotogp": "sky sport motogp",
    "skysportgolf": "sky sport golf",
    "skysportlegend": "sky sport legend",
    "skysportmix": "sky sport mix",
    "skysportcalcio": "sky sport calcio",
    "skysport251": "sky sport 251",
    "skysport252": "sky sport 252",
    "skysport253": "sky sport 253",
    "skysport254": "sky sport 254",
    "skysport255": "sky sport 255",
    "skysport256": "sky sport 256",
    "skysport257": "sky sport 257",
    "skysport258": "sky sport 258",
    "skysport259": "sky sport 259",
}

def xor_decrypt(data_b64: str, key: bytes = XOR_KEY) -> str:
    """Decodes Base64 data and decrypts using cyclic XOR with key."""
    try:
        raw = base64.b64decode(data_b64)
        out = bytearray()
        for i, b in enumerate(raw):
            out.append(b ^ key[i % len(key)])
        return out.decode("utf-8", errors="ignore")
    except Exception as e:
        logger.debug("XOR decryption failed: %s", e)
        return ""


def extract_jwt_iat(url: str) -> Optional[int]:
    """Extracts the 'iat' (issued at) claim from a JWT token in a DAZN MPD URL."""
    if not url:
        return None
    jwt_match = re.search(r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+", url)
    if not jwt_match:
        return None
    try:
        token = jwt_match.group(0)
        p_b64 = token.split(".")[1]
        p_b64 += "=" * (-len(p_b64) % 4)
        payload = json.loads(base64.b64decode(p_b64).decode("utf-8"))
        return payload.get("iat")
    except Exception:
        return None


class MandrakodiService:
    """
    Service responsible for fetching, parsing, and reconciling Mandrakodi sources:
    - 24/7 Linear channels registry (Sky Sport 251-259, Eurosport 1-6+4K, AceStream, MPD)
      treated as stream sources for matching events (analogous to TvVoo).
    - Dynamic OTT live matches from A1A103 with strict 6-hour JWT 'iat' purge.
    - Italian web events from Socceron A1A156 with 6-hour kickoff purge.
    - Dynamic Sky Sport ClearKey on-demand resolution via A1A159.
    """

    def __init__(self):
        self._cached_matches: List[Dict[str, Any]] = []
        self._last_fetch_time: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()
        # 24/7 channels indexed by canonical name (e.g. 'sky sport uno', 'tnt sports 1')
        self._channels_by_canonical: Dict[str, List[Dict[str, Any]]] = {}
        self._last_channels_sync: float = 0.0
        self._channels_lock: asyncio.Lock = asyncio.Lock()
        # Cache for dynamically resolved Sky Sport channels: {channel_id: (resolved_dict, expiry_ts)}
        self._sky_channel_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}
        # Persistent HTTP client pool for keep-alive connection reuse
        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None
        self._client_lock: asyncio.Lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        """Returns or lazily initializes the shared HTTP client with connection pooling."""
        try:
            cur_loop = asyncio.get_running_loop()
        except RuntimeError:
            cur_loop = None

        if self._client is None or self._client.is_closed or self._client_loop != cur_loop:
            if self._client_loop != cur_loop:
                self._client_lock = asyncio.Lock()
            async with self._client_lock:
                if self._client is None or self._client.is_closed or self._client_loop != cur_loop:
                    self._client = httpx.AsyncClient(
                        timeout=httpx.Timeout(12.0, connect=6.0),
                        verify=False,
                        follow_redirects=True,
                        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=60.0),
                    )
                    self._client_loop = cur_loop
        return self._client

    async def close(self) -> None:
        """Closes the shared HTTP client pool gracefully on application shutdown."""
        client = self._client
        c_loop = self._client_loop
        self._client = None
        self._client_loop = None
        if client and not client.is_closed:
            try:
                cur_loop = asyncio.get_running_loop()
                if c_loop is None or c_loop == cur_loop:
                    await client.aclose()
            except Exception:
                pass

    def _get_headers(self) -> Dict[str, str]:
        pwd = MANDRAKODI_PASSWORD
        dev_id = MANDRAKODI_DEVICE_ID
        return {
            "User-Agent": f"MandraKodi2@@1.2.84@@{pwd}@@{dev_id}",
            "Accept": "application/json, text/plain, */*",
        }

    async def _fetch_endpoint(self, num_test: str, params: Optional[Dict[str, str]] = None) -> Any:
        """Helper to fetch a specific numTest from Mandrakodi using the shared client."""
        url = f"{BASE_API_URL}?numTest={num_test}"
        if params:
            url += "&" + urllib.parse.urlencode(params)
        try:
            client = await self._get_client()
            r = await client.get(url, headers=self._get_headers())
            if r.status_code == 200:
                return r.json()
            logger.warning("Mandrakodi query numTest=%s returned HTTP %s", num_test, r.status_code)
        except Exception as e:
            logger.warning("Mandrakodi query error for numTest=%s: %s", num_test, e)
        return None

    # =========================================================================
    # 24/7 LINEAR CHANNELS REGISTRY (Enrichment Engine, identical to TvVoo)
    # =========================================================================

    def get_canonical_channel_key(self, channel_name: str, country: Optional[str] = None) -> Optional[str]:
        """
        Resolves raw channel names to canonical keys matching TvVoo's naming conventions.
        """
        if not channel_name:
            return None

        # Clean punctuation and tags
        cleaned = re.sub(r"\[.*?\]|\(.*?\)", "", channel_name).strip()
        cleaned_lower = cleaned.lower()

        # Check explicit Sky channel IDs
        for chid, can in SKY_CHID_TO_CANONICAL.items():
            if chid in cleaned_lower or can in cleaned_lower:
                return can

        # Delegate to TvVoo's canonical resolver for multi-country matching
        from app.services.tvvoo_service import tvvoo_service
        can_tvvoo = tvvoo_service.get_canonical_key(cleaned, country=country)
        if can_tvvoo:
            return can_tvvoo

        # Direct string normalization fallback
        norm = re.sub(r"[^a-z0-9]+", " ", cleaned_lower).strip()
        if "eurosport 4k" in norm:
            return "eurosport 4k"
        if "eurosport 1" in norm:
            return "eurosport 1"
        if "eurosport 2" in norm:
            return "eurosport 2"
        if "eurosport 3" in norm:
            return "eurosport 3"
        if "eurosport 4" in norm:
            return "eurosport 4"
        if "eurosport 5" in norm:
            return "eurosport 5"
        if "eurosport 6" in norm:
            return "eurosport 6"
        if "m golf" in norm or "movistar golf" in norm:
            return "m+ golf"
        if "dazn baloncesto 1" in norm:
            return "dazn baloncesto 1"
        if "dazn baloncesto 2" in norm:
            return "dazn baloncesto 2"
        if "dazn baloncesto 3" in norm:
            return "dazn baloncesto 3"
        if "supertennis" in norm:
            return "supertennis"
        if "inter tv" in norm:
            return "inter tv"
        if "milan tv" in norm:
            return "milan tv"
        if "rai sport" in norm:
            return "rai sport"
        if "dmax" in norm:
            return "dmax"
        if "nba tv" in norm:
            return "nba tv"
        if "zona dazn" in norm:
            return "dazn 1"
        if "20 mediaset" in norm or "mediaset 20" in norm:
            return "20 mediaset"
        if "rai 4k" in norm:
            return "rai 4k"
        if "canale 5" in norm:
            return "canale 5"
        if "italia 1" in norm:
            return "italia 1"
        if "rai 1" in norm:
            return "rai 1"
        if "rai 2" in norm:
            return "rai 2"
        if "rai 3" in norm:
            return "rai 3"
        if "tv8" in norm:
            return "tv8"
        if "rsi la1" in norm or "rsi la 1" in norm:
            return "rsi la1"
        if "rsi la2" in norm or "rsi la 2" in norm:
            return "rsi la2"

        return norm if norm in self._channels_by_canonical else None

    def get_channel_streams(self, canonical_key: str) -> List[Dict[str, Any]]:
        """Returns all registered Mandrakodi streams for a canonical channel."""
        if not canonical_key:
            return []
        return list(self._channels_by_canonical.get(canonical_key.strip().lower(), []))

    def enrich_matches_with_mandrakodi(self, matches: List[Dict[str, Any]]) -> int:
        """
        Attaches Mandrakodi linear channel streams (Amstaff ClearKey 1080p 50fps and AceStream)
        to matches in the catalog that list recognized broadcast channels
        (from DaddyLive, Italian EPG, or Diretta).
        Operates exactly like TvVoo enrichment.
        """
        if not self._channels_by_canonical or not matches:
            return 0

        enriched_count = 0
        for m in matches:
            sources = m.get("sources")
            if not isinstance(sources, list):
                continue

            existing_stream_ids = {str(s.get("id")) for s in sources if isinstance(s, dict)}
            existing_sky_ids = {str(s.get("sky_channel_id")) for s in sources if isinstance(s, dict) and s.get("sky_channel_id")}

            # Identify target channels from existing source names
            target_canonicals: Set[str] = set()

            for s in list(sources):
                if not isinstance(s, dict):
                    continue
                # Skip mandrakodi sources to avoid self-enrichment loops
                if s.get("provider") == "mandrakodi":
                    continue
                s_name = s.get("name") or ""
                if not s_name:
                    continue

                can = self.get_canonical_channel_key(s_name)
                if can:
                    target_canonicals.add(can)

            # Also check _epg_channels or _diretta_tv_channels if present
            for ch in m.get("_diretta_tv_channels", []):
                can = self.get_canonical_channel_key(ch)
                if can:
                    target_canonicals.add(can)

            # Attach matching Mandrakodi streams
            for can in target_canonicals:
                streams = self.get_channel_streams(can)
                for st in streams:
                    st_id = str(st.get("id") or "")
                    sky_id = str(st.get("sky_channel_id") or "")
                    if (st_id and st_id in existing_stream_ids) or (sky_id and sky_id in existing_sky_ids):
                        continue

                    # Copy and attach
                    attached = dict(st)
                    sources.append(attached)
                    if st_id:
                        existing_stream_ids.add(st_id)
                    if sky_id:
                        existing_sky_ids.add(sky_id)
                    enriched_count += 1

        if enriched_count > 0:
            logger.info("Mandrakodi: enriched %d match entries with high-definition linear streams.", enriched_count)
        return enriched_count

    def _parse_amstaff_payload(self, raw_payload: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Decodes an amstaff payload and detects whether it is an encrypted ClearKey stream (amstaff)
        or an unencrypted public stream (direct HLS).
        Returns (source_type, stream_meta).
        """
        try:
            dec = raw_payload if raw_payload.startswith("http") else base64.b64decode(raw_payload).decode("utf-8", errors="ignore")
            parts = dec.split("|")
            mpd_url = parts[0].strip()
            key_part = parts[1].strip() if len(parts) > 1 else ""
            kid, key = key_part.split(":", 1) if ":" in key_part else ("", "")

            # Detect unencrypted clear streams (e.g. key == '0000', empty keys, or '-clr.isml')
            is_clear = (
                key_part in ("0000", "0", "none", "clear", "")
                or (not kid and not key)
                or "-clr.isml" in mpd_url
            )
            if is_clear:
                hls_url = mpd_url.replace("/manifest.mpd", "/manifest.m3u8").replace(".mpd", ".m3u8")
                return "direct", {
                    "stream_url": hls_url,
                    "original_mpd": mpd_url,
                    "is_clear": True,
                }

            return "amstaff", {
                "mpd_url": mpd_url,
                "kid": kid,
                "key": key,
            }
        except Exception as e:
            logger.debug("Failed to decode amstaff payload: %s", e)
            return None, {}

    async def sync_channels(self, force: bool = False) -> None:
        """
        Loads and indexes all 24/7 linear channels from A1A165 (Sky), A1A134F (MPD),
        and A1A103 (AceStream and linear MPD channels).
        """
        now = time.time()
        if not force and self._channels_by_canonical and (now - self._last_channels_sync < 1800):
            return

        async with self._channels_lock:
            if not force and self._channels_by_canonical and (now - self._last_channels_sync < 1800):
                return

            channels_map: Dict[str, List[Dict[str, Any]]] = {}

            # 1. Sky Sport linear channels (A1A165)
            if ENABLE_MANDRAKODI_AMSTAFF:
                sky_data = await self._fetch_endpoint("A1A165")
                if sky_data and isinstance(sky_data, dict):
                    for it in sky_data.get("items", []):
                        raw_title = it.get("title", "")
                        clean_title = re.sub(r"\[.*?\]", "", raw_title).strip()
                        myresolve = it.get("myresolve", "")
                        if not myresolve.startswith("sky@@"):
                            continue
                        chid = myresolve.replace("sky@@", "").strip()
                        canonical = SKY_CHID_TO_CANONICAL.get(chid, f"sky sport {clean_title.lower()}")
                        stream_entry = {
                            "source": "amstaff",
                            "provider": "mandrakodi",
                            "id": f"mandrakodi:sky:{chid}",
                            "name": f"Sky {clean_title}",
                            "sky_channel_id": chid,
                            "country": "Italia",
                            "language": "ita",
                            "quality": "1080p 50fps",
                        }
                        channels_map.setdefault(canonical, []).append(stream_entry)

            # 2. Curated Sports Channels (A1A134F)
            f_data = await self._fetch_endpoint("A1A134F")
            if f_data and isinstance(f_data, dict):
                TARGET_KEYWORDS = (
                    "EUROSPORT",
                    "INTER TV",
                    "MILAN TV",
                    "RAI SPORT",
                    "RSI LA1",
                    "RSI LA2",
                    "TNT SPORT",
                    "ELEVEN SPORT",
                    "BEIN SPORT",
                    "SPORT TV",
                    "DMAX",
                    "NBA TV",
                )
                for it in f_data.get("items", []):
                    raw_title = it.get("title", "")
                    clean_title = re.sub(r"\[.*?\]", "", raw_title).strip()
                    link = it.get("myresolve") or it.get("link") or ""
                    t_upper = clean_title.upper()
                    if not any(k in t_upper for k in TARGET_KEYWORDS):
                        continue

                    source_type = None
                    stream_meta: Dict[str, Any] = {}
                    if "amstaff@@" in link and ENABLE_MANDRAKODI_AMSTAFF:
                        source_type, stream_meta = self._parse_amstaff_payload(link.split("amstaff@@", 1)[1])
                        if not source_type:
                            continue
                    elif link.startswith("http"):
                        source_type = "direct"
                        stream_meta = {"stream_url": link.strip()}
                    else:
                        continue

                    canonical = self.get_canonical_channel_key(clean_title)
                    if not canonical:
                        continue

                    country = "Svizzera" if "RSI" in t_upper else ("Regno Unito" if "TNT" in t_upper else "Italia")
                    stream_entry = {
                        "source": source_type,
                        "provider": "mandrakodi",
                        "id": stream_meta.get("mpd_url") or stream_meta.get("stream_url"),
                        "name": clean_title,
                        "stream_data": stream_meta,
                        "country": country,
                        "language": "ita" if country in ("Italia", "Svizzera") else "eng",
                        "quality": "4K" if "4K" in t_upper else "1080p 50fps",
                    }
                    channels_map.setdefault(canonical, []).append(stream_entry)

            # 3. 24/7 Channels from A1A103 (AceStream and MPD channels)
            lm_data = await self._fetch_endpoint("A1A103")
            if lm_data and isinstance(lm_data, dict):
                is_ch_section = False
                for it in lm_data.get("items", []):
                    t = it.get("title", "")
                    link = it.get("myresolve") or it.get("link") or ""
                    if "===" in t:
                        clean_h = re.sub(r"\[.*?\]|===", "", t).strip().upper()
                        is_ch_section = any(k in clean_h for k in [
                            "BALONCESTO", "CANALI SPORT", "FORMULA 1", "GOLF", "PALLACANESTRO", "TENNIS", "TEST"
                        ])
                        continue

                    if not is_ch_section or not link or link == "ignore":
                        continue

                    clean_title = re.sub(r"\[.*?\]", "", t).strip()
                    source_type = None
                    stream_meta = {}

                    if link.startswith("acestream://") and ENABLE_MANDRAKODI_ACESTREAM:
                        source_type = "acestream"
                        ace_id = link.replace("acestream://", "").strip()
                        stream_meta = {"ace_id": ace_id}
                    elif "amstaff@@" in link and ENABLE_MANDRAKODI_AMSTAFF:
                        source_type, stream_meta = self._parse_amstaff_payload(link.split("amstaff@@", 1)[1])
                        if not source_type:
                            continue

                    if not source_type:
                        continue

                    canonical = self.get_canonical_channel_key(clean_title)
                    if not canonical:
                        continue

                    stream_entry = {
                        "source": source_type,
                        "provider": "mandrakodi",
                        "id": stream_meta.get("ace_id") or stream_meta.get("mpd_url"),
                        "name": clean_title,
                        "stream_data": stream_meta,
                        "country": "Internazionale",
                        "language": "ita" if "(ITA" in t else ("spa" if "(SPA" in t or "(ES" in t else "eng"),
                        "quality": "4K" if "4K" in clean_title else "1080p",
                    }
                    channels_map.setdefault(canonical, []).append(stream_entry)

            # 4. AceStream Channels from A1A134C (Zona DAZN, Eleven Sports, etc.)
            if ENABLE_MANDRAKODI_ACESTREAM:
                ace_data = await self._fetch_endpoint("A1A134C")
                if ace_data and isinstance(ace_data, dict):
                    for it in ace_data.get("items", []):
                        raw_title = it.get("title", "")
                        clean_title = re.sub(r"\[.*?\]", "", raw_title).strip()
                        link = it.get("myresolve") or it.get("link") or ""
                        if not link.startswith("acestream://"):
                            continue
                        ace_id = link.replace("acestream://", "").strip()
                        if not ace_id:
                            continue

                        canonical = self.get_canonical_channel_key(clean_title)
                        if not canonical:
                            continue

                        if any(s.get("id") == ace_id for s in channels_map.get(canonical, [])):
                            continue

                        t_upper = raw_title.upper()
                        country = "Italia" if ("(ITA" in t_upper or "DAZN" in clean_title.upper()) else "Internazionale"
                        is_4k = "4K" in clean_title.upper() or "[4K]" in raw_title.upper()
                        stream_entry = {
                            "source": "acestream",
                            "provider": "mandrakodi",
                            "id": ace_id,
                            "name": clean_title,
                            "stream_data": {"ace_id": ace_id},
                            "country": country,
                            "language": "ita" if country == "Italia" else "eng",
                            "quality": "4K" if is_4k else "1080p",
                        }
                        channels_map.setdefault(canonical, []).append(stream_entry)

            # 5. National Italian Broadcasters & DTT (A1A215: Mediaset 20 ClearKey, Rai 4K Direct, Canale 5, Italia 1, etc.)
            ita_data = await self._fetch_endpoint("A1A215")
            if ita_data and isinstance(ita_data, dict):
                for it in ita_data.get("items", []):
                    raw_title = it.get("title", "")
                    clean_title = re.sub(r"\[.*?\]", "", raw_title).strip()
                    link = it.get("myresolve") or it.get("link") or ""
                    if not link or link == "ignore" or link.startswith("skyTV@@"):
                        continue

                    source_type = None
                    stream_meta: Dict[str, Any] = {}
                    if "amstaff@@" in link and ENABLE_MANDRAKODI_AMSTAFF:
                        source_type, stream_meta = self._parse_amstaff_payload(link.split("amstaff@@", 1)[1])
                        if not source_type:
                            continue
                    elif link.startswith("http"):
                        source_type = "direct"
                        stream_meta = {"stream_url": link.strip()}
                    else:
                        continue

                    canonical = self.get_canonical_channel_key(clean_title)
                    if not canonical:
                        continue

                    st_id = stream_meta.get("mpd_url") or stream_meta.get("stream_url")
                    if st_id and any(s.get("id") == st_id for s in channels_map.get(canonical, [])):
                        continue

                    t_upper = clean_title.upper()
                    country = "Svizzera" if "RSI" in t_upper else "Italia"
                    is_4k = "4K" in t_upper
                    stream_entry = {
                        "source": source_type,
                        "provider": "mandrakodi",
                        "id": st_id,
                        "name": clean_title,
                        "stream_data": stream_meta,
                        "country": country,
                        "language": "ita",
                        "quality": "4K" if is_4k else "1080p 50fps",
                    }
                    channels_map.setdefault(canonical, []).append(stream_entry)

            # Prioritize Italian streams at the top of each canonical channel's list
            for can, s_list in channels_map.items():
                s_list.sort(key=lambda s: 0 if (s.get("country") == "Italia" or s.get("language") == "ita") else 1)

            self._channels_by_canonical = channels_map
            self._last_channels_sync = time.time()
            logger.info("Mandrakodi: synchronized 24/7 channel registry (%d canonical channels mapped).", len(channels_map))

    # =========================================================================
    # DYNAMIC LIVE MATCHES (A1A103 Last Minute & A1A156 Socceron)
    # =========================================================================

    def get_sync_interval(self) -> int:
        """
        Dynamic sync interval based on Rome time:
        - 12:00 to 22:59 (12 <= hour <= 22): 900 seconds (15 minutes).
        - 23:00 to 11:59: 3600 seconds (1 hour).
        """
        now_rome = datetime.datetime.now(ROME_TZ)
        if 12 <= now_rome.hour <= 22:
            return 900
        return 3600

    async def get_matches(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Fetches fresh dynamic OTT live matches from Mandrakodi.
        Applies a strict 6-hour purge rule on JWT token creation ('iat') and kickoff time.
        Ensures 24/7 linear channels are updated in memory without cluttering match catalog.
        Applies dynamic caching (15m peak 12-22, 60m off-peak) unless forced.
        """
        if not ENABLE_MANDRAKODI:
            return []

        now = time.time()
        interval = self.get_sync_interval()
        if not force and self._cached_matches and (now - self._last_fetch_time < interval):
            return self._cached_matches

        async with self._lock:
            if not force and self._cached_matches and (now - self._last_fetch_time < interval):
                return self._cached_matches

            try:
                # 1. Ensure 24/7 linear channels are loaded
                await self.sync_channels(force=force)

                # 2. Concurrently fetch dynamic matches
                tasks = [self._fetch_last_minute()]
                if ENABLE_MANDRAKODI_SOCCERON:
                    tasks.append(self._fetch_socceron())

                results = await asyncio.gather(*tasks, return_exceptions=True)

                all_matches: List[Dict[str, Any]] = []
                for res in results:
                    if isinstance(res, list):
                        all_matches.extend(res)
                    elif isinstance(res, Exception):
                        logger.error("Error fetching Mandrakodi dynamic matches: %s", res)

                self._cached_matches = all_matches
                self._last_fetch_time = time.time()
                logger.info(
                    "Mandrakodi live match sync complete: %d active matches retained (older >6h purged).",
                    len(all_matches),
                )
                return all_matches
            except Exception as e:
                logger.error("Mandrakodi sync failed: %s", e)
                return self._cached_matches

    async def resolve_live_match_streams(self, match: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Just-In-Time resolution triggered when a user clicks on an active or pre-match card.
        Fetches fresh dynamic events from Mandrakodi (A1A103/A1A156) if older than 60s,
        reconciles the catalog in memory, resets the periodic sync timer, and returns
        matching Mandrakodi stream sources for this match.
        """
        if not match:
            return []

        now = time.time()
        # Cooldown check: if refreshed within last 60 seconds, use cached matches
        if (now - self._last_fetch_time) > 60:
            await self.get_matches(force=True)
            # Reconcile catalog in background/memory and reset 15m timer
            from app.services.catalog_service import catalog_service
            catalog_service.reconcile_mandrakodi_live_matches(self._cached_matches)

        if not self._cached_matches:
            return []

        # Find matching Mandrakodi match
        m_title = (match.get("title") or "").strip()
        m_teams = match.get("teams") or {}
        m_home = (m_teams.get("home") or "").strip().lower()
        m_away = (m_teams.get("away") or "").strip().lower()

        # Token set for comparison
        clean_tokens = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", m_title.lower()))

        for km in self._cached_matches:
            km_title = (km.get("title") or "").strip().lower()
            km_teams = km.get("teams") or {}
            km_home = (km_teams.get("home") or "").strip().lower()
            km_away = (km_teams.get("away") or "").strip().lower()

            matched = False
            # 1. Exact or partial home & away team match
            if m_home and m_away and km_home and km_away:
                if (m_home in km_home or km_home in m_home) and (m_away in km_away or km_away in m_away):
                    matched = True
                elif (m_home in km_away or km_away in m_home) and (m_away in km_home or km_home in m_away):
                    matched = True

            # 2. Token overlap if teams match is not conclusive
            if not matched and clean_tokens:
                km_tokens = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", km_title))
                overlap = clean_tokens.intersection(km_tokens)
                if len(overlap) >= 2 or (len(clean_tokens) == 1 and len(overlap) == 1):
                    matched = True

            if matched:
                return [s for s in km.get("sources", []) if isinstance(s, dict)]

        return []

    async def _fetch_last_minute(self) -> List[Dict[str, Any]]:
        """
        Fetches numTest=A1A103 (Last Minute events).
        Extracts only dynamic match events, strictly skipping linear 24/7 channel sections.
        Enforces a hard 6-hour purge rule based on JWT 'iat' and Rome kickoff time.
        """
        data = await self._fetch_endpoint("A1A103")
        if not data or not isinstance(data, dict):
            return []

        raw_items = data.get("items", [])
        if not raw_items:
            return []

        current_catalog = "altri_sport"
        current_silo = "other"
        current_comp = "Varie"
        current_country = "Internazionale"
        now_dt = datetime.datetime.now(ROME_TZ)
        now_ts = now_dt.timestamp()

        is_ch_section = False
        matches: List[Dict[str, Any]] = []

        for it in raw_items:
            t = it.get("title", "").strip()
            link = it.get("myresolve") or it.get("link") or it.get("externallink") or ""

            # 1. Separator detection
            if "===" in t:
                clean_h = re.sub(r"\[.*?\]|===", "", t).strip().upper()
                is_ch_section = any(k in clean_h for k in [
                    "BALONCESTO", "CANALI SPORT", "FORMULA 1", "GOLF", "PALLACANESTRO", "TENNIS", "TEST"
                ])
                matched = False
                for k, (cat, silo, comp, country) in HEADER_MAP.items():
                    if k in clean_h:
                        current_catalog, current_silo, current_comp, current_country = cat, silo, comp, country
                        matched = True
                        break
                if not matched:
                    current_comp = clean_h if clean_h else "Varie"
                    current_country = "Internazionale"
                    current_silo = "other"
                    current_catalog = "altri_sport"
                continue

            # 2. Skip non-event items and linear channel sections
            if is_ch_section or not link or link == "ignore":
                continue

            t_clean = re.sub(r"\[.*?\]", "", t).strip()
            if not t_clean or t_clean.lower() in ("last minute", "nessun evento", "ignore"):
                continue

            # Skip known TV show recordings (e.g. Propaganda Live)
            if "propaganda live" in t_clean.lower():
                continue

            # 3. Clean event title and extract home/away teams
            name = re.sub(r"\b\d{1,2}:\d{2}\b", "", t_clean)
            name = re.sub(r"\(.*?\)", "", name).strip()
            name = re.sub(r"^\d+\s*-\s*\d+\s*-\s*\d+\s*-\s*\d+\s*\|\s*", "", name).strip()

            home, away = None, None
            for sep in (" - ", " vs ", " v "):
                if sep in name:
                    p = name.split(sep, 1)
                    home = p[0].strip()
                    away = p[1].strip()
                    break

            # If no vs/separator and no home/away, it is a non-match item -> skip
            if not home or not away:
                continue

            # 4. Extract stream payload
            source_type = None
            stream_meta: Dict[str, Any] = {}

            if "amstaff@@" in link and ENABLE_MANDRAKODI_AMSTAFF:
                source_type, stream_meta = self._parse_amstaff_payload(link.split("amstaff@@", 1)[1])
                if not source_type:
                    continue

            elif link.startswith("acestream://") and ENABLE_MANDRAKODI_ACESTREAM:
                source_type = "acestream"
                ace_id = link.replace("acestream://", "").strip()
                stream_meta = {"ace_id": ace_id}

            elif link.startswith("http"):
                source_type = "direct"
                stream_meta = {"stream_url": link.strip()}

            if not source_type:
                continue

            # 5. SIX-HOUR PURGE FILTER
            # Level A: Check DAZN JWT 'iat'
            mpd_url = stream_meta.get("mpd_url", "")
            iat = extract_jwt_iat(mpd_url)
            if iat:
                age_seconds = now_ts - iat
                if age_seconds > (6 * 3600):
                    logger.debug("Mandrakodi: purged stale DAZN match %s (iat is %.1f hours old)", name, age_seconds / 3600)
                    continue

            # Level B: Check Kickoff time in title (parsed explicitly in Europe/Rome)
            time_match = re.search(r"\b(\d{1,2}:\d{2})\b", t_clean)
            if time_match:
                try:
                    hh, mm = map(int, time_match.group(1).split(":"))
                    event_dt = datetime.datetime(now_dt.year, now_dt.month, now_dt.day, hh, mm, tzinfo=ROME_TZ)
                    event_ts = event_dt.timestamp()
                    ts_ms = int(event_ts * 1000)
                    # If kickoff was more than 6 hours ago, purge
                    if (now_ts - event_ts) > (6 * 3600):
                        logger.debug("Mandrakodi: purged concluded match %s (kickoff was %.1f hours ago)", name, (now_ts - event_ts) / 3600)
                        continue
                except Exception:
                    continue
            else:
                # If non-JWT event has no time in title, do not generate fake match
                continue

            event_title = f"{home} vs {away}"
            canon_comp = detect_canonical_competition(f"{event_title} {current_comp}", current_silo) or current_comp
            display_comp = get_canonical_display_name(canon_comp)

            # Unique deterministic ID
            m_hash = (hash(name.lower() + str(ts_ms // 3600000)) & 0xFFFFFFFF)
            match_id = f"mandrakodi:lm:{m_hash:08x}"

            quality_label = "1080p 50fps" if source_type == "amstaff" else ("4K" if "4K" in t_clean else "1080p")
            lang_label = "ita" if "ITA" in t_clean.upper() else "eng"

            match_dict: Dict[str, Any] = {
                "id": match_id,
                "title": event_title,
                "category": current_silo,
                "_silo": current_silo,
                "_catalog": current_catalog,
                "date": ts_ms,
                "competition": display_comp,
                "_competition": display_comp,
                "country": current_country,
                "_country": current_country,
                "teams": {"home": {"name": home}, "away": {"name": away}},
                "sources": [
                    {
                        "source": source_type,
                        "provider": "mandrakodi",
                        "id": stream_meta.get("mpd_url") or stream_meta.get("ace_id"),
                        "name": f"Mandrakodi {source_type.upper()}",
                        "stream_data": stream_meta,
                        "country": current_country,
                        "language": lang_label,
                        "quality": quality_label,
                    }
                ],
                "_provider": "mandrakodi",
                "_mandrakodi_type": "last_minute",
            }
            matches.append(match_dict)

        return matches

    async def _fetch_socceron(self) -> List[Dict[str, Any]]:
        """
        Fetches Italian web events from numTest=A1A156 (Socceron), if active.
        Enforces a hard 6-hour purge rule on kickoff time.
        """
        data = await self._fetch_endpoint("A1A156")
        if not data:
            return []

        raw_items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if not raw_items:
            return []

        matches: List[Dict[str, Any]] = []
        now_dt = datetime.datetime.now(ROME_TZ)
        now_ts = now_dt.timestamp()

        for it in raw_items:
            t = it.get("title", "").strip()
            link = it.get("myresolve") or it.get("link") or ""
            clean_title = re.sub(r"\[.*?\]", "", t).strip()
            if not clean_title or not link:
                continue

            # Time extraction in Europe/Rome
            time_match = re.search(r"\b(\d{1,2}:\d{2})\b", clean_title)
            if time_match:
                try:
                    hh, mm = map(int, time_match.group(1).split(":"))
                    event_dt = datetime.datetime(now_dt.year, now_dt.month, now_dt.day, hh, mm, tzinfo=ROME_TZ)
                    event_ts = event_dt.timestamp()
                    ts_ms = int(event_ts * 1000)
                    if (now_ts - event_ts) > (6 * 3600):
                        continue
                except Exception:
                    ts_ms = int(now_ts * 1000)
            else:
                ts_ms = int(now_ts * 1000)

            name = re.sub(r"\b\d{1,2}:\d{2}\b", "", clean_title).strip()
            home, away = None, None
            for sep in (" - ", " vs ", " v "):
                if sep in name:
                    p = name.split(sep, 1)
                    home = p[0].strip()
                    away = p[1].strip()
                    break

            if not home or not away:
                continue

            mid = (hash("socceron:" + clean_title) & 0xFFFFFFFF)
            match_dict: Dict[str, Any] = {
                "id": f"mandrakodi:soc:{mid:08x}",
                "title": f"{home} vs {away}",
                "category": "football",
                "_silo": "football",
                "_catalog": "calcio_italiano",
                "date": ts_ms,
                "competition": "Calcio Italiano (Socceron)",
                "_competition": "Calcio Italiano (Socceron)",
                "country": "Italia",
                "_country": "Italia",
                "teams": {"home": {"name": home}, "away": {"name": away}},
                "sources": [
                    {
                        "source": "socceron",
                        "provider": "mandrakodi",
                        "id": link,
                        "name": f"Socceron {clean_title}",
                        "stream_data": {"url": link},
                        "country": "Italia",
                        "language": "ita",
                        "quality": "720p",
                    }
                ],
                "_provider": "mandrakodi",
                "_mandrakodi_type": "socceron",
            }
            matches.append(match_dict)

        return matches

    async def resolve_sky_channel(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """
        Dynamically resolves the live MPD manifest and ClearKey for a Sky channel (A1A159).
        Includes in-memory cache until key expiry (fine).
        """
        now = time.time()
        if channel_id in self._sky_channel_cache:
            data, exp = self._sky_channel_cache[channel_id]
            if now < exp:
                return data

        res = await self._fetch_endpoint("A1A159", {"id": channel_id})
        if not res or not isinstance(res, dict) or not res.get("data"):
            logger.warning("Could not resolve Sky Sport channel %s", channel_id)
            return None

        decrypted_json = xor_decrypt(res["data"])
        if not decrypted_json:
            return None

        try:
            parsed = json.loads(decrypted_json)
            manifest = parsed.get("manifest")
            kid = parsed.get("kid")
            key = parsed.get("key")
            fine_str = parsed.get("fine")  # e.g. "21/10/2026 18:20:06"

            # Parse expiry time or default to 6 hours
            exp_ts = now + (6 * 3600)
            if fine_str:
                try:
                    dt = datetime.datetime.strptime(fine_str, "%d/%m/%Y %H:%M:%S")
                    exp_ts = dt.replace(tzinfo=ROME_TZ).timestamp()
                except Exception:
                    pass

            result = {
                "manifest": manifest,
                "kid": kid,
                "key": key,
                "fine": fine_str,
            }
            self._sky_channel_cache[channel_id] = (result, exp_ts)
            logger.info("Resolved Sky channel %s: KID=%s (valid until %s)", channel_id, kid, fine_str)
            return result
        except Exception as e:
            logger.error("Failed to parse resolved JSON for channel %s: %s", channel_id, e)
            return None


mandrakodi_service = MandrakodiService()
