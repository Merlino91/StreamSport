import asyncio
import datetime
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import httpx

from app.config import ENABLE_VIRGILIO, VIRGILIO_CACHE_INTERVAL
from app.services.tvvoo_service import tvvoo_service

logger = logging.getLogger("streamsport.virgilio_service")


class VirgilioService:
    """
    Scrapes and normalizes the daily Italian sports TV guide from Virgilio Sport (sport.virgilio.it/guida-tv/).
    Maps listed broadcasters (Sky Sport, DAZN, Eurosport, Rai Sport, etc.) directly to TvVoo/Vavoo streaming targets.
    Filters out ghost events (events with no playable TvVoo sources).
    Maintains a rolling cache to prevent evening matches from disappearing after midnight.
    Refreshes every 6 hours (configurable via VIRGILIO_CACHE_INTERVAL).
    """

    GUIDA_TV_URL = "https://sport.virgilio.it/guida-tv/"

    SPORT_MAP: Dict[str, Tuple[str, str]] = {
        "calcio": ("football", "football"),
        "calcio femminile": ("football", "football"),
        "basket": ("basketball", "basketball"),
        "tennis": ("tennis", "tennis"),
        "motociclismo": ("motor-sports", "motori"),
        "pallavolo": ("volleyball", "volley"),
        "pallavolo femminile": ("volleyball", "volley"),
        "ciclismo": ("cycling", "altri_sport"),
        "ciclismo femminile": ("cycling", "altri_sport"),
        "rugby": ("rugby", "altri_sport"),
    }

    # Italian to English country name translations for International National Teams
    COUNTRY_MAP: Dict[str, str] = {
        "norvegia": "Norway",
        "portogallo": "Portugal",
        "lituania": "Lithuania",
        "azerbaigian": "Azerbaijan",
        "spagna": "Spain",
        "francia": "France",
        "germania": "Germany",
        "inghilterra": "England",
        "italia": "Italy",
        "olanda": "Netherlands",
        "paesi bassi": "Netherlands",
        "croazia": "Croatia",
        "serbia": "Serbia",
        "belgio": "Belgium",
        "svizzera": "Switzerland",
        "austria": "Austria",
        "svezia": "Sweden",
        "danimarca": "Denmark",
        "polonia": "Poland",
        "repubblica ceca": "Czechia",
        "cechia": "Czechia",
        "turchia": "Turkey",
        "grecia": "Greece",
        "brasile": "Brazil",
        "argentina": "Argentina",
        "stati uniti": "USA",
        "ucraina": "Ukraine",
        "irlanda": "Ireland",
        "scozia": "Scotland",
        "galles": "Wales",
        "romania": "Romania",
        "bulgaria": "Bulgaria",
        "ungheria": "Hungary",
        "slovacchia": "Slovakia",
        "slovenia": "Slovenia",
        "finlandia": "Finland",
    }

    def __init__(self):
        self._cached_matches: List[Dict[str, Any]] = []
        self._last_fetch_time: float = 0.0
        self._fetch_lock = asyncio.Lock()

    @staticmethod
    def _parse_event_cell(cell_text: str) -> Tuple[str, str, str]:
        """
        Parses Virgilio event cell formatted as: 'Sport , Competition: Details'
        Returns (sport, competition, details).
        """
        raw = cell_text.strip()
        raw = re.sub(r"\s+", " ", raw)
        sport = ""
        comp = ""
        details = ""

        if "," in raw:
            parts = raw.split(",", 1)
            sport = parts[0].strip().lower()
            rest = parts[1].strip()
            if ":" in rest:
                c_parts = rest.split(":", 1)
                comp = c_parts[0].strip()
                details = c_parts[1].strip()
            else:
                comp = rest
        elif ":" in raw:
            c_parts = raw.split(":", 1)
            sport = c_parts[0].strip().lower()
            details = c_parts[1].strip()
        else:
            details = raw

        return sport, comp, details

    def _normalize_title_and_teams(self, comp: str, details: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Normalizes team pairings (e.g. 'Casertana-Catania' -> 'Casertana vs Catania')
        and extracts structured {home: {name: ...}, away: {name: ...}} object.
        Translates Italian national team names for international deduplication.
        """
        d = details.strip()
        # Clean quotes and decorative dashes
        d = d.replace("’", "'").replace("‘", "'")
        teams_dict: Optional[Dict[str, Any]] = None

        if "-" in d and " vs " not in d.lower():
            parts = d.split("-")
            if len(parts) == 2 and len(parts[0].strip()) > 1 and len(parts[1].strip()) > 1:
                h_raw, a_raw = parts[0].strip(), parts[1].strip()
                # Check for country translation (Nations League, etc.)
                h_name = self.COUNTRY_MAP.get(h_raw.lower(), h_raw)
                a_name = self.COUNTRY_MAP.get(a_raw.lower(), a_raw)
                d = f"{h_name} vs {a_name}"
                teams_dict = {
                    "home": {"name": h_name},
                    "away": {"name": a_name},
                }
        elif " vs " in d.lower():
            parts = re.split(r"\s+vs\s+", d, flags=re.IGNORECASE)
            if len(parts) == 2:
                teams_dict = {
                    "home": {"name": parts[0].strip()},
                    "away": {"name": parts[1].strip()},
                }

        formatted_title = f"{comp}: {d}" if comp and d else (d or comp)
        return formatted_title, teams_dict

    @staticmethod
    def _compute_event_timestamp(time_str: str) -> int:
        """
        Converts 'HH:MM' string in Europe/Rome timezone to epoch ms for today.
        """
        tz = ZoneInfo("Europe/Rome")
        now_rome = datetime.datetime.now(tz)
        try:
            parts = time_str.split(":")
            hour = int(parts[0])
            minute = int(parts[1])
            event_dt = now_rome.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return int(event_dt.timestamp() * 1000)
        except Exception:
            return int(now_rome.timestamp() * 1000)

    async def get_matches(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Fetches today's TV schedule from Virgilio Sport and maps to TvVoo streams.
        Returns only events with at least ONE valid TvVoo stream (no ghost events).
        Merges with ongoing matches from cache to prevent post-midnight dropouts.
        """
        if not ENABLE_VIRGILIO:
            return []

        now = time.time()
        now_ms = now * 1000
        if not force and self._cached_matches and (now - self._last_fetch_time) < VIRGILIO_CACHE_INTERVAL:
            return self._cached_matches

        async with self._fetch_lock:
            if not force and self._cached_matches and (now - self._last_fetch_time) < VIRGILIO_CACHE_INTERVAL:
                return self._cached_matches

            # Ensure TvVoo channel directory is synced
            await tvvoo_service.ensure_synced()

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }

            try:
                async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                    resp = await client.get(self.GUIDA_TV_URL)
                    if resp.status_code != 200:
                        logger.warning("Virgilio Sport Guida TV HTTP error %d", resp.status_code)
                        return self._cached_matches
                    html_content = resp.text
            except Exception as e:
                logger.error("Failed to fetch Virgilio Sport Guida TV: %s", e)
            # Limit parsing strictly to the "Oggi" section of the TV guide
            oggi_match = re.search(
                r"<h2[^>]*>.*?oggi.*?</h2>(.*?)(?:<h2[^>]*>|$)",
                html_content,
                re.IGNORECASE | re.DOTALL,
            )
            section_html = oggi_match.group(1) if oggi_match else html_content
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", section_html, re.DOTALL)
            fetched_matches: List[Dict[str, Any]] = []

            for row in rows:
                tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
                if len(tds) < 3:
                    continue

                time_raw = re.sub(r"<[^>]+>", "", tds[0]).strip()
                event_raw = re.sub(r"<[^>]+>", " ", tds[1]).strip()
                channels_raw = re.sub(r"<[^>]+>", " ", tds[2]).strip()

                if not time_raw or not event_raw or not channels_raw:
                    continue

                sport_raw, comp, details = self._parse_event_cell(event_raw)
                title, teams_dict = self._normalize_title_and_teams(comp, details)
                if not title:
                    continue

                # Category & Silo Resolution
                cat, silo = self.SPORT_MAP.get(sport_raw, ("altri_sport", "altri_sport"))

                # Resolve TvVoo sources for this event
                sources: List[Dict[str, Any]] = []
                channel_names = [ch.strip() for ch in channels_raw.split(",") if ch.strip()]
                seen_keys = set()

                for ch in channel_names:
                    tvvoo_streams = tvvoo_service.get_channel_streams(ch)
                    for st in tvvoo_streams:
                        st_url = st.get("url")
                        key = (st.get("canonical"), st.get("tag"))
                        if st_url and key not in seen_keys:
                            seen_keys.add(key)
                            sources.append({
                                "source": "tvvoo",
                                "id": st.get("canonical", "tvvoo"),
                                "name": st.get("display_name", ch),
                                "url": st_url,
                                "tag": st.get("tag", "c"),
                            })


                # GHOST EVENT FILTER:
                # Do NOT include in catalog if no playable TvVoo source exists!
                if not sources:
                    continue

                date_ms = self._compute_event_timestamp(time_raw)
                slug_title = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                match_id = f"virgilio_{slug_title}_{int(date_ms / 1000)}"

                match_obj: Dict[str, Any] = {
                    "id": match_id,
                    "title": title,
                    "category": cat,
                    "_silo": silo,
                    "competition": comp,
                    "_competition": comp,
                    "date": date_ms,
                    "sources": sources,
                    "popular": False,
                    "poster": None,
                }
                if teams_dict:
                    match_obj["teams"] = teams_dict

                fetched_matches.append(match_obj)

            # Rolling Cache / Anti-Midnight Dropout:
            # Preserve ongoing events from previous cache that started within the last 3.5 hours
            retained_old: List[Dict[str, Any]] = []
            for prev_m in self._cached_matches:
                p_date = prev_m.get("date", 0)
                # If event started less than 3.5 hours ago, keep it active!
                if p_date and (now_ms - p_date) < (3.5 * 3600 * 1000):
                    # Check if already present in fetched_matches
                    if not any(curr.get("id") == prev_m.get("id") for curr in fetched_matches):
                        retained_old.append(prev_m)

            final_matches = retained_old + fetched_matches
            self._cached_matches = final_matches
            self._last_fetch_time = now
            logger.info(
                "Virgilio Sport sync complete. Parsed %d events with active TvVoo streams (%d retained from ongoing evening window).",
                len(fetched_matches),
                len(retained_old),
            )
            return self._cached_matches


virgilio_service = VirgilioService()
