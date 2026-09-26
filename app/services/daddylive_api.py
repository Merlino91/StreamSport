import asyncio
import datetime
import html
import json
import logging
import re
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("streamsport.daddylive")

REMOTE_SCHEDULE_URL = "https://raw.githubusercontent.com/qwertyuiop8899/logo/main/daddyliveSchedule.json"
LOGO_BASE = "https://raw.githubusercontent.com/qwertyuiop8899/logo/main"

_MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Sept": 9, "Oct": 10, "Nov": 11, "Dec": 12
}

_ORDINAL_RX = re.compile(r"(\d{1,2})\s*(?:st|nd|rd|th)\b", re.IGNORECASE)

class DaddyLiveAPI:
    """
    Scrapes events from DaddyLive schedule (same upstream source as StreamViX).
    Converts events to the standard StreamSport format and extracts exact stream channels.
    """

    def __init__(self):
        self._cache: List[Dict[str, Any]] = []
        self._cache_time: float = 0
        self._cache_ttl = 3600  # 1 hour
        self._active_domain: str = "dlive.sx"
        self._last_domain_check: float = 0.0

    async def get_active_domain(self) -> str:
        """
        Dynamically detects the latest online DaddyLive/DLHD mirror domain
        using the daddylive.pk status checker and redirect follower.
        Caches for 6 hours; defaults to 'dlive.sx'.
        """
        now = time.time()
        if now - self._last_domain_check < 21600 and self._active_domain:
            return self._active_domain

        loop = asyncio.get_running_loop()
        try:
            def _check():
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'}
                status_url = "https://daddylive.pk/check_status.php?url=https://dlhd.pk"
                req = urllib.request.Request(status_url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as res:
                    data = json.loads(res.read().decode('utf-8'))
                    if data.get('status') == 'online':
                        final_url = data.get('final_url') or "https://dlive.sx"
                        from urllib.parse import urlparse
                        netloc = urlparse(final_url).netloc.lower()
                        if netloc:
                            return netloc
                return "dlive.sx"

            domain = await loop.run_in_executor(None, _check)
            if domain:
                self._active_domain = domain
                self._last_domain_check = now
                logger.info("DaddyLive active domain confirmed: %s", self._active_domain)
        except Exception as e:
            logger.debug("DaddyLive domain check fallback: %s", e)
            self._last_domain_check = now

        return self._active_domain

    def _clean_day(self, day: str) -> str:
        s = re.sub(r"\s*-\s*Schedule Time UK GMT\s*$", "", day, flags=re.IGNORECASE)
        s = s.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2009", " ").replace("\u200b", "")
        s = _ORDINAL_RX.sub(r"\1", s)
        return re.sub(r"\s+", " ", s).strip()

    def _parse_datetime(self, day_str: str, time_str: str) -> int:
        """Parses day string and time string into epoch ms."""
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

        try:
            import pytz
            rome_tz = pytz.timezone("Europe/Rome")
            naive_dt = datetime.datetime(year, month, daynum, h, m)
            dt = rome_tz.localize(naive_dt)
            return int(dt.timestamp() * 1000)
        except Exception:
            try:
                from zoneinfo import ZoneInfo
                rome_tz = ZoneInfo("Europe/Rome")
                dt = datetime.datetime(year, month, daynum, h, m, tzinfo=rome_tz)
                return int(dt.timestamp() * 1000)
            except Exception:
                # Manual Europe/Rome offset fallback (CEST UTC+2 in summer, CET UTC+1 in winter)
                offset_hours = 2 if 4 <= month <= 10 else 1
                tz_offset = datetime.timezone(datetime.timedelta(hours=offset_hours))
                dt = datetime.datetime(year, month, daynum, h, m, tzinfo=tz_offset)
                return int(dt.timestamp() * 1000)

    def _parse_upcoming_date(self, event_title: str) -> int:
        """Attempts to parse full date from upcoming title like '... | 26 September 2026'."""
        m = re.search(r"\|\s*(\d{1,2})\s*([A-Za-z]+)\s*(\d{4})", event_title)
        if m:
            daynum = int(m.group(1))
            month_str = m.group(2)
            year = int(m.group(3))
            month = _MONTHS.get(month_str, 9)
            try:
                import pytz
                rome_tz = pytz.timezone("Europe/Rome")
                dt = rome_tz.localize(datetime.datetime(year, month, daynum, 15, 0))
                return int(dt.timestamp() * 1000)
            except Exception:
                offset_hours = 2 if 4 <= month <= 10 else 1
                tz_offset = datetime.timezone(datetime.timedelta(hours=offset_hours))
                dt = datetime.datetime(year, month, daynum, 15, 0, tzinfo=tz_offset)
                return int(dt.timestamp() * 1000)
        return 0

    def _clean_title(self, raw_title: str) -> str:
        # Unescape HTML entities (&amp; -> &)
        t = html.unescape(raw_title or "")

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

    DADDYLIVE_EXCLUDED_CATEGORIES = (
        "big brother", "tv show", "reality", "movies", "horse racing", "upcoming events",
        "college soccer", "women's college soccer",
        "women's college volleyball", "women's college ice hockey",
        "minor league baseball"
    )

    @classmethod
    def map_daddylive_category_to_silo(cls, cat_raw: str, clean_title: str) -> Optional[Tuple[str, str]]:
        """
        Maps a DaddyLive raw category string and title to (silo_id, sport_category).
        Returns None if the category is excluded.
        """
        cat_lower = (cat_raw or "").lower()
        if any(k in cat_lower for k in cls.DADDYLIVE_EXCLUDED_CATEGORIES):
            return None

        title_lower = (clean_title or "").lower()

        # 1. Football (Soccer)
        if any(k in cat_lower for k in ("soccer", "league one", "league two", "national league", "mls", "usl")):
            return "football", "football"
        if "football" in cat_lower and "american" not in cat_lower and "cfl" not in cat_lower and "college" not in cat_lower:
            return "football", "football"

        # 2. Tennis
        if "tennis" in cat_lower:
            return "tennis", "tennis"

        # 3. Motorsport
        if "motor" in cat_lower or "formula" in cat_lower:
            return "motor-sports", "motor-sports"

        # 4. Basketball
        if "basket" in cat_lower or "nba" in cat_lower:
            return "basketball", "basketball"

        # 5. Combat / Fight
        if any(k in cat_lower for k in ("boxing", "mma", "ufc", "wrestling")):
            return "fight", "fight"

        # 6. Baseball
        if "baseball" in cat_lower or "mlb" in cat_lower:
            return "baseball", "baseball"

        # 7. American Football (NFL, CFL, UFL & College Football)
        if any(k in cat_lower for k in ("am. football", "american football", "cfl", "nfl", "ufl", "college football")):
            return "american-football", "american-football"

        # 8. Hockey
        if "hockey" in cat_lower or any(k in cat_lower for k in ("nhl", "shl", "ohl", "ushl")):
            return "hockey", "hockey"

        # 9. Volleyball
        if "volleyball" in cat_lower:
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
        if "cycling" in cat_lower or "ciclismo" in title_lower:
            return "altri_sport", "cycling"
        if "handball" in cat_lower or "pallamano" in title_lower:
            return "altri_sport", "handball"
        if "futsal" in cat_lower:
            return "altri_sport", "futsal"
        if "afl" in cat_lower or "aussie" in cat_lower:
            return "altri_sport", "afl"
        if "billiards" in cat_lower or "snooker" in title_lower:
            return "altri_sport", "billiards"

        return "altri_sport", "other"

    async def get_matches(self, force: bool = False) -> List[Dict[str, Any]]:
        now = time.time()
        if not force and self._cache and (now - self._cache_time < self._cache_ttl):
            return self._cache

        loop = asyncio.get_running_loop()
        try:
            def _fetch():
                req = urllib.request.Request(
                    REMOTE_SCHEDULE_URL,
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=15) as res:
                    return json.loads(res.read().decode("utf-8"))

            schedule_data = await loop.run_in_executor(None, _fetch)
        except Exception as e:
            logger.warning("Failed to fetch DaddyLive schedule: %s", e)
            return self._cache

        matches: List[Dict[str, Any]] = []

        for day_str, categories in schedule_data.items():
            if not isinstance(categories, dict):
                continue

            for cat_raw, event_list in categories.items():
                if not isinstance(event_list, list):
                    continue

                cat_lower = cat_raw.lower()
                if any(k in cat_lower for k in ("big brother", "tv show", "reality", "movies")):
                    continue

                is_upcoming = "upcoming" in cat_lower

                for ev in event_list:
                    raw_title = ev.get("event") or ""
                    if not raw_title:
                        continue

                    # Clean event title
                    clean_title = self._clean_title(raw_title)

                    # Extract channels
                    channels = ev.get("channels") or []
                    sources = []
                    for ch in channels:
                        if isinstance(ch, dict):
                            ch_id = str(ch.get("channel_id") or "").strip()
                            ch_name = (ch.get("channel_name") or f"CH-{ch_id}").strip()
                            # Filter out placeholder channels with no stream
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

                    time_str = ev.get("time") or "00:00"

                    if is_upcoming:
                        date_ms = self._parse_upcoming_date(raw_title)
                    else:
                        date_ms = self._parse_datetime(day_str, time_str)

                    # Extract teams if 'vs' in title
                    teams = None
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

                    slug = re.sub(r"[^a-z0-9]+", "-", clean_title.lower()).strip("-")[:60]
                    match_id = f"dlhd-{slug}-{str(date_ms)[-6:]}"

                    matches.append({
                        "id": match_id,
                        "title": clean_title,
                        "category": category,
                        "_silo": silo_id,
                        "date": date_ms,
                        "poster": None,
                        "popular": False,
                        "teams": teams,
                        "sources": sources
                    })

        self._cache = matches
        self._cache_time = now
        logger.info("Parsed %d matches from DaddyLive schedule", len(matches))
        return matches

daddylive_api = DaddyLiveAPI()
