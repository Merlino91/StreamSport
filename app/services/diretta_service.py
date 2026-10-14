import asyncio
import datetime
import html
import logging
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx

from app.services.canonical_competition_service import detect_canonical_competition, get_canonical_display_name

logger = logging.getLogger("streamsport.diretta")

# Flashscore sport IDs mapped to (Sport Display Name, StreamSport Silo)
FS_SPORTS: Dict[int, Tuple[str, str]] = {
    1: ("Calcio", "football"),
    2: ("Tennis", "tennis"),
    3: ("Basket", "basketball"),
    4: ("Hockey", "hockey"),
    5: ("Football Americano", "american-football"),
    6: ("Baseball", "baseball"),
    12: ("Volley", "volley"),
}

class DirettaService:
    """
    Primary downstream validator powered by Diretta.it (Flashscore) feed engine.
    Fetches real-time multi-sport official schedule across yesterday, today, and upcoming days (-1 to +2).
    Validates kickoff times, normalizes team names in Italian/clean form, and enriches missing competitions.
    """

    DIRETTA_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "x-fsign": "SW9D1eZo",
        "Referer": "https://www.diretta.it/",
        "Origin": "https://www.diretta.it"
    }

    # Normalized aliases for common club names across Italian & international sources
    TEAM_ALIASES = {
        r"\binternazionale\b": "inter",
        r"\bparis saint germain\b": "psg",
        r"\bparis sg\b": "psg",
        r"\bmanchester city\b": "man city",
        r"\bmanchester united\b": "man utd",
        r"\bman united\b": "man utd",
        r"\bbayern munchen\b": "bayern",
        r"\bbayern monaco\b": "bayern",
        r"\bborussia dortmund\b": "dortmund",
        r"\batletico madrid\b": "atletico",
        r"\bolympiakos\b": "olympiacos",
        r"\bsporting lisbona\b": "sporting",
        r"\bstella rossa\b": "red star",
    }

    GENERIC_TERMS: Set[str] = {
        "fc", "ac", "cf", "sc", "as", "the", "club", "calcio", "basket", "u19", "u20", "u21", "u23", "vs", "at"
    }

    def __init__(self):
        self._cached_events: List[Dict[str, Any]] = []
        self._cache_time: float = 0.0
        self._cache_ttl: float = 1800  # 30 minutes TTL
        self._is_healthy: bool = False
        self._lock = asyncio.Lock()

    @property
    def is_healthy(self) -> bool:
        """Returns True if the last fetch from Diretta.it was successful and returned events."""
        return self._is_healthy and len(self._cached_events) > 0

    def parse_flashscore_feed(self, raw_text: str, sport_name: str, silo_name: str) -> List[Dict[str, Any]]:
        """Parses raw Flashscore stream delimited by ~ and ¬ into structured match dictionaries."""
        matches: List[Dict[str, Any]] = []
        current_league = "Unknown"
        current_country = ""

        blocks = raw_text.split("~")
        for block in blocks:
            if not block:
                continue
            fields: Dict[str, str] = {}
            for item in block.split("¬"):
                if "÷" in item:
                    k, v = item.split("÷", 1)
                    fields[k] = v

            # League header block: starts with ZA (League name), ZY (Country name)
            if "ZA" in fields:
                current_league = fields.get("ZA", "").strip()
                current_country = fields.get("ZY", "").strip()
                continue

            # Match block: starts with AA (Match ID)
            if "AA" in fields:
                m_id = fields.get("AA")
                home = fields.get("AE") or fields.get("CX") or ""
                away = fields.get("AF") or ""
                time_epoch = fields.get("AD")
                date_ms = int(time_epoch) * 1000 if time_epoch and time_epoch.isdigit() else 0

                if home and away:
                    comp_name = f"{current_country}: {current_league}".strip(": ") if current_country else current_league
                    matches.append({
                        "id": f"diretta-{m_id}",
                        "_silo": silo_name,
                        "sport": sport_name,
                        "country": current_country,
                        "league": current_league,
                        "competition": comp_name,
                        "home": home.strip(),
                        "away": away.strip(),
                        "title": f"{home.strip()} vs {away.strip()}",
                        "date": date_ms,
                    })
        return matches

    async def get_events(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Fetches multi-day schedule from Diretta.it across all sports (-1 to +2 days).
        Returns in-memory cache if valid within TTL.
        """
        now = time.time()
        if not force and self._cached_events and (now - self._cache_time < self._cache_ttl):
            return self._cached_events

        async with self._lock:
            if not force and self._cached_events and (now - self._cache_time < self._cache_ttl):
                return self._cached_events

            all_events: List[Dict[str, Any]] = []
            success_count = 0
            fail_count = 0

            # Query days: -1 (yesterday), 0 (today), 1 (tomorrow), 2 (day after tomorrow)
            target_days = (-1, 0, 1, 2)
            tasks = []

            async with httpx.AsyncClient(timeout=9.0, headers=self.DIRETTA_HEADERS) as client:
                async def fetch_sport_day(s_id: int, s_name: str, s_silo: str, day: int):
                    url = f"https://400.flashscore.ninja/400/x/feed/f_{s_id}_{day}_3_it_1"
                    try:
                        res = await client.get(url)
                        if res.status_code == 200 and res.text:
                            return self.parse_flashscore_feed(res.text, s_name, s_silo)
                    except Exception as e:
                        logger.debug("Diretta.it fetch failed for sport %s day %d: %s", s_name, day, e)
                    return []

                for s_id, (s_name, s_silo) in FS_SPORTS.items():
                    for day in target_days:
                        tasks.append(fetch_sport_day(s_id, s_name, s_silo, day))

                results = await asyncio.gather(*tasks, return_exceptions=True)

                for res in results:
                    if isinstance(res, list) and res:
                        all_events.extend(res)
                        success_count += 1
                    else:
                        fail_count += 1

            if len(all_events) > 100:
                self._cached_events = all_events
                self._cache_time = now
                self._is_healthy = True
                logger.info(
                    "Diretta.it feed engine synchronized: %d events indexed across 4 days (success: %d feeds).",
                    len(all_events),
                    success_count,
                )
            else:
                self._is_healthy = False
                logger.warning(
                    "Diretta.it feed engine returned insufficient events (%d events, failed feeds: %d). Marking unhealthy for fallback.",
                    len(all_events),
                    fail_count,
                )

            return self._cached_events

    def _clean_team_words(self, name: str) -> Set[str]:
        t = (name or "").lower()
        t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("utf-8")
        t = re.sub(r"[\U0001F1E6-\U0001F1FF]{2}", "", t)
        for pat, rep in self.TEAM_ALIASES.items():
            t = re.sub(pat, rep, t)
        words = set(re.sub(r"[^a-z0-9]+", " ", t).split()) - self.GENERIC_TERMS
        return {w for w in words if len(w) > 1}

    def reconcile_matches(self, matches: List[Dict[str, Any]]) -> int:
        """
        Reconciles stream matches against cached Diretta.it official schedule.
        When matched:
        - Normalizes match title in clean 'Home vs Away' form
        - Aligns kickoff date/time to exact UTC minute
        - Assigns official competition if missing
        - Marks _diretta_matched = True
        """
        if not self._cached_events or not matches:
            return 0

        # Group Diretta events by sport silo
        by_silo: Dict[str, List[Dict[str, Any]]] = {}
        for ev in self._cached_events:
            by_silo.setdefault(ev.get("_silo", "altri_sport"), []).append(ev)

        reconciled_count = 0

        for m in matches:
            # Determine silo
            silo = (m.get("_silo") or "").lower().strip()
            if not silo:
                from app.services.catalog_service import catalog_service
                silo = catalog_service.get_canonical_silo(m)

            candidates = by_silo.get(silo, [])
            if not candidates:
                continue

            m_date = m.get("date") or 0
            m_title = m.get("title") or ""
            teams = m.get("teams")

            m_home_words: Set[str] = set()
            m_away_words: Set[str] = set()

            if isinstance(teams, dict) and teams.get("home") and teams.get("away"):
                m_home_words = self._clean_team_words(teams.get("home", {}).get("name", ""))
                m_away_words = self._clean_team_words(teams.get("away", {}).get("name", ""))
            elif " vs " in m_title.lower():
                parts = re.split(r"\s+vs\s+", m_title, maxsplit=1, flags=re.IGNORECASE)
                m_home_words = self._clean_team_words(parts[0])
                m_away_words = self._clean_team_words(parts[1])

            if not m_home_words or not m_away_words:
                continue

            best_cand: Optional[Dict[str, Any]] = None

            for cand in candidates:
                c_date = cand.get("date") or 0
                # Strict kickoff proximity: within +/- 90 minutes
                if m_date and c_date:
                    if abs(m_date - c_date) > 90 * 60 * 1000:
                        continue

                c_home_words = self._clean_team_words(cand.get("home", ""))
                c_away_words = self._clean_team_words(cand.get("away", ""))
                if not c_home_words or not c_away_words:
                    continue

                # Direct Home-Home and Away-Away match
                home_match = bool(m_home_words & c_home_words)
                away_match = bool(m_away_words & c_away_words)

                if home_match and away_match:
                    best_cand = cand
                    break

                # Inverted match (for US "at" format or reversed streams)
                home_inv = bool(m_home_words & c_away_words)
                away_inv = bool(m_away_words & c_home_words)

                if home_inv and away_inv:
                    best_cand = cand
                    break

            if best_cand:
                reconciled_count += 1
                m["_diretta_matched"] = True
                m["title"] = f"{best_cand['home']} vs {best_cand['away']}"

                # Adopt exact kickoff time from Diretta
                if best_cand.get("date"):
                    m["date"] = best_cand["date"]

                # Adopt official Italian competition if missing or generic
                current_comp = (m.get("competition") or "").lower().strip()
                if not current_comp or current_comp in ("other", "unknown", "altri sport", "live sports"):
                    m["competition"] = best_cand["competition"]
                    m["_competition"] = best_cand["competition"]

                # Enrich canonical competition tag if missing
                if not m.get("canonical_competition") and best_cand.get("competition"):
                    canon = detect_canonical_competition(
                        f"{best_cand['competition']} : {m['title']}", silo_hint=silo
                    )
                    if canon:
                        m["canonical_competition"] = canon
                        m["_canonical_competition"] = canon

        if reconciled_count > 0:
            logger.info("Diretta.it Convalidatore: convalidati e normalizzati %d match con successo.", reconciled_count)

        return reconciled_count

diretta_service = DirettaService()
