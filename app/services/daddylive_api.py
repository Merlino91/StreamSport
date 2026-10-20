import asyncio
import datetime
import html
import json
import logging
import re
import time
import httpx
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import hashlib
from app.services.canonical_competition_service import (
    detect_canonical_competition,
    get_canonical_display_name,
    get_canonical_silo,
    is_redzone_event,
    is_simulcast_event,
    get_simulcast_info,
)

logger = logging.getLogger("streamsport.daddylive")

REMOTE_SCHEDULE_URLS = [
    "https://raw.githubusercontent.com/qwertyuiop8899/logo/main/daddyliveSchedule.json",
    "https://cdn.jsdelivr.net/gh/qwertyuiop8899/logo@main/daddyliveSchedule.json",
]
REMOTE_SCHEDULE_URL = REMOTE_SCHEDULE_URLS[0]
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
        self._cache_ttl: int = 3600  # 1 hour default
        self._last_content_hash: str = ""
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

        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'}
            status_url = "https://daddylive.pk/check_status.php?url=https://dlhd.pk"
            async with httpx.AsyncClient(timeout=5.0, headers=headers, follow_redirects=True) as client:
                res = await client.get(status_url)
                if res.status_code == 200:
                    data = res.json()
                    if data.get('status') == 'online':
                        final_url = data.get('final_url') or "https://dlive.sx"
                        from urllib.parse import urlparse
                        netloc = urlparse(final_url).netloc.lower()
                        if netloc:
                            self._active_domain = netloc
                            self._last_domain_check = now
                            logger.info("DaddyLive active domain confirmed: %s", self._active_domain)
                            return self._active_domain
            self._active_domain = "dlive.sx"
            self._last_domain_check = now
            return "dlive.sx"
        except Exception as e:
            logger.debug("DaddyLive domain check fallback: %s", e)
            self._last_domain_check = now
            self._active_domain = "dlive.sx"

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

        # Broadcast day runs from 06:00 to 05:59 next day.
        # Events scheduled at 00:00 - 05:59 belong to the following calendar morning!
        day_offset = 1 if h < 6 else 0
        base_date = datetime.date(year, month, daynum)
        if day_offset:
            base_date += datetime.timedelta(days=day_offset)

        # DaddyLive schedule times are explicitly published in UK GMT / UTC ("Schedule Time UK GMT")
        dt = datetime.datetime(base_date.year, base_date.month, base_date.day, h, m, tzinfo=datetime.timezone.utc)
        return int(dt.timestamp() * 1000)

    def _parse_upcoming_date(self, event_title: str) -> int:
        """Attempts to parse full date from upcoming title like '... | 26 September 2026'."""
        m = re.search(r"\|\s*(\d{1,2})\s*([A-Za-z]+)\s*(\d{4})", event_title)
        if m:
            daynum = int(m.group(1))
            month_str = m.group(2)
            year = int(m.group(3))
            month = _MONTHS.get(month_str, 9)
            dt = datetime.datetime(year, month, daynum, 15, 0, tzinfo=datetime.timezone.utc)
            return int(dt.timestamp() * 1000)
        return 0

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

    DADDYLIVE_EXCLUDED_CATEGORIES = (
        "big brother", "tv show", "reality", "movies", "horse racing", "upcoming events",
        "college soccer", "men's college soccer", "women's college soccer",
        "college volleyball", "women's college volleyball",
        "college ice hockey", "women's college ice hockey",
        "minor league baseball",
        "usl championship", "usl league one", "usl",
        "ahl ice hockey", "ahl",
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
        if any(k in cat_lower for k in (
            "soccer", "championship", "league one", "league two", "national league", "mls", "usl",
            "youth league", "champions league", "europa league", "conference league",
            "serie a", "serie b", "serie c", "la liga", "bundesliga", "ligue 1", "premier league",
            "eredivisie", "coppa italia", "fa cup", "copa del rey", "copa libertadores", "copa sudamericana"
        )):
            return "football", "football"
        if "football" in cat_lower and "american" not in cat_lower and "am." not in cat_lower and "cfl" not in cat_lower and "college" not in cat_lower:
            return "football", "football"

        # 2. Tennis
        if "tennis" in cat_lower:
            return "tennis", "tennis"

        # 3. Motorsport
        if "motor" in cat_lower or "formula" in cat_lower:
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

        # 7. American Football (NFL, NCAA FBS, CFL, UFL)
        if any(k in cat_lower for k in ("am. football", "american football", "cfl", "nfl", "ufl", "college football", "ncaaf", "ncaa")):
            return "american-football", "american-football"

        # 8. Hockey (NHL, European Leagues, Roller Hockey, Field Hockey)
        if "hockey" in cat_lower or "hockey" in title_lower or any(k in cat_lower for k in ("nhl", "shl", "ohl", "ushl")):
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
        if ("cycling" in cat_lower or "ciclismo" in title_lower) and "hockey" not in title_lower:
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

        schedule_data = None
        new_content_hash = ""
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
                for url in REMOTE_SCHEDULE_URLS:
                    for attempt in range(2):
                        try:
                            res = await client.get(url)
                            if res.status_code == 200:
                                new_content_hash = hashlib.md5(res.content).hexdigest()
                                # Fast return if content hasn't changed since last full parse
                                if not force and new_content_hash == self._last_content_hash and self._cache:
                                    self._cache_time = now
                                    logger.debug("DaddyLive schedule unchanged (hash %s)", new_content_hash[:8])
                                    return self._cache
                                schedule_data = res.json()
                                break
                        except Exception as e:
                            if attempt == 0:
                                await asyncio.sleep(1.0)
                            else:
                                logger.debug("DaddyLive schedule attempt failed for %s: %s", url, e)
                    if schedule_data:
                        break
        except Exception as e:
            logger.warning("Failed to fetch DaddyLive schedule: %s", e)

        if not schedule_data:
            logger.warning("Failed to fetch DaddyLive schedule from all mirror endpoints")
            return self._cache

        matches: List[Dict[str, Any]] = []

        for day_str, categories in schedule_data.items():
            if not isinstance(categories, dict):
                continue

            for cat_raw, event_list in categories.items():
                if not isinstance(event_list, list):
                    continue

                cat_lower = cat_raw.lower()
                if any(k in cat_lower for k in self.DADDYLIVE_EXCLUDED_CATEGORIES):
                    continue

                is_upcoming = "upcoming" in cat_lower

                for ev in event_list:
                    raw_title = ev.get("event") or ""
                    if not raw_title:
                        continue

                    # Exclude non-football college sports (soccer, volleyball, hockey, baseball)
                    if re.search(r"\b(college\s*(?:soccer|volleyball|ice\s*hockey|baseball))\b", raw_title.lower()):
                        continue

                    # Exclude lower-division college football (FCS, Division II, Division III, NAIA, JuCo)
                    # Keeping strictly Division I FBS (Football Bowl Subdivision)
                    if re.search(r"\b(fcs|ncaa\s*fcs|division\s*(?:ii|iii|1-aa|i-aa)|d-?[23]|naia|juco)\b", raw_title.lower()):
                        continue

                    # Soccer Youth & Women: accept Italian teams/competitions, filter foreign youth & women
                    is_soccer = "soccer" in cat_lower or "football" in cat_lower
                    if is_soccer:
                        title_low = raw_title.lower()
                        # 1. Native Italian markers guaranteed to pass without restriction
                        is_italian_native = bool(re.search(r"\b(primavera|allievi|giovanissimi|femminile)\b", title_low))
                        if not is_italian_native:
                            # 2. Foreign sentinels for youth and women tournaments
                            is_foreign_youth = bool(re.search(r"\b(u-?1[5-9]|under\s*1[5-9]|u-?2[0-3]|under\s*2[0-3]|youth(?:\s*league)?|junior)\b", title_low))
                            is_foreign_women = bool(re.search(r"\b(women(?:[’']s)?|ladies|frauen|f[ée]minine|femenino|uwcl)\b", title_low)) or bool(re.search(r"\b[A-Za-z0-9]+\s+[WD]\b", raw_title))
                            if is_foreign_youth or is_foreign_women:
                                is_italian = (
                                    "🇮🇹" in raw_title
                                    or bool(re.search(r"\b(italy|italia|milan|inter|juventus|roma|atalanta|bologna|lazio|fiorentina|torino|empoli|lecce|napoli|verona|sassuolo|genoa|monza|cagliari|sampdoria|parma|udinese|venezia|palermo|bari|brescia|cremonese|cesena|salernitana|spezia|frosinone|catanzaro|cosenza|reggiana|modena|carrarese|mantova|sudtirol|cittadella|pisa|juve\s*stabia)\b", title_low))
                                )
                                if not is_italian:
                                    continue

                    # Basketball: filter Asian club leagues, foreign minor women, youth & lower divisions
                    is_basketball = any(k in cat_lower for k in ("basket", "nba", "wnba", "eurocup", "euroleague", "aba")) or \
                                    any(k in raw_title.lower() for k in ("(nba)", "(eurocup)", "(euroleague)", "basketball", "cba", "kbl"))
                    if is_basketball:
                        title_low = raw_title.lower()
                        full_basket_text = f"{cat_lower} {title_low}"

                        # 1. Youth
                        if re.search(r"\b(u-?1[0-9]|u-?2[01]|under\s*1[0-9]|under\s*2[01]|sub-?1[0-9]|sub-?20|junior|high\s*school)\b", full_basket_text):
                            if not ("🇮🇹" in raw_title or re.search(r"\b(italia|italy|lba|serie\s*a)\b", full_basket_text)):
                                continue

                        # 2. Lower college divisions & amateur circuits (Keep Division I Men's and March Madness)
                        if re.search(r"\b(division\s*(?:ii|iii|1-aa|i-aa)|d-?[23]|naia|juco|mpbl|zone\s*5|amateur|development\s*league)\b", full_basket_text):
                            continue

                        # 3. Foreign Women's Basketball (Keep WNBA, Italian women, FIBA World/Euro)
                        is_women = bool(re.search(r"\b(women(?:['’]s)?|femminile|femenil|damen|ladies|wnbl|wbla)\b", full_basket_text)) or bool(re.search(r"\b[A-Za-z0-9]+\s+[WD]\b", raw_title))
                        if is_women:
                            is_wnba = bool(re.search(r"\bwnba\b", full_basket_text))
                            is_it_women = "🇮🇹" in raw_title or bool(re.search(r"\b(serie\s*a[12]?|coppa\s*italia|lba|italia|italy)\b", full_basket_text))
                            is_top_fiba_women = bool(re.search(r"\b(fiba\s*world\s*cup|eurobasket|olympic)\b", full_basket_text)) and "asia" not in full_basket_text
                            if not (is_wnba or is_it_women or is_top_fiba_women):
                                continue

                        # 4. Asian & Minor Exotic Domestic Competitions
                        ASIAN_FLAGS = ("🇨🇳", "🇰🇷", "🇵🇭", "🇹🇼", "🇯🇵", "🇻🇳", "🇹🇭", "🇮🇩", "🇲🇾", "🇮🇳")
                        has_asian_flag = any(f in raw_title for f in ASIAN_FLAGS)
                        is_asian_exotic_comp = bool(re.search(
                            r"\b(cba|chinese\s*basketball|kbl|korean\s*basketball|pba|philippine\s*basketball|governors\s*cup|commissioner\s*s\s*cup|t1\s*league|tpbl|plg|b\.league|road\s*to\s*b\.?a\.?l\.?|champions\s*clubs|basketball\s*africa\s*league|bal)\b",
                            full_basket_text
                        ))
                        if has_asian_flag or is_asian_exotic_comp:
                            is_top_world_fiba = bool(re.search(r"\b(fiba\s*(?:world\s*cup|qualifier)|olympic)\b", full_basket_text)) and not bool(re.search(r"\b(club|asia|bal|league)\b", full_basket_text))
                            if not is_top_world_fiba:
                                continue

                        # 5. Minor foreign 2nd divisions
                        if re.search(r"\b(aba\s*league\s*2|israeli\s*national\s*league)\b", full_basket_text):
                            continue

                    # Volleyball: filter college volleyball and minor Asian/exotic club leagues
                    is_volley = "volley" in cat_lower or "volley" in raw_title.lower()
                    if is_volley:
                        title_low = raw_title.lower()
                        full_volley_text = f"{cat_lower} {title_low}"
                        is_it_volley = "🇮🇹" in raw_title or bool(re.search(r"\b(superlega|serie\s*a[123]?|coppa\s*italia|italia|italy)\b", full_volley_text))
                        if not is_it_volley:
                            if re.search(r"\b(college\s*volleyball|ncaa|district|universiade|amateur)\b", full_volley_text):
                                continue
                            ASIAN_FLAGS = ("🇨🇳", "🇰🇷", "🇵🇭", "🇹🇼", "🇯🇵", "🇻🇳", "🇹🇭", "🇮🇩", "🇲🇾", "🇮🇳")
                            has_asian_flag = any(f in raw_title for f in ASIAN_FLAGS)
                            is_asian_exotic = has_asian_flag or bool(re.search(r"\b(kovo|pvl|pro\s*challenge|v-league|thailand\s*league)\b", full_volley_text))
                            if is_asian_exotic:
                                is_top_world_fivb = bool(re.search(r"\b(vnl|nations\s*league|world\s*championship|olympic|cev|champions\s*league)\b", full_volley_text))
                                if not is_top_world_fivb:
                                    continue

                    # Tennis: filter table tennis / ping pong leaks
                    is_tennis = "tennis" in cat_lower or "tennis" in raw_title.lower()
                    if is_tennis:
                        title_low = raw_title.lower()
                        if re.search(r"\b(table\s*tennis|ping\s*pong|tt\s*cup|setka\s*cup)\b", title_low):
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

                    # Discard events scheduled more than 3 days ahead
                    now_ms = int(time.time() * 1000)
                    if date_ms and date_ms > (now_ms + 3 * 24 * 3600 * 1000):
                        continue

                    # Check if simulcast / whip-around show (never extract fake teams)
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
                        # Only allow canonical competition to refine silo_id/category if the sport was generic
                        if canon and category in ("other", "unknown", ""):
                            canon_silo = get_canonical_silo(canon)
                            if canon_silo:
                                silo_id = canon_silo
                                category = "football" if canon_silo == "football" else canon_silo
                        comp_disp = get_canonical_display_name(canon) or cat_raw

                    slug = re.sub(r"[^a-z0-9]+", "-", clean_title.lower()).strip("-")[:60]
                    match_id = f"dlhd-{slug}-{str(date_ms)[-6:]}"

                    matches.append({
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
                        "sources": sources
                    })

        self._cache = matches
        self._cache_time = now
        if new_content_hash:
            self._last_content_hash = new_content_hash

        # Smart Cache Invalidation: check upcoming events for today
        now_ms = time.time() * 1000
        future_count = sum(1 for m in matches if (m.get("date") or 0) > now_ms)
        if future_count < 5:
            # Morning lag: schedule likely not yet published by DaddyLive operators; recheck every 15m
            self._cache_ttl = 900  # 15 minutes
            logger.info("DaddyLive has only %d upcoming matches; setting aggressive 15m refresh window", future_count)
        else:
            self._cache_ttl = 3600  # 1 hour

        logger.info("Parsed %d matches from DaddyLive schedule (TTL: %ds, hash: %s)", len(matches), self._cache_ttl, self._last_content_hash[:8])
        return matches

daddylive_api = DaddyLiveAPI()
