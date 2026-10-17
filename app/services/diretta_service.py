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

# Flashscore Italian feeds (Project 400 - Diretta.it): extracts Italian names & Italian TV channels
FS_IT_SPORTS: Dict[int, Tuple[str, str]] = {
    1: ("Calcio", "football"),
    2: ("Tennis", "tennis"),
    3: ("Basket", "basketball"),
    12: ("Volley", "volley"),
}

# Flashscore English feeds (Project 2 - Flashscore Global): English team names, TV channels disabled
FS_EN_SPORTS: Dict[int, Tuple[str, str]] = {
    1: ("Calcio", "football"),
    3: ("Basket", "basketball"),
    4: ("Hockey", "hockey"),
    5: ("Football Americano", "american-football"),
    6: ("Baseball", "baseball"),
}

# Combined mapping for backward compatibility
FS_SPORTS: Dict[int, Tuple[str, str]] = {**FS_IT_SPORTS, **FS_EN_SPORTS}

class DirettaService:
    """
    Primary downstream validator powered by Diretta.it and Flashscore Global feed engine.
    Fetches multi-sport official schedule across yesterday, today, and tomorrow (-1 to +1).
    Validates kickoff times, normalizes team names in Italian/clean form, and enriches missing competitions.
    """

    DIRETTA_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "x-fsign": "SW9D1eZo",
        "Referer": "https://www.diretta.it/",
        "Origin": "https://www.diretta.it"
    }

    FLASHSCORE_EN_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "x-fsign": "SW9D1eZo",
        "Referer": "https://www.flashscore.com/",
        "Origin": "https://www.flashscore.com"
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

    GENERIC_COMPETITIONS: Set[str] = {
        "", "other", "unknown", "altri sport", "live sports", "football", "soccer",
        "calcio", "basket", "basketball", "pallacanestro", "tennis", "undefined",
        "americhe e leghe extra-ue", "altre leghe europee", "altri campionati europei",
        "calcio internazionale", "partite amichevoli", "campionati minori",
        "leghe europee hockey", "leghe europee", "european leagues", "ice hockey", "hockey",
        "volleyball", "volley", "pallavolo", "motor-sports", "motorsport",
        "american-football", "american football", "baseball", "fight", "boxe", "mma"
    }

    @classmethod
    def is_generic_competition(cls, comp: Optional[str]) -> bool:
        """Checks if a competition name is missing or generic (ignoring decorative emojis)."""
        if not comp:
            return True
        c = re.sub(r"[\U0001F1E6-\U0001F1FF\U0001F300-\U0001F9FF]", "", comp).lower().strip()
        c = re.sub(r"\s+", " ", c).strip(" :-–|")
        return not c or c in cls.GENERIC_COMPETITIONS

    def __init__(self):
        self._cached_events: List[Dict[str, Any]] = []
        self._en_events_by_silo: Dict[str, List[Dict[str, Any]]] = {}
        self._cache_time: float = 0.0
        self._cache_ttl: float = 1800  # 30 minutes TTL
        self._is_healthy: bool = False
        self._lock = asyncio.Lock()

    @property
    def is_healthy(self) -> bool:
        """Returns True if the last fetch from Diretta.it was successful and returned events."""
        return self._is_healthy and len(self._cached_events) > 0

    def parse_flashscore_feed(
        self,
        raw_text: str,
        sport_name: str,
        silo_name: str,
        extract_tv_channels: bool = True
    ) -> List[Dict[str, Any]]:
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

                # Extract official broadcast channels only for Italian feeds (strictly filtering OTT)
                tv_channels: List[str] = []
                if extract_tv_channels:
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
                    # Clean competition name: use league name directly (fallback to country or Live Sports)
                    comp_name = current_league.strip() if current_league else (current_country or "Live Sports")

                    matches.append({
                        "id": f"diretta-{m_id}",
                        "flashscore_id": m_id,
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
        Fetches multi-day schedule from Flashscore IT and Global EN engines across yesterday, today, tomorrow (-1 to +1).
        Enriches Italian events with native English club names (alt_home/alt_away).
        """
        now = time.time()
        if not force and self._cached_events and (now - self._cache_time < self._cache_ttl):
            return self._cached_events

        async with self._lock:
            if not force and self._cached_events and (now - self._cache_time < self._cache_ttl):
                return self._cached_events

            target_days = (-1, 0, 1)
            tasks = []
            success_count = 0
            fail_count = 0

            async with httpx.AsyncClient(timeout=9.0) as client:
                async def fetch_it(s_id: int, s_name: str, s_silo: str, day: int):
                    url = f"https://400.flashscore.ninja/400/x/feed/f_{s_id}_{day}_3_it_1"
                    try:
                        res = await client.get(url, headers=self.DIRETTA_HEADERS)
                        if res.status_code == 200 and res.text:
                            return ("it", self.parse_flashscore_feed(res.text, s_name, s_silo, extract_tv_channels=True))
                    except Exception as e:
                        logger.debug("Diretta IT fetch failed for sport %s day %d: %s", s_name, day, e)
                    return ("it", [])

                async def fetch_en(s_id: int, s_name: str, s_silo: str, day: int):
                    url = f"https://2.flashscore.ninja/2/x/feed/f_{s_id}_{day}_3_en-uk_1"
                    try:
                        res = await client.get(url, headers=self.FLASHSCORE_EN_HEADERS)
                        if res.status_code == 200 and res.text:
                            return ("en", self.parse_flashscore_feed(res.text, s_name, s_silo, extract_tv_channels=False))
                    except Exception as e:
                        logger.debug("Flashscore EN fetch failed for sport %s day %d: %s", s_name, day, e)
                    return ("en", [])

                for s_id, (s_name, s_silo) in FS_IT_SPORTS.items():
                    for day in target_days:
                        tasks.append(fetch_it(s_id, s_name, s_silo, day))

                for s_id, (s_name, s_silo) in FS_EN_SPORTS.items():
                    for day in target_days:
                        tasks.append(fetch_en(s_id, s_name, s_silo, day))

                results = await asyncio.gather(*tasks, return_exceptions=True)

                it_events: List[Dict[str, Any]] = []
                en_events: List[Dict[str, Any]] = []
                en_by_id: Dict[str, Dict[str, Any]] = {}
                en_events_by_silo: Dict[str, List[Dict[str, Any]]] = {}

                for res in results:
                    if isinstance(res, tuple) and len(res) == 2:
                        origin, ev_list = res
                        if isinstance(ev_list, list) and ev_list:
                            success_count += 1
                            if origin == "it":
                                it_events.extend(ev_list)
                            else:
                                for ev in ev_list:
                                    en_events.append(ev)
                                    fid = ev.get("flashscore_id")
                                    if fid:
                                        en_by_id[fid] = ev
                                    en_events_by_silo.setdefault(ev.get("_silo", "altri_sport"), []).append(ev)
                        else:
                            fail_count += 1
                    else:
                        fail_count += 1

                # Enrich Italian events with alt_home / alt_away from English feed
                for it_ev in it_events:
                    fid = it_ev.get("flashscore_id")
                    if fid and fid in en_by_id:
                        en_m = en_by_id[fid]
                        it_ev["alt_home"] = en_m["home"]
                        it_ev["alt_away"] = en_m["away"]
                        it_ev["alt_title"] = en_m["title"]

                # Consolidated cache: IT events + EN-only sports (Hockey, American Football, Baseball)
                # and any EN events not covered by IT
                all_consolidated = list(it_events)
                it_fids = {ev.get("flashscore_id") for ev in it_events if ev.get("flashscore_id")}
                for en_ev in en_events:
                    fid = en_ev.get("flashscore_id")
                    if fid not in it_fids:
                        all_consolidated.append(en_ev)

                if len(all_consolidated) > 100:
                    self._cached_events = all_consolidated
                    self._en_events_by_silo = en_events_by_silo
                    self._cache_time = now
                    self._is_healthy = True
                    logger.info(
                        "Diretta.it feed engine synchronized: %d events indexed (IT: %d, EN: %d).",
                        len(all_consolidated),
                        len(it_events),
                        len(en_events),
                    )
                else:
                    self._is_healthy = False
                    logger.warning(
                        "Diretta.it feed engine returned insufficient events (%d events, failed feeds: %d). Marking unhealthy for fallback.",
                        len(all_consolidated),
                        fail_count,
                    )

            return self._cached_events

    def _clean_team_words(self, name: str) -> Set[str]:
        return extract_team_words(name)

    def reconcile_matches(self, matches: List[Dict[str, Any]]) -> int:
        """
        Reconciles stream matches using a 2-pass Flashscore algorithm:
        Pass 1: Identifies the match on the English feed (to obtain the universal Flashscore ID).
        Pass 2: Reconciles against Italian / Consolidated candidates with a +0.20 Match ID bonus,
                inheriting official Italian titles and TV channels.
        """
        if not self._cached_events or not matches:
            return 0

        # Group consolidated events by silo
        by_silo: Dict[str, List[Dict[str, Any]]] = {}
        for ev in self._cached_events:
            by_silo.setdefault(ev.get("_silo", "altri_sport"), []).append(ev)

        reconciled_count = 0

        for m in matches:
            silo = (m.get("_silo") or "").lower().strip()
            if not silo:
                from app.services.catalog_service import catalog_service
                silo = catalog_service.get_canonical_silo(m)

            m_date = m.get("date") or 0

            # Pass 1: Flashscore EN Identification
            en_cands = self._en_events_by_silo.get(silo, [])
            if m_date and en_cands:
                en_cands = [e for e in en_cands if abs((e.get("date") or 0) - m_date) <= 90 * 60 * 1000]

            best_en_cand: Optional[Dict[str, Any]] = None
            best_en_score: float = 0.0
            for en_c in en_cands:
                sc = calculate_match_score(m, en_c, phase="phase1_diretta")
                if sc["matched"] and sc["score"] > best_en_score:
                    best_en_score = sc["score"]
                    best_en_cand = en_c
                    if best_en_score >= 0.95:
                        break

            if best_en_cand and best_en_score >= 0.75:
                m["_flashscore_id"] = best_en_cand.get("flashscore_id") or best_en_cand.get("id")
                # DOMINANT: Adopt official English competition and country from Livesport (Flashscore EN)
                en_comp = (best_en_cand.get("competition") or "").strip()
                if en_comp and not self.is_generic_competition(en_comp):
                    m["competition"] = en_comp
                    m["_competition"] = en_comp
                    if best_en_cand.get("country"):
                        m["country"] = best_en_cand["country"]
                        m["_country"] = best_en_cand["country"]
                    en_canon = detect_canonical_competition(
                        f"{en_comp} : {best_en_cand.get('title') or m.get('title', '')}", silo_hint=silo
                    )
                    if en_canon:
                        m["canonical_competition"] = en_canon
                        m["_canonical_competition"] = en_canon

            # Pass 2: Reconcile against Italian / Consolidated candidates (with Match ID bonus/penalty)
            candidates = by_silo.get(silo, [])
            if m_date and candidates:
                candidates = [e for e in candidates if abs((e.get("date") or 0) - m_date) <= 90 * 60 * 1000]

            best_cand: Optional[Dict[str, Any]] = None
            best_score: float = 0.0
            for cand in candidates:
                res = calculate_match_score(m, cand, phase="phase1_diretta")
                if res["matched"] and res["score"] > best_score:
                    best_cand = cand
                    best_score = res["score"]
                    if best_score >= 0.95:
                        break

            # If an Italian/consolidated candidate matched, use it; otherwise fallback to the EN match
            chosen = best_cand if (best_cand and best_score >= 0.75) else (best_en_cand if (best_en_cand and best_en_score >= 0.75) else None)

            if chosen:
                reconciled_count += 1
                m["_diretta_matched"] = True
                m["_diretta_home"] = chosen["home"]
                m["_diretta_away"] = chosen["away"]
                m["_diretta_title"] = f"{chosen['home']} vs {chosen['away']}"

                # Non-destructive: preserve raw title if not already tracked
                if "raw_titles" not in m and m.get("title"):
                    m["raw_titles"] = [m["title"]]
                m["title"] = m["_diretta_title"]

                # Adopt exact kickoff time from Diretta
                if chosen.get("date"):
                    m["_official_date"] = chosen["date"]
                    m["date"] = chosen["date"]

                # Adopt country from chosen validator candidate
                if chosen.get("country") and not m.get("country"):
                    m["country"] = chosen["country"]
                    m["_country"] = chosen["country"]

                # Inherit confirmed traits from chosen candidate
                c_text = f"{chosen.get('competition', '')} {chosen.get('home', '')} {chosen.get('away', '')}".lower()
                if re.search(r"\b(women(?:[’']s)?|femminile|femenino|ladies|frauen|w-|wta|wnba|uwcl)\b", c_text):
                    m["_gender"] = "+"
                if re.search(r"\b(u-?1[5-9]|u-?2[01]|under\s*1[5-9]|under\s*2[01]|youth|primavera|junior)\b", c_text):
                    m["_age"] = "+"

                # Adopt official Italian competition ONLY if missing or generic (Livesport English competition is dominant)
                current_comp = m.get("competition")
                if self.is_generic_competition(current_comp):
                    m["competition"] = chosen["competition"]
                    m["_competition"] = chosen["competition"]

                # Enrich canonical competition tag if still missing
                if not m.get("canonical_competition") and chosen.get("competition"):
                    canon = detect_canonical_competition(
                        f"{chosen['competition']} : {m['title']}", silo_hint=silo
                    )
                    if canon:
                        m["canonical_competition"] = canon
                        m["_canonical_competition"] = canon

                # Attach recognized TV channels & enrich with TvVoo streams (only if present, e.g. from IT feed)
                cand_tvs = chosen.get("tv_channels", [])
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
