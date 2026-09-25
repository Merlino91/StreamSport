import asyncio
import datetime
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import unicodedata
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
    ("soccer", "uefa.euroq", "calcio_estero", "Nazionali e Amichevoli", "football"),
    ("soccer", "caf.nations_qual", "calcio_estero", "Nazionali e Amichevoli", "football"),
    ("soccer", "caf.nations", "calcio_estero", "Nazionali e Amichevoli", "football"),

    # Nazionali Giovanili
    ("soccer", "uefa.euro_u21", "calcio_estero", "Europei Under 21 e Nazionali Giovanili", "football"),

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
    ("hockey", "womens-college-hockey", "hockey", "KHL e Leghe Europee", "hockey"),

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
            sem = asyncio.Semaphore(25)
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

                                # Query remaining scheduled dates in the next 7 days from the league calendar
                                cal = data.get("leagues", [{}])[0].get("calendar", [])
                                extra_dates = set()
                                now_dt = datetime.datetime.now(datetime.timezone.utc)

                                if cal and isinstance(cal[0], str):
                                    for c in cal:
                                        try:
                                            dt = datetime.datetime.fromisoformat(c.replace("Z", "+00:00"))
                                            if 0 <= (dt.date() - now_dt.date()).days <= 7:
                                                extra_dates.add(dt.strftime("%Y%m%d"))
                                        except Exception:
                                            pass
                                else:
                                    # Tournaments, cups or non-string calendars (e.g. UEFA, cups, playoffs):
                                    # Scan the 7 upcoming days
                                    for i in range(1, 8):
                                        extra_dates.add((now_dt + datetime.timedelta(days=i)).strftime("%Y%m%d"))

                                for ed in extra_dates:
                                    try:
                                        res2 = await client.get(f"{url}?dates={ed}", headers=headers)
                                        if res2.status_code == 200:
                                            for ev2 in res2.json().get("events", []):
                                                item2 = self.parse_espn_event(ev2, cat_id, genre, category)
                                                if item2:
                                                    parsed.append(item2)
                                    except Exception:
                                        pass

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

            # Deduplicate by event ID
            seen_ids = set()
            deduped = []
            for ev in all_events:
                eid = ev.get("id")
                if eid and eid not in seen_ids:
                    seen_ids.add(eid)
                    deduped.append(ev)

            self._cached_events = deduped
            self._cache_time = now
            logger.info("ESPN official sports registry loaded: %d events indexed across the week.", len(deduped))
            return self._cached_events

    ESPN_NORM_ALIASES = {
        r"\bturkey\b": "turkiye",
        r"\bitaly\b": "italia",
        r"\bunited states\b": "usa",
        r"\bczech republic\b": "czechia",
        r"\bbosnia and herzegovina\b": "bosnia",
        r"\bbosnia herzegovina\b": "bosnia",
        r"\bsouth korea\b": "korea",
        r"\bnorth macedonia\b": "macedonia",
        r"\binternazionale\b": "inter",
        r"\bparis saint germain\b": "psg",
        r"\bparis sg\b": "psg",
        r"\bmanchester city\b": "man city",
        r"\bmanchester united\b": "man utd",
        r"\bman united\b": "man utd",
        r"\bbayern munchen\b": "bayern",
        r"\bbayern munich\b": "bayern",
        r"\bborussia dortmund\b": "dortmund",
        r"\batletico madrid\b": "atletico",
        r"\bolympiakos\b": "olympiacos",
    }

    GENERIC_LOCATION_WORDS = {
        "new", "york", "los", "angeles", "san", "francisco", "diego", "antonio", "jose",
        "north", "south", "east", "west", "central", "state", "city", "de", "del", "la", "el"
    }

    @staticmethod
    def is_category_compatible(c1: str, c2: str) -> bool:
        c1 = (c1 or "").lower().strip()
        c2 = (c2 or "").lower().strip()
        if not c1 or not c2:
            return True
        if c1 == c2:
            return True
        if {c1, c2} == {"football", "soccer"}:
            return True
        return False

    def _extract_team_words(self, match: Dict[str, Any]) -> Tuple[set, set]:
        teams = match.get("teams")
        if isinstance(teams, dict) and teams.get("home") and teams.get("away"):
            h = (teams.get("home", {}).get("name") or "").lower()
            a = (teams.get("away", {}).get("name") or "").lower()
            h = unicodedata.normalize("NFKD", h).encode("ascii", "ignore").decode("utf-8")
            a = unicodedata.normalize("NFKD", a).encode("ascii", "ignore").decode("utf-8")
            for pat, rep in self.ESPN_NORM_ALIASES.items():
                h = re.sub(pat, rep, h)
                a = re.sub(pat, rep, a)
            h = re.sub(r"[^a-z0-9]+", " ", h).strip()
            a = re.sub(r"[^a-z0-9]+", " ", a).strip()
            h_words = set(w for w in h.split() if w not in ("fc", "ac", "cf", "sc", "as", "the") and len(w) > 1)
            a_words = set(w for w in a.split() if w not in ("fc", "ac", "cf", "sc", "as", "the") and len(w) > 1)
            return h_words, a_words
        return set(), set()

    def _clean_tokens_with_aliases(self, title: str, clean_tokens_fn) -> set:
        t = (title or "").lower()
        t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("utf-8")
        t = re.sub(r"[\U0001F1E6-\U0001F1FF]{2}", "", t)
        for pat, rep in self.ESPN_NORM_ALIASES.items():
            t = re.sub(pat, rep, t)
        cleaned = clean_tokens_fn(t)
        return set(cleaned.split()) if cleaned else set()

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
            m_cat = m.get("category") or ""
            m_words = self._clean_tokens_with_aliases(m.get("title", ""), clean_tokens_fn)
            mh, ma = self._extract_team_words(m)

            for espn in espn_events:
                # 0. Sport Category Compatibility: prevent cross-sport false positives (e.g. WNBA vs NFL)
                e_cat = espn.get("category") or ""
                if not self.is_category_compatible(m_cat, e_cat):
                    continue

                # 1. Date Compatibility: must coincide within 90 minutes
                e_date = espn.get("date") or 0
                if m_date and e_date:
                    if abs(m_date - e_date) > 90 * 60 * 1000:
                        continue

                # 2. Team match with distinctive words (direct and inverted for "at" matches)
                eh, ea = self._extract_team_words(espn)
                is_same = False
                if mh and ma and eh and ea:
                    h_match_d = bool((mh & eh) - self.GENERIC_LOCATION_WORDS)
                    a_match_d = bool((ma & ea) - self.GENERIC_LOCATION_WORDS)
                    if h_match_d and a_match_d:
                        is_same = True
                    else:
                        h_match_i = bool((mh & ea) - self.GENERIC_LOCATION_WORDS)
                        a_match_i = bool((ma & eh) - self.GENERIC_LOCATION_WORDS)
                        if h_match_i and a_match_i:
                            is_same = True

                # 3. Token similarity with distinctive words
                if not is_same:
                    e_words = self._clean_tokens_with_aliases(espn.get("title", ""), clean_tokens_fn)
                    if m_words and e_words:
                        if m_words == e_words:
                            is_same = True
                        else:
                            common = m_words & e_words
                            distinctive_common = common - self.GENERIC_LOCATION_WORDS
                            if len(distinctive_common) >= 2:
                                is_same = True
                            else:
                                union = m_words | e_words
                                sim = len(common) / len(union) if union else 0.0
                                is_subset = len(distinctive_common) >= 1 and (m_words.issubset(e_words) or e_words.issubset(m_words))
                                if sim >= 0.45 or is_subset:
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
