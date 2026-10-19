import asyncio
import datetime
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.config import (
    ENABLE_FCTV33,
    FCTV33_API_BASE,
    FCTV33_CACHE_TTL,
)

logger = logging.getLogger("streamsport.fctv33")

# Mapping from FCTV33 sportType integer to canonical StreamSport Silos
FCTV33_SPORT_TYPE_MAP: Dict[int, Dict[str, str]] = {
    1: {"silo": "football", "name": "Calcio"},
    2: {"silo": "basketball", "name": "Basket"},
    3: {"silo": "tennis", "name": "Tennis"},
    4: {"silo": "baseball", "name": "Baseball"},
    6: {"silo": "cricket", "name": "Cricket"},
    7: {"silo": "motor-sports", "name": "Motori"},
    8: {"silo": "rugby", "name": "Rugby"},
    9: {"silo": "american-football", "name": "Football Americano"},
    10: {"silo": "aussie-rules", "name": "Aussie Rules"},
    11: {"silo": "hockey", "name": "Hockey"},
    12: {"silo": "badminton", "name": "Badminton"},
    13: {"silo": "volley", "name": "Volley"},
    14: {"silo": "fight", "name": "Combattimento"},
    15: {"silo": "cycling", "name": "Ciclismo"},
    16: {"silo": "handball", "name": "Pallamano"},
    90: {"silo": "other", "name": "Altri Sport"},
}

DEFAULT_FETCH_SPORTS = [1, 2, 3, 7, 9, 13, 14, 4, 11]

