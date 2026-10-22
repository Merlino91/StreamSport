import asyncio
import datetime
import logging
import re
import time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
import httpx

from app.config import (
    ENABLE_SPON,
    SPON_PRIMARY_URL,
    SPON_FALLBACK_URL,
    SPON_CACHE_TTL,
    SPON_ITALIAN_ONLY,
)

logger = logging.getLogger("streamsport.spon")

WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


class SPONService:
    """
    Direct ingestion service for Sportzonline (SPON).
    Parses prog.txt daily schedule, extracts dynamic language mappings per day,
    and isolates events with Italian commentary (HD7/HD8) for deduplication into
    the StreamSport canonical catalog.
    """

    def __init__(self):
        self._cache: List[Dict[str, Any]] = []
        self._cache_time: float = 0.0
        self._cache_ttl: int = SPON_CACHE_TTL
        self._lock = asyncio.Lock()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/plain, */*",
        }

    async def get_matches(self, force: bool = False) -> List[Dict[str, Any]]:
        """Returns parsed matches from prog.txt, utilizing cache when valid."""
        if not ENABLE_SPON:
            return []

        now = time.time()
        if not force and self._cache and (now - self._cache_time < self._cache_ttl):
            return self._cache

        async with self._lock:
            if not force and self._cache and (now - self._cache_time < self._cache_ttl):
                return self._cache

            raw_text = await self._fetch_prog_text()
            if not raw_text:
                logger.warning("Could not fetch Sportzonline prog.txt from any mirror.")
                return self._cache

            try:
                matches = self.parse_prog(raw_text)
                self._cache = matches
                self._cache_time = time.time()
                logger.info("Sportzonline: Ingested %d matches from prog.txt (Italian only: %s)", len(matches), SPON_ITALIAN_ONLY)
                return self._cache
            except Exception as e:
                logger.error("Failed to parse Sportzonline prog.txt: %s", e, exc_info=True)
                return self._cache

    async def _fetch_prog_text(self) -> Optional[str]:
        """Tries to download prog.txt from the primary URL, then fallback mirror."""
        urls = [SPON_PRIMARY_URL, SPON_FALLBACK_URL]
        for url in urls:
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=self.headers) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        text = resp.text
                        if "LAST UPDATE" in text or any(day in text for day in WEEKDAYS):
                            return text
            except Exception as e:
                logger.debug("Failed to fetch Sportzonline prog from %s: %s", url, e)
        return None

    def parse_prog(self, raw_text: str) -> List[Dict[str, Any]]:
        """Parses prog.txt content into canonical StreamSport match dictionaries."""
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

        # 1. Determine base reference date (from 'LAST UPDATE: DD-MM-YY' or today)
        london_tz = ZoneInfo("Europe/London")
        now_london = datetime.datetime.now(london_tz)
        base_date = now_london.date()

        for line in lines[:25]:
            m_update = re.search(r"LAST UPDATE:\s*(\d{1,2})-(\d{1,2})-(\d{2,4})", line, re.IGNORECASE)
            if m_update:
                day_v = int(m_update.group(1))
                month_v = int(m_update.group(2))
                raw_year = int(m_update.group(3))
                year_v = 2000 + raw_year if raw_year < 100 else raw_year
                try:
                    base_date = datetime.date(year_v, month_v, day_v)
                except ValueError:
                    pass
                break

        current_day: Optional[str] = None
        day_date_map: Dict[str, datetime.date] = {}
        day_lang_maps: Dict[str, Dict[str, str]] = {}
        parsed_events: List[Dict[str, Any]] = []

        day_order = 0
        for line in lines:
            # Check for Day section header
            upper_line = line.upper()
            if upper_line in WEEKDAYS:
                current_day = upper_line
                if current_day not in day_lang_maps:
                    day_lang_maps[current_day] = {}
                    # Calculate date for this day
                    # If first day section, anchor to base_date; successive days get +1 day
                    day_date_map[current_day] = base_date + datetime.timedelta(days=day_order)
                    day_order += 1
                continue

            if not current_day:
                continue

            # Check for channel language mapping: e.g. 'HD7 ITALIAN', 'HD8 ITALIAN & SPANISH'
            m_lang = re.match(r"^(HD\d+|BR\d+)\s+(.+)$", line, re.IGNORECASE)
            if m_lang:
                ch_key = m_lang.group(1).upper()
                lang_val = m_lang.group(2).strip()
                day_lang_maps[current_day][ch_key] = lang_val
                continue

            # Check for event row: 'HH:MM   <Title> | <URL>'
            m_event = re.match(r"^(\d{1,2}:\d{2})\s+(.*?)\s*\|\s*(https?://\S+)", line)
            if m_event:
                time_str = m_event.group(1)
                raw_title = m_event.group(2).strip()
                channel_url = m_event.group(3).strip()

                # Extract channel identifier from URL (e.g. 'hd7.php' -> 'HD7', 'eleven1.php' -> 'ELEVEN1')
                m_ch = re.search(r"channels/(?:hd/|pt/|bra/)?([a-zA-Z0-9_-]+)\.php", channel_url, re.IGNORECASE)
                ch_id = m_ch.group(1).upper() if m_ch else "HD"

                lang_for_ch = day_lang_maps.get(current_day, {}).get(ch_id, "")

                # If SPON_ITALIAN_ONLY is active, skip streams that do not include Italian commentary
                if SPON_ITALIAN_ONLY:
                    is_italian = "ITALIAN" in lang_for_ch.upper() or ch_id == "HD7"
                    if not is_italian:
                        continue

                event_date = day_date_map.get(current_day, base_date)
                hour_part, min_part = [int(p) for p in time_str.split(":")]
                dt_london = datetime.datetime(
                    event_date.year, event_date.month, event_date.day,
                    hour_part, min_part, tzinfo=london_tz
                )
                dt_utc = dt_london.astimezone(datetime.timezone.utc)
                start_time_ms = int(dt_utc.timestamp() * 1000)

                # Parse sport and silo from title
                silo, clean_title, teams = self._parse_silo_and_teams(raw_title)

                lang_label = "Italiano" if "ITALIAN" in lang_for_ch.upper() or ch_id == "HD7" else lang_for_ch.title()
                flag = "🇮🇹" if "ITALIAN" in lang_for_ch.upper() or ch_id == "HD7" else "🌍"

                source_item = {
                    "source": "sportsonline",
                    "id": channel_url,
                    "channel": ch_id,
                    "lang": lang_label,
                    "flag": flag,
                    "url": channel_url,
                }

                match_item = {
                    "id": f"spon:{ch_id.lower()}_{int(dt_utc.timestamp())}",
                    "title": clean_title,
                    "category": silo,
                    "date": start_time_ms,
                    "time": dt_utc.strftime("%H:%M"),
                    "teams": teams,
                    "_provider": "sportsonline",
                    "_silo": silo,
                    "sources": [source_item],
                }
                parsed_events.append(match_item)

        return parsed_events

    def _parse_silo_and_teams(self, raw_title: str) -> tuple[str, str, Dict[str, str]]:
        """Extracts sport silo, clean title, and structured teams from an event string."""
        title = raw_title.strip()

        # Check for explicit sport prefix
        if ":" in title:
            prefix, rest = title.split(":", 1)
            p_low = prefix.lower().strip()
            rest_clean = rest.strip()
            if "tennis" in p_low:
                return "tennis", rest_clean, {}
            if any(k in p_low for k in ("motogp", "moto2", "moto3", "f1", "gp", "motor")):
                return "motor-sports", f"{prefix}: {rest_clean}", {}
            if "nba" in p_low or "basket" in p_low:
                silo = "basketball"
            elif "nfl" in p_low:
                silo = "american-football"
            elif "golf" in p_low:
                return "other", f"{prefix}: {rest_clean}", {}
            else:
                silo = "other"

            # Parse competitors in rest (e.g. 'Team A @ Team B' or 'Team A vs Team B')
            if " @ " in rest_clean:
                away, home = rest_clean.split(" @ ", 1)
                return silo, f"{home.strip()} vs {away.strip()}", {"home": home.strip(), "away": away.strip()}
            elif " vs " in rest_clean.lower():
                parts = re.split(r"\s+vs\s+", rest_clean, flags=re.IGNORECASE)
                return silo, f"{parts[0].strip()} vs {parts[1].strip()}", {"home": parts[0].strip(), "away": parts[1].strip()}
            elif " x " in rest_clean.lower():
                parts = re.split(r"\s+x\s+", rest_clean, flags=re.IGNORECASE)
                return silo, f"{parts[0].strip()} vs {parts[1].strip()}", {"home": parts[0].strip(), "away": parts[1].strip()}
            return silo, f"{prefix}: {rest_clean}", {}

        # Standard Calcio / 1v1 match ('Team A x Team B' or 'Team A vs Team B')
        if " x " in title:
            parts = title.split(" x ", 1)
            home, away = parts[0].strip(), parts[1].strip()
            return "football", f"{home} vs {away}", {"home": home, "away": away}
        elif " vs " in title.lower():
            parts = re.split(r"\s+vs\s+", title, flags=re.IGNORECASE)
            home, away = parts[0].strip(), parts[1].strip()
            return "football", f"{home} vs {away}", {"home": home, "away": away}

        return "football", title, {}


spon_service = SPONService()
