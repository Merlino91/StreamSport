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

from app.services.canonical_competition_service import detect_canonical_competition
from app.utils.demographics import extract_demographics

logger = logging.getLogger("streamsport.diretta")

# Universal multi-sport mapping for Diretta.it and Flashscore Global
FS_SUPPORTED_SPORTS: Dict[int, Tuple[str, str]] = {
    1: ("Calcio", "football"),
    2: ("Tennis", "tennis"),
    3: ("Basket", "basketball"),
    4: ("Hockey", "hockey"),
    5: ("Football Americano", "american-football"),
    6: ("Baseball", "baseball"),
    7: ("Pallamano", "handball"),
    8: ("Rugby", "rugby"),
    12: ("Volley", "volley"),
    16: ("Boxe", "fight"),
    28: ("MMA", "fight"),
    32: ("Formula 1", "motor-sports"),
    33: ("MotoGP", "motor-sports"),
}

FS_IT_SPORTS: Dict[int, Tuple[str, str]] = FS_SUPPORTED_SPORTS
FS_EN_SPORTS: Dict[int, Tuple[str, str]] = FS_SUPPORTED_SPORTS

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
        self._cache_time: float = 0.0
        self._cache_ttl: float = 1800  # 30 minutes TTL
        self._lock = asyncio.Lock()

    @staticmethod
    def reconstruct_tennis_name(abbrev: str, slug: str) -> str:
        """Reconstructs 'Jasmine Paolini' from 'Paolini J.' and slug 'paolini-jasmine'."""
        if not slug or not abbrev:
            return abbrev.strip()
        if "/" in abbrev:
            return abbrev.strip()
        try:
            # Strip trailing initials: ' J.', ' J. M.', ' J-L.', etc.
            last_name = re.sub(r"(?:\s+[A-Za-z][\.\-]?[A-Za-z]?\.?)+$", "", abbrev).strip()
            slug_tokens = slug.split("-")
            last_tokens = [re.sub(r"[^a-zA-Z]", "", t).lower() for t in last_name.split() if t]
            first_tokens = [t.capitalize() for t in slug_tokens if t.lower() not in last_tokens]
            if first_tokens:
                fn = " ".join(first_tokens)
                return f"{fn} {last_name}".strip()
        except Exception:
            pass
        return abbrev.strip()

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
        raw_za = ""
        current_session = ""
        emitted_motorsport_keys: Set[str] = set()

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
                current_session = fields.get("ZAE", "").strip()
                # Clean redundant country prefix (e.g. "ITALIA: Serie A" -> "Serie A")
                if current_country and raw_za.upper().startswith(f"{current_country.upper()}:"):
                    current_league = raw_za[len(current_country) + 1:].strip()
                else:
                    current_league = raw_za
                emitted_motorsport_keys.clear()
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

                # Special handling for motor-sports non-1v1 session cards (e.g. F1, MotoGP, NASCAR)
                # Flashscore lists each individual driver (20+ drivers) as separate AA blocks under the session header.
                # Deduplicate by (raw_za, session, time) to emit exactly one session card instead of 20+ driver duplicates.
                if silo_name == "motor-sports":
                    sess_name = fields.get("ZAE", "").strip() or current_session
                    sess_key = f"{raw_za}_{sess_name}_{time_epoch}"
                    if date_ms > 0 and raw_za and m_id and sess_key not in emitted_motorsport_keys:
                        emitted_motorsport_keys.add(sess_key)
                        comp_name = current_league.strip() if current_league else (current_country or sport_name)
                        sess_title = f"{raw_za} - {sess_name}" if sess_name and sess_name.lower() not in raw_za.lower() else raw_za
                        matches.append({
                            "id": f"diretta-{m_id}",
                            "flashscore_id": m_id,
                            "_silo": silo_name,
                            "sport": sport_name,
                            "country": current_country,
                            "league": current_league,
                            "competition": comp_name,
                            "session": sess_name,
                            "home": "",
                            "away": "",
                            "full_home": "",
                            "full_away": "",
                            "title": sess_title,
                            "date": date_ms,
                            "tv_channels": tv_channels,
                        })
                    continue

                if home and away:
                    # Clean competition name: use league name directly (fallback to country or Live Sports)
                    comp_name = current_league.strip() if current_league else (current_country or "Live Sports")

                    full_home = home.strip()
                    full_away = away.strip()
                    if silo_name == "tennis":
                        full_home = self.reconstruct_tennis_name(home, fields.get("WU") or "")
                        full_away = self.reconstruct_tennis_name(away, fields.get("WV") or "")

                    is_w, is_y = extract_demographics(current_league)

                    match_obj = {
                        "id": f"diretta-{m_id}",
                        "flashscore_id": m_id,
                        "_silo": silo_name,
                        "sport": sport_name,
                        "country": current_country,
                        "league": current_league,
                        "competition": comp_name,
                        "home": home.strip(),
                        "away": away.strip(),
                        "full_home": full_home,
                        "full_away": full_away,
                        "title": f"{full_home} vs {full_away}" if silo_name == "tennis" else f"{home.strip()} vs {away.strip()}",
                        "date": date_ms,
                        "tv_channels": tv_channels,
                    }
                    if is_w:
                        match_obj["is_women"] = True
                    if is_y:
                        match_obj["is_youth"] = True

                    matches.append(match_obj)
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

            target_days = (-1, 0, 1, 2)
            tasks = []
            success_count = 0
            fail_count = 0
            sem = asyncio.Semaphore(8)

            async with httpx.AsyncClient(timeout=9.0) as client:
                async def fetch_it(s_id: int, s_name: str, s_silo: str, day: int):
                    url = f"https://400.flashscore.ninja/400/x/feed/f_{s_id}_{day}_3_it_1"
                    async with sem:
                        try:
                            res = await client.get(url, headers=self.DIRETTA_HEADERS)
                            if res.status_code == 200 and res.text:
                                return ("it", self.parse_flashscore_feed(res.text, s_name, s_silo, extract_tv_channels=True))
                        except Exception as e:
                            logger.debug("Diretta IT fetch failed for sport %s day %d: %s", s_name, day, e)
                        return ("it", [])

                async def fetch_en(s_id: int, s_name: str, s_silo: str, day: int):
                    url = f"https://2.flashscore.ninja/2/x/feed/f_{s_id}_{day}_3_en-uk_1"
                    async with sem:
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
                    self._cache_time = now
                    logger.info(
                        "Diretta.it feed engine synchronized: %d events indexed (IT: %d, EN: %d).",
                        len(all_consolidated),
                        len(it_events),
                        len(en_events),
                    )
                else:
                    logger.warning(
                        "Diretta.it feed engine returned insufficient events (%d events, failed feeds: %d).",
                        len(all_consolidated),
                        fail_count,
                    )

            return self._cached_events

    async def get_official_sliding_schedule(
        self,
        cutoff_hours_behind: int = 6,
        hours_ahead: int = 48,
        force: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Extracts the official multi-sport schedule from Diretta/Flashscore within the sliding window
        (yesterday last 6 hours up to +48h), strictly admitting only Tier 1 and Tier 2 competitions.
        Produces complete official match dictionaries ready for catalog indexing.
        """
        all_events = await self.get_events(force=force)
        now_ms = time.time() * 1000
        min_ms = now_ms - (cutoff_hours_behind * 3600 * 1000)
        max_ms = now_ms + (hours_ahead * 3600 * 1000)

        from app.services.canonical_competition_service import is_tier_match, detect_canonical_competition
        from app.services.genre_classifier import genre_classifier

        official_matches: List[Dict[str, Any]] = []
        seen_fids: Set[str] = set()

        for m in all_events:
            fid = m.get("flashscore_id") or m.get("id")
            if not fid or fid in seen_fids:
                continue

            date_ms = m.get("date", 0)
            if date_ms and (date_ms < min_ms or date_ms > max_ms):
                continue

            admitted, slug = is_tier_match(m)
            if not admitted:
                continue

            seen_fids.add(fid)

            d_home = m.get("full_home") or m.get("home", "")
            d_away = m.get("full_away") or m.get("away", "")
            silo = m.get("_silo", "altri_sport")
            comp = m.get("competition") or m.get("league", "")
            country = m.get("country", "")

            # Italian names are primary for UI/catalog; English (Flashscore UK) are kept in alt_* for TSDB/providers
            if silo == "motor-sports":
                home = ""
                away = ""
                title = m.get("title", "")
                alt_home = ""
                alt_away = ""
                alt_title = m.get("title", "")
                teams_obj: Dict[str, Any] = {}
                alt_teams_obj: List[Dict[str, Any]] = []
            else:
                home = d_home
                away = d_away
                title = f"{d_home} vs {d_away}"
                alt_home = m.get("alt_home") or d_home
                alt_away = m.get("alt_away") or d_away
                alt_title = m.get("alt_title") or f"{alt_home} vs {alt_away}"
                teams_obj = {
                    "home": {"name": home},
                    "away": {"name": away},
                }
                alt_teams_obj = [
                    {"home": {"name": alt_home}, "away": {"name": alt_away}},
                ]

            # Infer canonical slug fallback if needed
            if not slug:
                fallback_text = title if silo == "motor-sports" else f"{country}: {comp} {title}"
                slug = detect_canonical_competition(fallback_text, silo_hint=silo)

            official_match = {
                "id": f"diretta-{fid}",
                "flashscore_id": fid,
                "_official_id": fid,
                "title": title,
                "category": silo,
                "_silo": silo,
                "sport": m.get("sport", ""),
                "session": m.get("session", ""),
                "home": home,
                "away": away,
                "alt_home": alt_home,
                "alt_away": alt_away,
                "alt_title": alt_title,
                "date": date_ms,
                "_date_ms": date_ms,
                "teams": teams_obj,
                "alt_teams": alt_teams_obj,
                "raw_titles": list(dict.fromkeys([t for t in (title, alt_title) if t])),
                "competition": comp,
                "_competition": comp,
                "country": country,
                "_country": country,
                "league": m.get("league", comp),
                "canonical_competition": slug,
                "_canonical_competition": slug,
                "tv_channels": m.get("tv_channels", []),
                "sources": [],
            }

            # Preserve gender/age strictly from official extraction or competition text
            is_w = bool(m.get("is_women"))
            is_y = bool(m.get("is_youth"))
            if not is_w or not is_y:
                comp_w, comp_y = extract_demographics(comp)
                is_w = is_w or comp_w
                is_y = is_y or comp_y
            if is_w:
                official_match["is_women"] = True
            if is_y:
                official_match["is_youth"] = True

            # Classify catalog and genre using official genre_classifier
            item_catalog, item_genre = genre_classifier.classify(official_match)
            official_match["_catalog"] = item_catalog
            official_match["genres"] = [item_genre]

            official_matches.append(official_match)

        return official_matches


diretta_service = DirettaService()
