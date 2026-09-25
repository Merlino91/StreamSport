import asyncio
import datetime
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx

logger = logging.getLogger("streamsport.espn")

# Complete mapping of ESPN sports & leagues to StreamSport catalogs and genres
ESPN_LEAGUE_MAP = [
    # --- SOCCER: CALCIO ITALIANO ---
    ("soccer", "ita.1", "calcio_italiano", "Serie A", "football"),
    ("soccer", "ita.2", "calcio_italiano", "Serie B", "football"),
    ("soccer", "ita.coppa_italia", "calcio_italiano", "Coppa Italia e Supercoppa", "football"),

    # --- SOCCER: CALCIO INTERNAZIONALE E COPPE ---
    ("soccer", "uefa.champions", "calcio_estero", "Champions League", "football"),
    ("soccer", "uefa.europa", "calcio_estero", "Europa e Conference League", "football"),
    ("soccer", "uefa.europa.conf", "calcio_estero", "Europa e Conference League", "football"),
    ("soccer", "eng.1", "calcio_estero", "Premier League", "football"),
    ("soccer", "esp.1", "calcio_estero", "La Liga", "football"),
    ("soccer", "ger.1", "calcio_estero", "Bundesliga e Ligue 1", "football"),
    ("soccer", "fra.1", "calcio_estero", "Bundesliga e Ligue 1", "football"),

    # Altri Campionati Europei e Coppe Nazionali
    ("soccer", "eng.2", "calcio_estero", "Altri Campionati Europei", "football"),
    ("soccer", "eng.fa", "calcio_estero", "Altri Campionati Europei", "football"),
    ("soccer", "eng.league_cup", "calcio_estero", "Altri Campionati Europei", "football"),
    ("soccer", "esp.2", "calcio_estero", "Altri Campionati Europei", "football"),
    ("soccer", "esp.copa_del_rey", "calcio_estero", "Altri Campionati Europei", "football"),
    ("soccer", "ger.2", "calcio_estero", "Altri Campionati Europei", "football"),
    ("soccer", "ger.dfb_pokal", "calcio_estero", "Altri Campionati Europei", "football"),
    ("soccer", "fra.2", "calcio_estero", "Altri Campionati Europei", "football"),
    ("soccer", "ned.1", "calcio_estero", "Altri Campionati Europei", "football"),
    ("soccer", "por.1", "calcio_estero", "Altri Campionati Europei", "football"),
    ("soccer", "sco.1", "calcio_estero", "Altri Campionati Europei", "football"),
    ("soccer", "bel.1", "calcio_estero", "Altri Campionati Europei", "football"),
    ("soccer", "tur.1", "calcio_estero", "Altri Campionati Europei", "football"),

    # Americhe e Leghe Extra-UE
    ("soccer", "usa.1", "calcio_estero", "Americhe e Leghe Extra-UE", "football"),
    ("soccer", "arg.1", "calcio_estero", "Americhe e Leghe Extra-UE", "football"),
    ("soccer", "bra.1", "calcio_estero", "Americhe e Leghe Extra-UE", "football"),
    ("soccer", "mex.1", "calcio_estero", "Americhe e Leghe Extra-UE", "football"),

    # Nazionali e Amichevoli
    ("soccer", "uefa.nations", "calcio_estero", "Nazionali e Amichevoli", "football"),
    ("soccer", "fifa.friendly", "calcio_estero", "Nazionali e Amichevoli", "football"),
    ("soccer", "fifa.world", "calcio_estero", "Nazionali e Amichevoli", "football"),
    ("soccer", "uefa.euro", "calcio_estero", "Nazionali e Amichevoli", "football"),

    # --- BASKET ---
    ("basketball", "nba", "basket", "NBA", "basketball"),
    ("basketball", "euroleague", "basket", "Eurolega ed Eurocup", "basketball"),
    ("basketball", "wnba", "basket", "WNBA e Femminile", "basketball"),
    ("basketball", "nbl", "basket", "Campionati Esteri ed NBL", "basketball"),
    ("basketball", "mens-college-basketball", "basket", "NCAA e College Basket", "basketball"),
    ("basketball", "womens-college-basketball", "basket", "WNBA e Femminile", "basketball"),
    ("basketball", "fiba", "basket", "FIBA e Tornei Nazionali", "basketball"),

    # --- MOTORI ---
    ("racing", "f1", "motori", "Formula 1", "motor-sports"),
    ("racing", "nascar-premier", "motori", "NASCAR e IndyCar", "motor-sports"),
    ("racing", "irl", "motori", "NASCAR e IndyCar", "motor-sports"),

    # --- FOOTBALL AMERICANO ---
    ("football", "nfl", "football_americano", "NFL", "american-football"),
    ("football", "college-football", "football_americano", "NCAA College Football", "american-football"),
    ("football", "cfl", "football_americano", "CFL e Altre Leghe", "american-football"),
    ("football", "ufl", "football_americano", "CFL e Altre Leghe", "american-football"),

    # --- BASEBALL ---
    ("baseball", "mlb", "baseball", "MLB", "baseball"),
    ("baseball", "college-baseball", "baseball", "NCAA College Baseball", "baseball"),

    # --- HOCKEY ---
    ("hockey", "nhl", "hockey", "NHL", "hockey"),
    ("hockey", "mens-college-hockey", "hockey", "KHL e Leghe Europee", "hockey"),

    # --- SPORT DA COMBATTIMENTO ---
    ("mma", "ufc", "combattimento", "UFC", "fight"),

    # --- ALTRI SPORT ---
    ("golf", "pga", "altri_sport", "Golf", "golf"),
    ("golf", "lpga", "altri_sport", "Golf", "golf"),
    ("golf", "liv", "altri_sport", "Golf", "golf"),
    ("australian-football", "afl", "altri_sport", "Rugby e AFL", "rugby"),
]


