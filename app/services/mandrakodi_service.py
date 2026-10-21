import asyncio
import base64
import datetime
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
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


class MandrakodiService:
    """
    Service responsible for fetching and parsing live sports events,
    linear Sky Sport channels, and MPD ClearKey feeds from the Mandrakodi API.
    """

    def __init__(self):
        self._cached_matches: List[Dict[str, Any]] = []
        self._last_fetch_time: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()
        # Cache for dynamically resolved Sky Sport channels: {channel_id: (resolved_dict, expiry_ts)}
        self._sky_channel_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}

    def _get_headers(self) -> Dict[str, str]:
        pwd = MANDRAKODI_PASSWORD or "UDvXkJqTBR"
        dev_id = MANDRAKODI_DEVICE_ID or "ABC123XYZ456"
        return {
            "User-Agent": f"MandraKodi2@@1.2.84@@{pwd}@@{dev_id}",
            "Accept": "application/json, text/plain, */*",
        }

    async def _fetch_endpoint(self, num_test: str, params: Optional[Dict[str, str]] = None) -> Any:
        """Helper to fetch a specific numTest from Mandrakodi."""
        url = f"{BASE_API_URL}?numTest={num_test}"
        if params:
            url += "&" + urllib.parse.urlencode(params)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(12.0, connect=6.0),
                verify=False,
                follow_redirects=True,
            ) as client:
                r = await client.get(url, headers=self._get_headers())
                if r.status_code == 200:
                    return r.json()
                logger.warning("Mandrakodi query numTest=%s returned HTTP %s", num_test, r.status_code)
        except Exception as e:
            logger.warning("Mandrakodi query error for numTest=%s: %s", num_test, e)
        return None

    async def get_matches(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Fetches all current matches and channels from Mandrakodi.
        Includes caching to prevent redundant requests.
        """
        if not ENABLE_MANDRAKODI:
            return []

        now = time.time()
        if not force and self._cached_matches and (now - self._last_fetch_time < MANDRAKODI_CACHE_TTL):
            return self._cached_matches

        async with self._lock:
            # Check again inside lock
            if not force and self._cached_matches and (now - self._last_fetch_time < MANDRAKODI_CACHE_TTL):
                return self._cached_matches

            try:
                tasks = [
                    self._fetch_last_minute(),
                    self._fetch_sky_linear_channels(),
                    self._fetch_mpd_all(),
                ]
                if ENABLE_MANDRAKODI_SOCCERON:
                    tasks.append(self._fetch_socceron())

                results = await asyncio.gather(*tasks, return_exceptions=True)

                all_matches: List[Dict[str, Any]] = []
                for res in results:
                    if isinstance(res, list):
                        all_matches.extend(res)
                    elif isinstance(res, Exception):
                        logger.error("Error fetching Mandrakodi sub-source: %s", res)

                self._cached_matches = all_matches
                self._last_fetch_time = time.time()
                logger.info(
                    "Mandrakodi sync completed successfully. Total items indexed: %d",
                    len(all_matches),
                )
                return all_matches
            except Exception as e:
                logger.error("Mandrakodi sync failed: %s", e)
                return self._cached_matches

    async def _fetch_last_minute(self) -> List[Dict[str, Any]]:
        """Fetches and parses numTest=A1A103 (Last Minute events of the day)."""
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
        today = datetime.date.today()
        now_ts = int(time.time() * 1000)

        matches: List[Dict[str, Any]] = []

        for it in raw_items:
            t = it.get("title", "").strip()
            link = it.get("myresolve") or it.get("link") or it.get("externallink") or ""

            # 1. Separator detection
            if link == "ignore" or ("===" in t and not any(k in t.lower() for k in ("dazn", "amstaff", "acestream"))):
                clean_h = re.sub(r"\[.*?\]|===", "", t).strip().upper()
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

            # 2. Skip non-event items
            t_clean = re.sub(r"\[.*?\]", "", t).strip()
            if not t_clean or t_clean.lower() in ("last minute", "nessun evento", "ignore"):
                continue

            # 3. Time extraction
            time_match = re.search(r"\b(\d{1,2}:\d{2})\b", t_clean)
            if time_match:
                try:
                    hh, mm = map(int, time_match.group(1).split(":"))
                    event_dt = datetime.datetime(today.year, today.month, today.day, hh, mm, tzinfo=datetime.timezone.utc)
                    ts_ms = int(event_dt.timestamp() * 1000)
                except Exception:
                    ts_ms = now_ts
            else:
                ts_ms = now_ts

            # 4. Clean event title and extract home/away teams
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

            # 5. Extract stream payload
            source_type = None
            stream_meta: Dict[str, Any] = {}

            if "amstaff@@" in link and ENABLE_MANDRAKODI_AMSTAFF:
                source_type = "amstaff"
                raw_payload = link.split("amstaff@@", 1)[1]
                try:
                    dec = raw_payload if raw_payload.startswith("http") else base64.b64decode(raw_payload).decode("utf-8", errors="ignore")
                    parts = dec.split("|")
                    mpd_url = parts[0].strip()
                    key_part = parts[1].strip() if len(parts) > 1 else ""
                    kid, key = key_part.split(":", 1) if ":" in key_part else ("", "")
                    stream_meta = {
                        "mpd_url": mpd_url,
                        "kid": kid,
                        "key": key,
                    }
                except Exception as e:
                    logger.debug("Failed to decode amstaff payload: %s", e)
                    continue

            elif link.startswith("acestream://") and ENABLE_MANDRAKODI_ACESTREAM:
                source_type = "acestream"
                ace_id = link.replace("acestream://", "").strip()
                stream_meta = {"ace_id": ace_id}

            if not source_type:
                continue

            event_title = f"{home} vs {away}" if (home and away) else name
            canon_comp = detect_canonical_competition(f"{event_title} {current_comp}", current_silo) or current_comp
            display_comp = get_canonical_display_name(canon_comp)

            # Unique deterministic ID
            m_hash = (hash(name.lower() + str(ts_ms // 3600000)) & 0xFFFFFFFF)
            match_id = f"mandrakodi:lm:{m_hash:08x}"

            quality_label = "1080p 50fps" if source_type == "amstaff" else ("4K" if "4K" in t_clean else "1080p")
            lang_label = "ita" if any(k in t_clean.upper() for k in ("ITA", "SKY", "DAZN")) else "eng"

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
                "teams": {"home": {"name": home}, "away": {"name": away}} if (home and away) else None,
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

    async def _fetch_sky_linear_channels(self) -> List[Dict[str, Any]]:
        """
        Fetches the 21 Sky Sport linear channels (A1A165).
        Returns them as 24/7 channels ready for reconciliation or direct playback.
        """
        data = await self._fetch_endpoint("A1A165")
        if not data or not isinstance(data, dict):
            return []

        items = data.get("items", [])
        if not items:
            return []

        channels: List[Dict[str, Any]] = []
        now_ts = int(time.time() * 1000)

        for it in items:
            raw_title = it.get("title", "")
            clean_title = re.sub(r"\[.*?\]", "", raw_title).strip()
            myresolve = it.get("myresolve", "")
            thumb = it.get("thumbnail")

            if not myresolve.startswith("sky@@"):
                continue

            chid = myresolve.replace("sky@@", "").strip()

            # Map silo based on title
            silo = "football"
            cat = "calcio_italiano"
            t_upper = clean_title.upper()
            if "TENNIS" in t_upper:
                silo, cat = "tennis", "tennis"
            elif "BASKET" in t_upper:
                silo, cat = "basketball", "basket"
            elif "F1" in t_upper or "MOTOGP" in t_upper:
                silo, cat = "motor-sports", "motori"
            elif "GOLF" in t_upper or "ARENA" in t_upper:
                silo, cat = "other", "altri_sport"

            ch_dict: Dict[str, Any] = {
                "id": f"mandrakodi:sky:{chid}",
                "title": f"Sky {clean_title}",
                "category": silo,
                "_silo": silo,
                "_catalog": cat,
                "date": now_ts,
                "poster": thumb,
                "competition": "Sky Sport 24/7",
                "_competition": "Sky Sport 24/7",
                "country": "Italia",
                "_country": "Italia",
                "teams": None,
                "sources": [
                    {
                        "source": "amstaff",
                        "provider": "mandrakodi",
                        "id": chid,
                        "name": f"Sky {clean_title}",
                        "sky_channel_id": chid,
                        "country": "Italia",
                        "language": "ita",
                        "quality": "1080p 50fps",
                    }
                ],
                "_provider": "mandrakodi",
                "_mandrakodi_type": "sky_linear",
            }
            channels.append(ch_dict)

        return channels

    async def _fetch_mpd_all(self) -> List[Dict[str, Any]]:
        """
        Fetches the curated Italian sports channels from numTest=A1A134F (MPD ALL).
        Focuses on Eurosport 1-6 + 4K, Inter TV, Milan TV, Rai Sport, Radio TV Serie A, RSI LA1/LA2.
        """
        data = await self._fetch_endpoint("A1A134F")
        if not data or not isinstance(data, dict):
            return []

        items = data.get("items", [])
        if not items:
            return []

        channels: List[Dict[str, Any]] = []
        now_ts = int(time.time() * 1000)

        # White-list keywords to only import high-value Italian/Swiss sports channels
        TARGET_KEYWORDS = (
            "EUROSPORT",
            "INTER TV",
            "MILAN TV",
            "RAI SPORT",
            "RADIO TV SERIE A",
            "RSI LA1",
            "RSI LA2",
        )

        for it in items:
            raw_title = it.get("title", "")
            clean_title = re.sub(r"\[.*?\]", "", raw_title).strip()
            link = it.get("myresolve") or it.get("link") or ""

            t_upper = clean_title.upper()
            if not any(k in t_upper for k in TARGET_KEYWORDS):
                continue

            source_type = "amstaff" if "amstaff@@" in link else "direct"
            stream_meta: Dict[str, Any] = {}

            if "amstaff@@" in link:
                raw_payload = link.split("amstaff@@", 1)[1]
                try:
                    dec = raw_payload if raw_payload.startswith("http") else base64.b64decode(raw_payload).decode("utf-8", errors="ignore")
                    parts = dec.split("|")
                    stream_meta = {
                        "mpd_url": parts[0].strip(),
                        "kid": parts[1].split(":")[0].strip() if len(parts) > 1 and ":" in parts[1] else "",
                        "key": parts[1].split(":")[1].strip() if len(parts) > 1 and ":" in parts[1] else "",
                    }
                except Exception:
                    continue
            elif link.startswith("http"):
                stream_meta = {"stream_url": link.strip()}
            else:
                continue

            # Determine silo
            silo = "other"
            cat = "altri_sport"
            comp = "Live TV Sport"
            if "INTER" in t_upper or "MILAN" in t_upper or "SERIE A" in t_upper:
                silo, cat, comp = "football", "calcio_italiano", "Serie A"
            elif "EUROSPORT" in t_upper:
                silo, cat, comp = "other", "altri_sport", "Eurosport"
            elif "RSI" in t_upper:
                silo, cat, comp = "football", "calcio_estero", "RSI Sport Svizzera"

            cid_hash = (hash(clean_title) & 0xFFFFFFFF)
            ch_dict: Dict[str, Any] = {
                "id": f"mandrakodi:mpd:{cid_hash:08x}",
                "title": clean_title,
                "category": silo,
                "_silo": silo,
                "_catalog": cat,
                "date": now_ts,
                "competition": comp,
                "_competition": comp,
                "country": "Italia" if "RSI" not in t_upper else "Svizzera",
                "_country": "Italia" if "RSI" not in t_upper else "Svizzera",
                "teams": None,
                "sources": [
                    {
                        "source": source_type,
                        "provider": "mandrakodi",
                        "id": stream_meta.get("mpd_url") or stream_meta.get("stream_url"),
                        "name": clean_title,
                        "stream_data": stream_meta,
                        "country": "Italia",
                        "language": "ita",
                        "quality": "4K" if "4K" in t_upper else "1080p",
                    }
                ],
                "_provider": "mandrakodi",
                "_mandrakodi_type": "mpd_channel",
            }
            channels.append(ch_dict)

        return channels

    async def _fetch_socceron(self) -> List[Dict[str, Any]]:
        """Fetches Italian web events from numTest=A1A156 (Socceron), if active."""
        data = await self._fetch_endpoint("A1A156")
        if not data:
            return []

        raw_items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if not raw_items:
            return []

        matches: List[Dict[str, Any]] = []
        now_ts = int(time.time() * 1000)

        for it in raw_items:
            t = it.get("title", "").strip()
            link = it.get("myresolve") or it.get("link") or ""
            clean_title = re.sub(r"\[.*?\]", "", t).strip()
            if not clean_title or not link:
                continue

            home, away = None, None
            for sep in (" - ", " vs ", " v "):
                if sep in clean_title:
                    p = clean_title.split(sep, 1)
                    home = p[0].strip()
                    away = p[1].strip()
                    break

            mid = (hash("socceron:" + clean_title) & 0xFFFFFFFF)
            match_dict: Dict[str, Any] = {
                "id": f"mandrakodi:soc:{mid:08x}",
                "title": clean_title,
                "category": "football",
                "_silo": "football",
                "_catalog": "calcio_italiano",
                "date": now_ts,
                "competition": "Calcio Italiano (Socceron)",
                "_competition": "Calcio Italiano (Socceron)",
                "country": "Italia",
                "_country": "Italia",
                "teams": {"home": {"name": home}, "away": {"name": away}} if (home and away) else None,
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
            import json
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
                    exp_ts = dt.replace(tzinfo=datetime.timezone.utc).timestamp()
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