# Protobuf Varint and Envelope Decoders
def _read_varint(buffer: bytes, offset: int = 0) -> Tuple[int, int]:
    value, shift, index = 0, 0, offset
    buflen = len(buffer)
    while index < buflen:
        byte = buffer[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            break
        shift += 7
    return value, index


def _read_fields(buffer: bytes) -> Dict[int, List[Any]]:
    fields: Dict[int, List[Any]] = {}
    offset = 0
    buflen = len(buffer)
    while offset < buflen:
        tag, offset = _read_varint(buffer, offset)
        field, wire = tag >> 3, tag & 0x7
        if wire == 0:
            val, offset = _read_varint(buffer, offset)
            fields.setdefault(field, []).append(val)
        elif wire == 2:
            length, offset = _read_varint(buffer, offset)
            chunk = buffer[offset:offset + length]
            offset += length
            fields.setdefault(field, []).append(chunk)
        else:
            break
    return fields


class FCTV33Service:
    """
    Direct REST + Protobuf integration for FCTV33 (RBTV / TCPWE network).
    Fetches real-time multi-sport catalogs and stream IDs without a headless browser.
    """

    def __init__(self):
        self._cache: List[Dict[str, Any]] = []
        self._cache_time: float = 0.0
        self._cache_ttl: int = FCTV33_CACHE_TTL
        self._signatures: Dict[int, str] = {}
        self._sig_expiry: float = 0.0
        self._lock = asyncio.Lock()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://www.fctv33hd.host/",
            "Origin": "https://www.fctv33hd.host",
            "Accept": "*/*",
        }

    async def _get_signature(self, client: httpx.AsyncClient, code: int = 100, sport_type: int = 1) -> Optional[str]:
        """Obtains dynamic sfver signature for code 100 (/api/match/live) via /api/common/bs."""
        now = time.time()
        if self._signatures.get(code) and now < self._sig_expiry:
            return self._signatures[code]

        try:
            bs_url = f"{FCTV33_API_BASE}/api/common/bs?code=100&code=101&sportType={sport_type}&stream=true"
            resp = await client.get(bs_url, headers=self.headers, timeout=8.0)
            if resp.status_code != 200:
                logger.warning("FCTV33 handshake returned HTTP %s", resp.status_code)
                return None

            data = resp.content
            fields = _read_fields(data)
            for chunk in fields.get(10, []):
                for sub in _read_fields(chunk).get(1, []):
                    sf = _read_fields(sub)
                    c_id = sf.get(1, [None])[0]
                    c_sig = sf.get(2, [None])[0]
                    if c_id and c_sig:
                        sig_str = c_sig.decode("utf-8", errors="replace") if isinstance(c_sig, bytes) else str(c_sig)
                        self._signatures[c_id] = sig_str

            self._sig_expiry = now + 600  # 10 minutes cache
            return self._signatures.get(code)
        except Exception as e:
            logger.warning("Failed refreshing FCTV33 bs signature: %s", e)
            return None

    def _parse_match_item(self, match_bytes: bytes, sport_type: int) -> Optional[Dict[str, Any]]:
        """Parses a single match message chunk from Protobuf into StreamSport dictionary."""
        try:
            mf = _read_fields(match_bytes)
            mid = mf.get(1, [None])[0]
            if not mid:
                return None

            mid_str = str(mid)
            ts = mf.get(3, [None])[0]  # millisecond epoch timestamp
            if not ts:
                return None

            # Title extraction from Field 30 (subfield 2) or Field 150 (slug)
            title = ""
            if 30 in mf:
                t_sub = _read_fields(mf[30][0])
                t_val = t_sub.get(2, [b""])[0]
                if isinstance(t_val, bytes):
                    title = t_val.decode("utf-8", errors="replace").strip()
                elif isinstance(t_val, str):
                    title = t_val.strip()

            slug = ""
            if 150 in mf:
                s_sub = _read_fields(mf[150][0])
                s_val = s_sub.get(20, [b""])[0]
                if isinstance(s_val, bytes):
                    slug = s_val.decode("utf-8", errors="replace").strip()

            if not title and slug:
                # convert slug 'juventus-vs-lazio' to 'Juventus vs Lazio'
                title = slug.replace("-", " ").title()

            if not title:
                return None

            # League & Country extraction from Field 10
            league = ""
            country = ""
            logo_url = None
            if 10 in mf:
                l_sub = _read_fields(mf[10][0])
                l_name = l_sub.get(3, [b""])[0]
                if isinstance(l_name, bytes):
                    s = l_name.decode("utf-8", errors="replace")
                    raw_l = s.split("\x12")[-1].strip() if "\x12" in s else s.strip()
                    league = re.sub(r"^[^a-zA-Z0-9]+", "", raw_l).strip()

                l_logo = l_sub.get(4, [b""])[0]
                if isinstance(l_logo, bytes) and l_logo.startswith(b"http"):
                    logo_url = l_logo.decode("utf-8", errors="replace")

                if 80 in l_sub:
                    c_raw = l_sub[80][0]
                    if isinstance(c_raw, bytes):
                        cs = c_raw.decode("utf-8", errors="replace")
                        country = cs.split("\x12")[-1].split('"')[0].strip() if "\x12" in cs else ""

            # Canonical silo resolution based on sportType
            silo_info = FCTV33_SPORT_TYPE_MAP.get(sport_type, {"silo": "other", "name": "Altri Sport"})
            canonical_silo = silo_info["silo"]

            return {
                "id": f"fctv33:{mid_str}",
                "title": title,
                "category": canonical_silo,
                "date": int(ts),
                "poster": logo_url,
                "sources": [
                    {
                        "source": "fctv33",
                        "id": mid_str,
                        "match_id": mid_str,
                        "sport_type": sport_type,
                        "name": "FCTV33 Live",
                    }
                ],
                "_silo": canonical_silo,
                "_competition": league,
                "_country": country,
                "_fctv33_id": mid_str,
                "_sport_type": sport_type,
                "_provider": "fctv33",
            }
        except Exception as e:
            logger.debug("Error parsing FCTV33 match item: %s", e)
            return None

    async def get_matches(self, force: bool = False, sport_types: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        """
        Fetches multi-sport events currently live or upcoming today from FCTV33 backend.
        Uses in-RAM caching to prevent redundant upstream requests.
        """
        if not ENABLE_FCTV33:
            return []

        now = time.time()
        if not force and self._cache and (now - self._cache_time < self._cache_ttl):
            return self._cache

        async with self._lock:
            if not force and self._cache and (now - self._cache_time < self._cache_ttl):
                return self._cache

            target_sports = sport_types or DEFAULT_FETCH_SPORTS
            collected: List[Dict[str, Any]] = []

            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                # 1. Obtain signature
                sig = await self._get_signature(client, code=100, sport_type=1)
                if not sig:
                    logger.warning("FCTV33: unable to obtain live signature, skipping")
                    return self._cache

                # 2. Concurrently fetch target sports
                async def fetch_sport(st: int):
                    try:
                        url = f"{FCTV33_API_BASE}/sfver{sig}/api/match/live?sportType={st}&stream=true"
                        resp = await client.get(url, headers=self.headers, timeout=8.0)
                        if resp.status_code != 200:
                            return []

                        env = _read_fields(resp.content)
                        if not env.get(10):
                            return []

                        payload = _read_fields(env[10][0])
                        match_chunks = payload.get(1, [])
                        sport_matches = []
                        for mc in match_chunks:
                            item = self._parse_match_item(mc, st)
                            if item:
                                sport_matches.append(item)
                        return sport_matches
                    except Exception as err:
                        logger.debug("FCTV33: error fetching sportType %s: %s", st, err)
                        return []

                tasks = [fetch_sport(st) for st in target_sports]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for r in results:
                    if isinstance(r, list):
                        collected.extend(r)

            if collected:
                # Deduplicate by ID
                seen_ids = set()
                unique_matches = []
                for m in collected:
                    mid = m["id"]
                    if mid not in seen_ids:
                        seen_ids.add(mid)
                        unique_matches.append(m)

                self._cache = unique_matches
                self._cache_time = now
                logger.info("✅ FCTV33 sync complete: %d matches across %d sports", len(self._cache), len(target_sports))

            return self._cache

    async def get_streams_for_match(self, match_id: str, sport_type: int = 1) -> List[Dict[str, Any]]:
        """
        Queries /api/match/detail in real-time to retrieve the latest broadcast channels and stream IDs.
        """
        if not ENABLE_FCTV33 or not match_id:
            return []

        mid_clean = match_id.replace("fctv33:", "").strip()
        try:
            url = f"{FCTV33_API_BASE}/api/match/detail?matchId={mid_clean}&sportType={sport_type}&digit=foth&country=IT&continent=EU"
            async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=self.headers)
                if resp.status_code != 200:
                    return []

                env = _read_fields(resp.content)
                if not env.get(10):
                    return []

                payload = _read_fields(env[10][0])
                stream_chunks = payload.get(2, [])
                sources = []
                for sc in stream_chunks:
                    sf = _read_fields(sc)
                    sid = sf.get(1, [None])[0]
                    if not sid:
                        continue

                    sname_raw = sf.get(3, [b""])[0]
                    sname = sname_raw.decode("utf-8", errors="replace").strip() if isinstance(sname_raw, bytes) else str(sname_raw).strip()
                    site_type = sf.get(9, [2001])[0]

                    sources.append({
                        "source": "fctv33",
                        "name": sname or "FCTV33 HD",
                        "match_id": mid_clean,
                        "stream_id": str(sid),
                        "sport_type": sport_type,
                        "site_type": site_type,
                    })

                return sources
        except Exception as e:
            logger.debug("FCTV33: error resolving streams for match %s: %s", match_id, e)
            return []


fctv33_service = FCTV33Service()