class ESPNService:
    """
    Official registry service (anagrafe ufficiale) powered by ESPN Scoreboard API.
    Provides canonical team names, home/away order, exact UTC kickoff times,
    and deterministic catalog & genre classification.
    """

    def __init__(self):
        self._cached_events: List[Dict[str, Any]] = []
        self._cache_time: float = 0.0
        self._cache_ttl: float = 3600  # 1 hour
        self._lock = asyncio.Lock()

    def parse_espn_event(
        self, ev: Dict[str, Any], catalog_id: str, genre: str, category: str
    ) -> Optional[Dict[str, Any]]:
        """Parses raw ESPN scoreboard event into standard StreamSport match dictionary."""
        competitions = ev.get("competitions") or []
        if not competitions:
            return None
        comp = competitions[0]
        competitors = comp.get("competitors") or []
        if len(competitors) < 2:
            return None

        home_comp = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away_comp = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        home_name = home_comp.get("team", {}).get("displayName") or home_comp.get("team", {}).get("name") or ""
        away_name = away_comp.get("team", {}).get("displayName") or away_comp.get("team", {}).get("name") or ""

        if not home_name or not away_name:
            return None

        # Format title strictly as European Home vs Away
        title = f"{home_name} vs {away_name}"

        # Parse ISO date string to ms
        date_str = ev.get("date")
        date_ms = 0
        if date_str:
            try:
                dt = datetime.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                date_ms = int(dt.timestamp() * 1000)
            except Exception:
                date_ms = 0

        eid = ev.get("id") or str(date_ms)
        return {
            "id": f"espn-{eid}",
            "title": title,
            "category": category,
            "date": date_ms,
            "teams": {
                "home": {"name": home_name},
                "away": {"name": away_name},
            },
            "_catalog": catalog_id,
            "_genre": genre,
            "_espn": True,
            "sources": [],
            "poster": "",
        }

    async def get_official_events(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Fetches official slates from ESPN across all mapped sports and leagues.
        Cached for 1 hour to respect server resources and guarantee fast startups.
        """
        async with self._lock:
            now = time.time()
            if not force and self._cached_events and (now - self._cache_time < self._cache_ttl):
                return self._cached_events

            logger.info("Fetching official sports events registry from ESPN Scoreboard API...")
            sem = asyncio.Semaphore(15)
            headers = {
                "User-Agent": "curl/8.4.0",
                "Accept": "*/*",
            }

            all_events: List[Dict[str, Any]] = []

            async with httpx.AsyncClient(timeout=8.0) as client:
                async def fetch_league(sport: str, league: str, cat_id: str, genre: str, category: str):
                    async with sem:
                        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
                        try:
                            res = await client.get(url, headers=headers)
                            if res.status_code == 200:
                                data = res.json()
                                ev_list = data.get("events") or []
                                parsed = []
                                for ev in ev_list:
                                    item = self.parse_espn_event(ev, cat_id, genre, category)
                                    if item:
                                        parsed.append(item)
                                return parsed
                        except Exception as e:
                            logger.debug("Failed fetching ESPN %s/%s: %s", sport, league, e)
                        return []

                tasks = [
                    fetch_league(sport, league, cat_id, genre, category)
                    for sport, league, cat_id, genre, category in ESPN_LEAGUE_MAP
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for res in results:
                    if isinstance(res, list):
                        all_events.extend(res)

            self._cached_events = all_events
            self._cache_time = now
            logger.info("ESPN official sports registry loaded: %d events indexed.", len(all_events))
            return self._cached_events

    def reconcile_matches(
        self,
        stream_matches: List[Dict[str, Any]],
        espn_events: List[Dict[str, Any]],
        clean_tokens_fn,
        get_teams_key_fn,
    ) -> None:
        """
        Reconciles stream matches (Streamed, DaddyLive) against the official ESPN registry.
        When a match coincides with an ESPN official event:
        - Adopts canonical ESPN title (Home vs Away)
        - Adopts official home and away team names
        - Adopts exact kickoff timestamp (date ms)
        - Adopts official catalog and genre
        """
        if not espn_events or not stream_matches:
            return

        for m in stream_matches:
            m_date = m.get("date") or 0
            m_tokens = clean_tokens_fn(m.get("title", ""))
            m_teams = get_teams_key_fn(m)
            m_words = set(m_tokens.split()) if m_tokens else set()

            for espn in espn_events:
                e_date = espn.get("date") or 0
                # 1. Date Compatibility: must coincide within 90 minutes
                if m_date and e_date:
                    if abs(m_date - e_date) > 90 * 60 * 1000:
                        continue

                # 2. Team match or token similarity
                e_teams = get_teams_key_fn(espn)
                e_tokens = clean_tokens_fn(espn.get("title", ""))
                e_words = set(e_tokens.split()) if e_tokens else set()

                is_same = False
                if m_teams and e_teams and m_teams == e_teams:
                    is_same = True
                elif m_words and e_words:
                    if m_words == e_words:
                        is_same = True
                    else:
                        common = m_words & e_words
                        union = m_words | e_words
                        sim = len(common) / len(union) if union else 0.0
                        is_subset = len(common) >= 2 and (m_words.issubset(e_words) or e_words.issubset(m_words))
                        if sim >= 0.55 or is_subset:
                            is_same = True

                if is_same:
                    # Adopt official ESPN metadata
                    m["title"] = espn["title"]
                    m["teams"] = espn["teams"]
                    if e_date:
                        m["date"] = e_date
                    m["_catalog"] = espn["_catalog"]
                    m["_genre"] = espn["_genre"]
                    m["_espn_matched"] = True
                    break


espn_service = ESPNService()
