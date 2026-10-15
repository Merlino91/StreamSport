import asyncio
import datetime
import html
import json
import logging
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx

from app.services.canonical_competition_service import detect_canonical_competition, get_canonical_display_name
from app.services.team_matching import calculate_match_score, extract_team_words
from app.services.tvvoo_service import tvvoo_service

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
        r"\bla fc\b": "lafc",
        r"\blos angeles fc\b": "lafc",
        r"\bnew york city\b": "nycfc",
        r"\bny city\b": "nycfc",
        r"\bred bulls\b": "rbny",
        r"\bred bull salzburg\b": "salzburg",
        r"\brb leipzig\b": "leipzig",
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
        r"\bsporting cp\b": "sporting",
        r"\bstella rossa\b": "red star",
        r"\bwolverhampton\b": "wolves",
        r"\btottenham\b": "spurs",
    }

    GENERIC_TERMS: Set[str] = {
        "fc", "ac", "cf", "sc", "as", "the", "club", "calcio", "basket", "u19", "u20", "u21", "u23", "vs", "at"
    }

    CONTINENTAL_AREAS: Set[str] = {
        "EUROPA", "MONDO", "ASIA", "SUD AMERICA", "NORD E CENTRO AMERICA", "AFRICA"
    }

    OTT_EXCLUDE: Tuple[str, ...] = (
        "now tv", "sky go", "amazon prime", "prime video", "como tv",
        "srf play", "discovery+", "raiplay", "mediaset infinity", "infinity+",
        "timvision", "diretta gol", "diretta goal", "mosaico", "zona gol",
        "onefootball", "fifa+", "uefa.tv", "red bull tv", "youtube", "twitch",
        "apple tv", "mls season pass", "nba global league pass", "nhl.tv", "fiba tv", "euroleague tv", "mlb.tv"
    )

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
                raw_za = fields.get("ZA", "").strip()
                current_country = fields.get("ZY", "").strip()
                # Clean redundant country prefix (e.g. "ITALIA: Serie A" -> "Serie A")
                if current_country and raw_za.upper().startswith(f"{current_country.upper()}:"):
                    current_league = raw_za[len(current_country) + 1:].strip()
                else:
                    current_league = raw_za
                continue

            # Match block: starts with AA (Match ID)
            if "AA" in fields:
                m_id = fields.get("AA")
                home = fields.get("AE") or fields.get("CX") or ""
                away = fields.get("AF") or ""
                time_epoch = fields.get("AD")
                date_ms = int(time_epoch) * 1000 if time_epoch and time_epoch.isdigit() else 0

                # Extract official broadcast channels from AL field, strictly filtering OTT and mosaico streams
                tv_channels: List[str] = []
                al_raw = fields.get("AL")
                if al_raw:
                    try:
                        al_data = json.loads(al_raw)
                        for tv in al_data.get("1", []):
                            bn = tv.get("BN", "").strip()
                            if bn and not any(ott in bn.lower() for ott in self.OTT_EXCLUDE):
                                tv_channels.append(bn)
                    except Exception:
                        pass

                if home and away:
                    if current_country and current_league:
                        comp_name = f"{current_country}: {current_league}".strip(": ")
                    else:
                        comp_name = current_league or current_country or "Live Sports"

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
                        "tv_channels": tv_channels,
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
        return extract_team_words(name)

    def reconcile_matches(self, matches: List[Dict[str, Any]]) -> int:
        """
        Reconciles stream matches against cached Diretta.it official schedule.
        When matched:
        - Normalizes match title in clean 'Home vs Away' form (while retaining raw_titles)
        - Aligns kickoff date/time to exact UTC minute
        - Assigns official competition if missing
        - Marks _diretta_matched = True, _diretta_home, _diretta_away, _official_date
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

            best_cand: Optional[Dict[str, Any]] = None
            best_score: float = 0.0

            for cand in candidates:
                res = calculate_match_score(m, cand, phase="phase1_diretta")
                if res["matched"] and res["score"] > best_score:
                    best_cand = cand
                    best_score = res["score"]
                    if best_score >= 0.95:
                        break

            if best_cand:
                reconciled_count += 1
                m["_diretta_matched"] = True
                m["_diretta_home"] = best_cand["home"]
                m["_diretta_away"] = best_cand["away"]
                m["_diretta_title"] = f"{best_cand['home']} vs {best_cand['away']}"

                # Non-destructive: preserve raw title if not already tracked
                if "raw_titles" not in m and m.get("title"):
                    m["raw_titles"] = [m["title"]]
                m["title"] = m["_diretta_title"]

                # Adopt exact kickoff time from Diretta
                if best_cand.get("date"):
                    m["_official_date"] = best_cand["date"]
                    m["date"] = best_cand["date"]

                # Adopt official Italian competition if missing or generic
                GENERIC_COMPETITIONS = {
                    "", "other", "unknown", "altri sport", "live sports", "football", "soccer",
                    "calcio", "basket", "tennis", "undefined",
                    "americhe e leghe extra-ue", "altre leghe europee", "altri campionati europei",
                    "calcio internazionale", "partite amichevoli", "campionati minori"
                }
                current_comp = (m.get("competition") or "").lower().strip()
                if not current_comp or current_comp in GENERIC_COMPETITIONS:
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

                # Attach recognized TV channels & enrich with TvVoo streams
                cand_tvs = best_cand.get("tv_channels", [])
                if cand_tvs:
                    m["_diretta_tv_channels"] = cand_tvs
                    existing_urls = {
                        s.get("url") for s in m.get("sources", [])
                        if isinstance(s, dict) and s.get("url")
                    }
                    for ch in cand_tvs:
                        if any(ott in ch.lower() for ott in self.OTT_EXCLUDE):
                            continue
                        tv_streams = tvvoo_service.get_channel_streams(channel_name=ch, country="Italy")
                        if tv_streams:
                            for st in tv_streams:
                                if st.get("url") and st["url"] not in existing_urls:
                                    existing_urls.add(st["url"])
                                    m.setdefault("sources", []).append(st)

        if reconciled_count > 0:
            logger.info("Diretta.it Convalidatore: convalidati e normalizzati %d match con successo.", reconciled_count)

        return reconciled_count

diretta_service = DirettaService()
