import re
from typing import Any, Dict, Optional, Tuple
from app.services.canonical_competition_service import (
    is_redzone_event,
    is_simulcast_event,
    get_simulcast_info,
)
from app.services.team_matching import extract_team_name

class GenreClassifier:
    """
    5-layer intelligent classifier that assigns a dedicated catalog and granular genre
    to any sports match/event.
    """

    # Women & Youth patterns
    _WOMEN_REGEX = re.compile(
        r"\b(women(?:[’']s)?|femminile|ladies|frauen|féminine|femenino|w-|wta|uwcl)\b",
        re.IGNORECASE,
    )
    _YOUTH_REGEX = re.compile(
        r"\b(u-?\d{1,2}|under\s*\d{1,2}|sub-?\d{1,2}|primavera|youth|giovanili|allievi|juniores|cadete|juvenil|college(?:\s*soccer)?|ncaa)\b",
        re.IGNORECASE,
    )

    # Foreign leagues & countries that might have homonym leagues (e.g. Brazil - Serie B, Russia - FNL)
    _FOREIGN_LEAGUE_REGEX = re.compile(
        r"\b(brazil|brasil|colombia|ecuador|uruguay|argentina|mexico|chile|peru|paraguay|venezuela|bolivia|costa\s*rica|saudi|egypt|morocco|australia|japan|korea|russia|russian|slovakia|czech|poland|austria|switzerland|ukraine|turkey|greece|denmark|sweden|norway|croatia|serbia|romania|bulgaria|hungary|ireland|scotland|belgium|netherlands|portugal|france|spain|germany|england)\b",
        re.IGNORECASE,
    )

    # Extra-European confederations and regional cups (CAF, AFC, CONCACAF, COSAFA, etc.)
    _EXTRA_EU_CONFED_REGEX = re.compile(
        r"\b(caf|cosafa|afc\s*(?:champions|cup|asian|u-?\d+|qualif)|concacaf|ofc|cecafa|unaf|wafu|conmebol|asean)\b",
        re.IGNORECASE,
    )

    # Extra-European leagues & tournaments (Americas, Asia, Africa, Oceania)
    _EXTRA_EU_LEAGUES_REGEX = re.compile(
        r"\b("
        r"mls|major\s*league\s*soccer|usl|nwsl|cpl|canadian\s*premier|"
        r"liga\s*mx|mexico|mexican|asenso\s*mx|"
        r"libertadores|sudamericana|recopa|copa\s*libertadores|"
        r"brazil|brasil|brasile|brasileir[ao]|serie\s*a\s*brazil|copa\s*do\s*brasil|paulista|carioca|"
        r"argentina|primera\s*division\s*argentina|copa\s*de\s*la\s*liga|"
        r"colombia|chile|peru|perù|uruguay|ecuador|paraguay|venezuela|bolivia|costa\s*rica|"
        r"betplay|dimayor|torneo\s*betplay|liga\s*betplay|"
        r"saudi|roshn|saudi\s*pro\s*league|king\s*cup\s*champions|"
        r"qatar|stars\s*league|uae|pro\s*league\s*uae|"
        r"j-?league|j[123]?\s*league|k-?league(?:\s*[1234])?|csl|chinese\s*super\s*league|"
        r"a-?league|australia|indian\s*super\s*league|isl|"
        r"egypt|morocco|botola|south\s*africa|psl"
        r")\b",
        re.IGNORECASE,
    )

    # European recognized countries (Italian and English variants)
    _EUROPEAN_COUNTRIES = {
        "portogallo", "portugal", "olanda", "netherlands", "paesi bassi", "belgio", "belgium",
        "scozia", "scotland", "turchia", "turkey", "grecia", "greece", "austria", "svizzera", "switzerland",
        "danimarca", "denmark", "svezia", "sweden", "norvegia", "norway", "polonia", "poland",
        "repubblica ceca", "czech republic", "czech", "cechia", "croazia", "croatia", "serbia",
        "romania", "bulgaria", "ungheria", "hungary", "ucraina", "ukraine", "irlanda", "ireland",
        "irlanda del nord", "northern ireland", "galles", "wales", "finlandia", "finland",
        "cipro", "cyprus", "san marino", "malta", "islanda", "iceland", "slovenia", "slovacchia", "slovakia",
        "bosnia", "bosnia ed erzegovina", "bosnia and herzegovina", "albania", "macedonia", "north macedonia",
        "macedonia del nord", "montenegro", "kosovo", "lussemburgo", "luxembourg", "estonia", "lettonia", "latvia",
        "lituania", "lithuania", "bielorussia", "belarus", "moldavia", "moldova", "georgia", "armenia",
        "azerbaigian", "azerbaijan", "kazakistan", "kazakhstan", "isole far oer", "faroe islands", "faroe",
        "gibilterra", "gibraltar", "andorra", "liechtenstein"
    }

    # European emoji flags
    _EUROPEAN_FLAGS = (
        "🇵🇹", "🇳🇱", "🇧🇪", "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "🇹🇷", "🇬🇷", "🇦🇹", "🇨🇭", "🇩🇰", "🇸🇪",
        "🇳🇴", "🇵🇱", "🇨🇿", "🇭🇷", "🇷🇸", "🇷🇴", "🇧🇬", "🇭🇺", "🇺🇦", "🇮🇪",
        "🇫🇮", "🇨🇾", "🇸🇲", "🇲🇹", "🇮🇸", "🇸🇮", "🇸🇰", "🇧🇦", "🇦🇱", "🇲🇰",
        "🇲🇪", "🇽🇰", "🇱🇺", "🇪🇪", "🇱🇻", "🇱🇹", "🇧🇾", "🇲🇩", "🇬🇪", "🇦🇲",
        "🇦🇿", "🇰🇿", "🇫🇴", "🇬🇮", "🇦🇩", "🇱🇮"
    )

    # European leagues outside top 5 (e.g. EFL, Portugal, Netherlands, Turkey, Greece, Denmark, etc.)
    _EUROPEAN_LEAGUES_REGEX = re.compile(
        r"\b("
        r"england|efl|championship|league\s*one|league\s*two|fa\s*cup|carabao|trophy|"
        r"portugal|primeira\s*liga|taca\s*de\s*portugal|eredivisie|netherlands|knvb|"
        r"scotland|premiership|belgium|jupiler|pro\s*league|turkey|s[üu]per\s*lig|"
        r"greece|super\s*league\s*greece|denmark|superliga|1\.\s*division|"
        r"sweden|allsvenskan|superettan|norway|eliteserien|austria|bundesliga\s*austria|oefb|"
        r"switzerland|super\s*league\s*switzerland|poland|ekstraklasa|czech|chance\s*liga|fortuna\s*liga|"
        r"croatia|hnl|serbia|prva\s*liga|superliga|romania|liga\s*i|superliga\s*romania|"
        r"bulgaria|parva\s*liga|hungary|otp\s*bank|nb\s*i|ukraine|premier\s*liha|"
        r"ireland|irish|league\s*of\s*ireland|finland|veikkausliiga|finnish|"
        r"cyprus|cypriot|cipro|san\s*marino|campionato\s*sammarinese|titano|malta|maltese|"
        r"islanda|iceland|besta\s*deild|slovenia|prva\s*liga|slovakia|nike\s*liga|albania|bosnia|"
        r"armenia|azerbaijan|georgia|kazakhstan|faroe|gibraltar|andorra|luxembourg"
        r")\b",
        re.IGNORECASE,
    )

    # Friendly / Exhibition matches
    _FRIENDLY_REGEX = re.compile(
        r"\b(friendly|club\s*friendly|amichevole|trofeo)\b",
        re.IGNORECASE,
    )




    # NFL Full Franchise Names
    NFL_FULL_TEAMS = {
        "arizona cardinals", "atlanta falcons", "baltimore ravens", "buffalo bills",
        "carolina panthers", "chicago bears", "cincinnati bengals", "cleveland browns",
        "dallas cowboys", "denver broncos", "detroit lions", "green bay packers",
        "houston texans", "indianapolis colts", "jacksonville jaguars", "kansas city chiefs",
        "las vegas raiders", "oakland raiders", "los angeles chargers", "la chargers",
        "san diego chargers", "los angeles rams", "la rams", "st. louis rams", "miami dolphins",
        "minnesota vikings", "new england patriots", "new orleans saints", "new york giants",
        "ny giants", "new york jets", "ny jets", "philadelphia eagles", "pittsburgh steelers",
        "san francisco 49ers", "sf 49ers", "seattle seahawks", "tampa bay buccaneers",
        "tennessee titans", "washington commanders"
    }

    # Nicknames exclusive to NFL teams (never or extraordinarily rarely shared with NCAA colleges)
    NFL_EXCLUSIVE_NICKNAMES = {
        "bengals", "steelers", "seahawks", "commanders", "chiefs", "dolphins",
        "titans", "packers", "buccaneers", "vikings", "patriots", "49ers"
    }

    # NFL Franchises
    NFL_TEAMS = {
        "cardinals", "falcons", "ravens", "bills", "panthers", "bears", "bengals",
        "browns", "cowboys", "broncos", "lions", "packers", "texans", "colts",
        "jaguars", "chiefs", "raiders", "chargers", "rams", "dolphins", "vikings",
        "patriots", "saints", "giants", "jets", "eagles", "steelers", "49ers",
        "seahawks", "buccaneers", "titans", "commanders"
    }

    # NCAA College Football Nicknames & Keywords
    NCAA_KEYWORDS = {
        "ncaa", "cfb", "college football",
        "buckeyes", "wolverines", "crimson tide", "longhorns", "volunteers", "ducks",
        "trojans", "nittany lions", "gators", "seminoles", "fighting irish", "sooners",
        "cornhuskers", "hawkeyes", "badgers", "huskies", "utes", "rebels", "aggies",
        "hokies", "demon deacons", "black knights", "midshipmen", "horned frogs",
        "terrapins", "buffaloes", "hoosiers", "boilermakers", "cyclones", "wolfpack",
        "mountaineers", "mustangs", "green wave", "blue devils", "tar heels",
        "cavaliers", "hurricanes", "scarlet knights", "commodores", "razorbacks",
        "gamecocks", "yellow jackets", "bulldogs", "fighting illini", "golden gophers",
        "bearcats", "wildcats", "spartans", "chippewas", "bobcats", "cougars",
        "roadrunners", "aztecs", "bearkats", "lobos", "red raiders", "rainbow warriors",
        "owls", "chanticleers"
    }

    # NCAA Major College Football Programs (FBS/FCS)
    NCAA_COLLEGES = {
        "alabama", "clemson", "ohio state", "michigan", "georgia", "texas", "notre dame",
        "penn state", "oklahoma", "florida", "florida state", "lsu", "usc", "oregon",
        "tennessee", "auburn", "wisconsin", "nebraska", "iowa", "washington", "colorado",
        "utah", "ole miss", "texas a&m", "arkansas", "kentucky", "south carolina",
        "mississippi state", "missouri", "vanderbilt", "indiana", "illinois", "purdue",
        "minnesota", "northwestern", "michigan state", "maryland", "rutgers", "ucla",
        "california", "stanford", "arizona", "arizona state", "oregon state",
        "washington state", "kansas", "kansas state", "baylor", "tcu", "texas tech",
        "west virginia", "iowa state", "cincinnati", "houston", "byu", "ucf", "smu",
        "virginia", "virginia tech", "north carolina", "nc state", "duke", "wake forest",
        "louisville", "pittsburgh", "syracuse", "boston college", "miami", "georgia tech",
        "boise state", "san diego state", "fresno state", "unlv", "colorado state",
        "wyoming", "air force", "army", "navy", "app state", "coastal carolina",
        "james madison", "liberty", "tulane", "memphis", "usf", "temple", "rice",
        "north texas", "utsa", "uab", "tulsa", "charlotte", "east carolina", "bowling green",
        "toledo", "ohio", "miami (oh)", "buffalo", "akron", "kent state", "ball state",
        "western michigan", "central michigan", "eastern michigan", "northern illinois",
        "marshall", "georgia southern", "georgia state", "troy", "south alabama",
        "southern miss", "louisiana", "ulm", "arkansas state", "texas state", "old dominion",
        "louisiana tech", "western kentucky", "fiu", "sam houston", "kennesaw state",
        "delaware", "howard", "tuskegee", "benedict", "central arkansas", "incarnate word"
    }


    # AFL / AFLW Teams (Australian Football League)
    AFL_TEAMS = {
        "adelaide crows", "crows", "brisbane lions", "carlton", "carlton blues", "blues",
        "collingwood", "collingwood magpies", "magpies", "essendon", "essendon bombers", "bombers",
        "fremantle", "fremantle dockers", "dockers", "geelong cats", "geelong", "cats",
        "gold coast suns", "gold coast", "gws giants", "greater western sydney", "giants",
        "hawthorn", "hawthorn hawks", "hawks", "melbourne demons", "demons",
        "north melbourne", "north melbourne kangaroos", "kangaroos", "port adelaide", "power",
        "richmond tigers", "richmond", "tigers", "st kilda", "saints", "sydney swans", "swans",
        "west coast eagles", "eagles", "western bulldogs", "bulldogs"
    }

    @staticmethod
    def _normalize_name(name: str) -> str:
        s = name.lower()
        s = re.sub(r'^(?:a\.s\.|as|a\.c\.|ac|ssc|s\.s\.c\.|ss|u\.s\.|us|u\.c\.|uc|f\.c\.|fc)\s+', '', s)
        s = re.sub(r'\b(cfc|fc|bc|sc|afc)\b', '', s)
        return s.strip()

    def classify(self, match: Dict[str, Any]) -> Tuple[str, str]:
        """
        Classifies a match dictionary into (catalog_id, genre).
        """
        title = (match.get("title") or "").strip()
        title_lower = title.lower()
        comp = (match.get("competition") or match.get("_competition") or "").lower().strip()
        country = (match.get("country") or match.get("_country") or "").lower().strip()
        raw_title_lower = (match.get("raw_title") or "").strip().lower()
        eval_text = f"{title_lower} {country} {comp} {raw_title_lower}".strip()
        is_women = bool(self._WOMEN_REGEX.search(eval_text)) or match.get("_gender") == "+"
        is_youth = bool(self._YOUTH_REGEX.search(eval_text)) or match.get("_age") == "+"

        # 0. Early Simulcast & Multi-match show routing (NFL RedZone, Golazo, Diretta Gol, etc.)
        sim_info = get_simulcast_info(title_lower, match.get("category") or match.get("_silo")) or get_simulcast_info(str(match.get("id", "")))
        if sim_info:
            return sim_info["catalog"], sim_info["genre"]

        # 1. Early Canonical Competition routing
        canon = match.get("canonical_competition")
        if canon:
            # Silo boundary guard: a canonical slug from another sport must NEVER hijack an event from a dedicated sport silo
            from app.services.canonical_competition_service import get_canonical_silo
            canon_silo = get_canonical_silo(canon)
            event_silo = (match.get("_silo") or match.get("category") or "").lower().strip()
            if event_silo in ("soccer",): event_silo = "football"
            if event_silo in ("basket",): event_silo = "basketball"
            if event_silo in ("motorsport", "motori"): event_silo = "motor-sports"
            if event_silo in ("volley",): event_silo = "volleyball"
            if event_silo in ("combattimento", "boxe", "mma"): event_silo = "fight"
            DEDICATED_SILOS = {"hockey", "basketball", "tennis", "motor-sports", "volleyball", "baseball", "fight", "american-football"}
            if event_silo in DEDICATED_SILOS and canon_silo and canon_silo != event_silo:
                canon = None

        if canon:
            # Women & Youth safety guards on canonical routing:
            # A men's senior canonical competition must NEVER hijack a women or youth event!
            if is_women:
                if canon in ("football_serie_a", "football_serie_b", "football_serie_c", "football_serie_a_women"):
                    return "calcio_italiano", "Calcio Femminile"
            if is_youth:
                if canon in ("football_serie_a", "football_serie_b", "football_serie_c", "football_primavera"):
                    return "calcio_italiano", "Primavera e Giovanili"

            # Country-specific guard for homonym league names
            # If the event explicitly originates from a non-domestic country, invalidate canonical override
            country_clean = (match.get("country") or "").strip().lower()
            if canon in ("football_serie_a", "football_serie_b", "football_serie_c"):
                if country_clean and country_clean not in ("italia", "italy"):
                    canon = None
            elif canon == "football_la_liga":
                if country_clean and country_clean not in ("spagna", "spain"):
                    canon = None
            elif canon == "football_premier_league":
                if country_clean and country_clean not in ("inghilterra", "england", "uk", "regno unito"):
                    canon = None

            canon_map = {
                "football_serie_a": ("calcio_italiano", "Serie A"),
                "football_serie_a_women": ("calcio_italiano", "Calcio Femminile"),
                "football_serie_b": ("calcio_italiano", "Serie B"),
                "football_serie_c": ("calcio_italiano", "Serie C"),
                "football_coppa_italia": ("calcio_italiano", "Coppa Italia e Supercoppa"),
                "football_primavera": ("calcio_italiano", "Primavera e Giovanili"),
                "football_premier_league": ("calcio_estero", "Premier League"),
                "football_championship": ("calcio_estero", "Altri Campionati Europei"),
                "football_la_liga": ("calcio_estero", "La Liga"),
                "football_segunda": ("calcio_estero", "Altri Campionati Europei"),
                "football_bundesliga": ("calcio_estero", "Bundesliga e Ligue 1"),
                "football_austrian_bundesliga": ("calcio_estero", "Altri Campionati Europei"),
                "football_danish_superliga": ("calcio_estero", "Altri Campionati Europei"),
                "football_swiss_super_league": ("calcio_estero", "Altri Campionati Europei"),
                "football_2_bundesliga": ("calcio_estero", "Altri Campionati Europei"),
                "football_ligue_1": ("calcio_estero", "Bundesliga e Ligue 1"),
                "football_uefa_youth_league": ("calcio_estero", "Europei Under 21 e Nazionali Giovanili"),
                "football_uwcl": ("calcio_estero", "Champions League"),
                "football_uefa_cl": ("calcio_estero", "Champions League"),
                "football_uefa_el": ("calcio_estero", "Europa e Conference League"),
                "football_uefa_ecl": ("calcio_estero", "Europa e Conference League"),
                "football_uefa_super_cup": ("calcio_estero", "Champions League"),
                "football_uefa_nations": ("calcio_estero", "Nazionali e Amichevoli"),
                "football_eredivisie": ("calcio_estero", "Altri Campionati Europei"),
                "football_primeira_liga": ("calcio_estero", "Altri Campionati Europei"),
                "football_scottish_prem": ("calcio_estero", "Altri Campionati Europei"),
                "football_super_lig": ("calcio_estero", "Altri Campionati Europei"),
                "football_belgian_pro": ("calcio_estero", "Altri Campionati Europei"),
                "football_ligue_2": ("calcio_estero", "Altri Campionati Europei"),
                "football_coupe_de_france": ("calcio_estero", "Altri Campionati Europei"),
                "football_efl_league_one": ("calcio_estero", "Altri Campionati Europei"),
                "football_efl_league_two": ("calcio_estero", "Altri Campionati Europei"),
                "football_copa_libertadores": ("calcio_estero", "Americhe e Leghe Extra-UE"),
                "football_copa_sudamericana": ("calcio_estero", "Americhe e Leghe Extra-UE"),
                "football_mls": ("calcio_estero", "Americhe e Leghe Extra-UE"),
                "football_liga_mx": ("calcio_estero", "Americhe e Leghe Extra-UE"),
                "football_brasileirao": ("calcio_estero", "Americhe e Leghe Extra-UE"),
                "football_brazil_serie_b": ("calcio_estero", "Americhe e Leghe Extra-UE"),
                "football_brazil_serie_c": ("calcio_estero", "Americhe e Leghe Extra-UE"),
                "football_ecuador_serie_a": ("calcio_estero", "Americhe e Leghe Extra-UE"),
                "football_ecuador_serie_b": ("calcio_estero", "Americhe e Leghe Extra-UE"),
                "football_liga_profesional_arg": ("calcio_estero", "Americhe e Leghe Extra-UE"),
                "football_saudi_pro": ("calcio_estero", "Americhe e Leghe Extra-UE"),
                "football_afc_cl": ("calcio_estero", "Americhe e Leghe Extra-UE"),
                "football_world_cup": ("calcio_estero", "Nazionali e Amichevoli"),
                "football_world_cup_qual": ("calcio_estero", "Nazionali e Amichevoli"),
                "football_euro": ("calcio_estero", "Nazionali e Amichevoli"),
                "football_euro_qual": ("calcio_estero", "Nazionali e Amichevoli"),
                "football_copa_america": ("calcio_estero", "Nazionali e Amichevoli"),
                "football_afcon": ("calcio_estero", "Nazionali e Amichevoli"),
                "football_gold_cup": ("calcio_estero", "Nazionali e Amichevoli"),
                "football_club_world_cup": ("calcio_estero", "Nazionali e Amichevoli"),
                "football_international_friendly": ("calcio_estero", "Nazionali e Amichevoli"),
                "basketball_nba": ("basket", "NBA"),
                "basketball_wnba": ("basket", "WNBA e Femminile"),
                "basketball_euroleague": ("basket", "Eurolega ed Eurocup"),
                "basketball_eurocup": ("basket", "Eurolega ed Eurocup"),
                "basketball_bcl": ("basket", "Eurolega ed Eurocup"),
                "basketball_lba_serie_a": ("basket", "LBA Serie A"),
                "basketball_acb": ("basket", "Campionati Esteri ed NBL"),
                "basketball_cba": ("basket", "Campionati Esteri ed NBL"),
                "basketball_nbl": ("basket", "Campionati Esteri ed NBL"),
                "basketball_bbl": ("basket", "Campionati Esteri ed NBL"),
                "basketball_lnb": ("basket", "Campionati Esteri ed NBL"),
                "basketball_greece": ("basket", "Campionati Esteri ed NBL"),
                "basketball_turkey": ("basket", "Campionati Esteri ed NBL"),
                "basketball_aba": ("basket", "Campionati Esteri ed NBL"),
                "basketball_fiba": ("basket", "FIBA e Tornei Nazionali"),
                "basketball_ncaa": ("basket", "NCAA e College Basket"),
                "tennis_grand_slam": ("tennis", "Grandi Slam"),
                "tennis_atp": ("tennis", "ATP"),
                "tennis_wta": ("tennis", "WTA"),
                "tennis_davis_bjk": ("tennis", "Coppa Davis e BJK Cup"),
                "tennis_challenger": ("tennis", "Challenger e Altri"),
                "motorsport_f1": ("motori", "Formula 1"),
                "motorsport_motogp": ("motori", "MotoGP e Superbike"),
                "motorsport_sbk": ("motori", "MotoGP e Superbike"),
                "motorsport_nascar": ("motori", "NASCAR e IndyCar"),
                "motorsport_indycar": ("motori", "NASCAR e IndyCar"),
                "motorsport_rally_wrc": ("motori", "Rally e WRC"),
                "am_football_nfl": ("football_americano", "NFL"),
                "am_football_ncaa": ("football_americano", "NCAA College Football"),
                "baseball_mlb": ("baseball", "MLB"),
                "baseball_ncaa": ("baseball", "NCAA College Baseball"),
                "hockey_chl": ("hockey", "KHL e Leghe Europee"),
                "hockey_nhl": ("hockey", "NHL"),
                "hockey_khl": ("hockey", "KHL e Leghe Europee"),
                "hockey_shl": ("hockey", "KHL e Leghe Europee"),
                "hockey_extraliga": ("hockey", "KHL e Leghe Europee"),
                "hockey_liiga": ("hockey", "KHL e Leghe Europee"),
                "hockey_del": ("hockey", "KHL e Leghe Europee"),
                "hockey_euro": ("hockey", "KHL e Leghe Europee"),
                "fight_ufc": ("combattimento", "UFC"),
                "fight_boxing": ("combattimento", "Boxe"),
                "fight_wwe": ("combattimento", "Wrestling e WWE"),
                "fight_mma": ("combattimento", "MMA"),
                "volleyball_superlega": ("volley", "SuperLega Maschile"),
                "volleyball_serie_a1_femm": ("volley", "Serie A1 Femminile"),
                "rugby_six_nations": ("altri_sport", "Rugby e AFL"),
                "rugby_top_14": ("altri_sport", "Rugby e AFL"),
                "afl_footy": ("altri_sport", "Rugby e AFL"),
                "golf_pga": ("altri_sport", "Golf"),
                "golf_liv": ("altri_sport", "Golf"),
                "darts_pdc": ("altri_sport", "Freccette / Darts"),
                "cycling_grand_tours": ("altri_sport", "Ciclismo"),
                "handball_ehf_cl": ("altri_sport", "Pallamano"),
            }
            if canon in canon_map:
                return canon_map[canon]

        cat = (match.get("category") or "").lower().strip()
        silo = (match.get("_silo") or "").lower().strip()
        comp = (match.get("competition") or match.get("_competition") or "").lower().strip()
        country = (match.get("country") or match.get("_country") or "").lower().strip()
        eval_text = f"{title_lower} {country} {comp}".strip()

        teams = match.get("teams") or {}
        home = ""
        away = ""
        if isinstance(teams, dict) and teams.get("home") and teams.get("away"):
            home = self._normalize_name(extract_team_name(teams.get("home")))
            away = self._normalize_name(extract_team_name(teams.get("away")))
        elif " vs " in title_lower:
            parts = title_lower.split(" vs ", 1)
            home = self._normalize_name(re.sub(r"[\(-].*$", "", parts[0]).strip())
            away = self._normalize_name(re.sub(r"[\(-].*$", "", parts[1]).strip())
        elif " - " in title_lower:
            parts = title_lower.split(" - ", 1)
            home = self._normalize_name(re.sub(r"[\(-].*$", "", parts[0]).strip())
            away = self._normalize_name(re.sub(r"[\(-].*$", "", parts[1]).strip())

        is_women = bool(self._WOMEN_REGEX.search(eval_text)) or match.get("_gender") == "+"
        is_youth = bool(self._YOUTH_REGEX.search(eval_text)) or match.get("_age") == "+"

        # Guard: Cue sports (Snooker, Q Tour, Billiards) often mislabeled upstream
        if re.search(r"\b(snooker|q\s*tour|billiards|biliardo)\b", title_lower):
            return "altri_sport", "Biliardo, Padel e Altri"

        # Guard: Handball / Pallamano mislabeled upstream as soccer/football
        if re.search(r"\b(handball|pallamano|ehf)\b", title_lower) or "ehf" in comp or "🤾" in title_lower:
            return "altri_sport", "Pallamano"

        # ------------------------------------------------------------------
        # 1. TENNIS (Dedicated Catalog: 'tennis')
        # ------------------------------------------------------------------
        is_football_context = (
            cat in ("football", "soccer")
            or silo == "football"
            or bool(re.search(r"\b(afc\s*wimbledon|wimbledon\s*fc)\b", title_lower))
        )
        is_other_sport_context = (
            cat in ("badminton", "table-tennis", "padel", "squash", "cricket", "rugby", "golf", "handball", "cycling", "darts", "billiards", "snooker", "waterpolo")
            or silo in ("badminton", "table-tennis", "padel", "squash", "cricket", "rugby", "golf", "handball", "cycling", "darts", "billiards", "snooker", "waterpolo")
        )
        eval_tennis = f"{title_lower} {comp}".strip()
        if not is_football_context and not is_other_sport_context and (
            cat == "tennis"
            or silo == "tennis"
            or "tennis" in eval_tennis
            or bool(re.search(r"\b(atp|wta|bjk|davis\s*cup|wimbledon|us\s*open|roland\s*garros|australian\s*open|french\s*open)\b", eval_tennis))
        ):
            if re.search(r"\b(wimbledon|us\s*open|roland\s*garros|australian\s*open|french\s*open|slam)\b", eval_tennis):
                return "tennis", "Grandi Slam"
            if re.search(r"\b(davis|bjk|billie\s*jean|laver\s*cup|united\s*cup|hopman\s*cup)\b", eval_tennis):
                return "tennis", "Coppa Davis e BJK Cup"
            if "atp" in eval_tennis and "wta" in eval_tennis:
                return "tennis", "Challenger e Altri"
            if is_women or "wta" in eval_tennis or re.search(r"\b(women|femminile|femenino)\b", eval_tennis):
                return "tennis", "WTA"
            if "atp" in eval_tennis:
                return "tennis", "ATP"
            if re.search(r"\b(challenger|itf|exhibition|esibizione|uts|ultimate\s*tennis)\b", eval_tennis):
                return "tennis", "Challenger e Altri"
            return "tennis", "ATP"

        # ------------------------------------------------------------------
        # 2. MOTORI (Dedicated Catalog: 'motori')
        # ------------------------------------------------------------------
        eval_motor = f"{title_lower} {comp}".strip()
        if (
            cat in ("motor-sports", "motorsports", "motorsport")
            or silo == "motor-sports"
            or bool(re.search(r"\b(formula\s*1|formula\s*one|f1|motogp|moto\s*gp|moto2|moto3|superbike|wsbk|nascar|indycar|rally|wrc|dakar)\b", eval_motor))
        ):
            if re.search(r"\b(f1|formula\s*1|formula\s*one)\b", eval_motor):
                return "motori", "Formula 1"
            if re.search(r"\b(motogp|moto\s*gp|moto2|moto3|superbike|wsbk|worldsbk|civ)\b", eval_motor):
                return "motori", "MotoGP e Superbike"
            if re.search(r"\b(rally|wrc|dakar|erc)\b", eval_motor):
                return "motori", "Rally e WRC"
            if re.search(r"\b(nascar|indycar|indy\s*500|supercars|dtm|le\s*mans|wec|imsa|gt\s*world|bathurst)\b", eval_motor):
                return "motori", "NASCAR e IndyCar"
            # Default fallback for grassroots/unclassified motor racing (dirt tracks, local ovals)
            return "motori", "NASCAR e IndyCar"



        # ------------------------------------------------------------------
        # 3. BASKET (Dedicated Catalog: 'basket')
        # ------------------------------------------------------------------
        if (
            cat in ("basketball", "basket")
            or silo == "basketball"
            or "basketball" in eval_text
            or re.search(r"\b(nba|wnba|euroleague|eurocup|lba|nbl|fiba|cba)\b", eval_text)
        ) and not is_football_context:
            # --------------------------------------------------------------
            # LIVELLO 1: Tornei Internazionali, Coppe Europee e Femminile
            # --------------------------------------------------------------

            # 1. WNBA & Basket Femminile
            if is_women or re.search(r"\b(wnba|liga\s*femenina|tauihi|wlb|women|femminile|femenino|wbbl|wnbl)\b", eval_text):
                return "basket", "WNBA e Femminile"

            # 2. FIBA, 3x3 e Tornei Nazionali
            if re.search(r"\b(fiba|3x3|world\s*tour|world\s*cup|mondiali|eurobasket|qualif|national\s*team|intercontinental\s*cup|afrobasket|americup|asia\s*cup)\b", eval_text):
                return "basket", "FIBA e Tornei Nazionali"

            # 3. Eurolega & Eurocup (Coppe Continentali per Club)
            if re.search(r"\b(euroleague|eurocup|eurolega|bcl|basketball\s*champions|fiba\s*europe\s*cup)\b", eval_text):
                return "basket", "Eurolega ed Eurocup"

            # 4. NCAA & College Basket
            if re.search(r"\b(ncaa|college\s*basketball|march\s*madness)\b", eval_text):
                return "basket", "NCAA e College Basket"

            # --------------------------------------------------------------
            # LIVELLO 2: Campionati Nazionali (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
            # --------------------------------------------------------------

            # Segnali di campionati esteri noti (Country o Sigle competizioni)
            has_foreign_flag = any(flag in eval_text for flag in ("🇨🇳", "🇪🇸", "🇦🇺", "🇩🇪", "🇫🇷", "🇬🇷", "🇹🇷", "🇧🇷", "🇯🇵", "🇰🇷"))
            is_foreign_country = country in (
                "china", "cina", "cn", "spain", "spagna", "es", "australia", "germany", "germania", "de",
                "france", "francia", "fr", "greece", "grecia", "gr", "turkey", "turchia", "tr", "russia", "ru",
                "brazil", "brasile", "br", "japan", "giappone", "jp", "korea", "corea", "kr", "philippines", "filippine",
                "argentina", "ar", "israel", "israele", "il", "poland", "polonia", "pl", "lithuania", "lituania", "lt",
                "serbia", "rs", "croatia", "croazia", "hr"
            ) or has_foreign_flag
            is_foreign_league = bool(re.search(
                r"\b(cba|chinese\s*basketball|chinese\s*cba|china\s*cba|acb|endesa|primera\s*feb|nbl|australian\s*nbl|bbl|lnb|pro\s*a|betclic|gbl|basket\s*league|bsl|super\s*ligi|aba\s*league|vtb|nbb|pba|kbl|b\s*league|korisliiga|plk|lkl)\b",
                eval_text
            ))

            # A) Se ha segnali espliciti di lega/nazione estera, instrada subito a Campionati Esteri
            if (is_foreign_country and country not in ("usa", "united states", "italia", "italy")) or is_foreign_league:
                return "basket", "Campionati Esteri ed NBL"

            # B) LBA Serie A (Italia: 1° Nazione -> 2° Torneo)
            is_italy = (
                country in ("italy", "italia")
                or "🇮🇹" in title
                or bool(re.search(r"\b(italy|italia|italian)\b", eval_text))
            )
            is_lba_comp = bool(re.search(r"\b(lba|serie\s*a\s*basket|lega\s*basket|serie\s*a2|coppa\s*italia\s*basket|supercoppa\s*basket)\b", eval_text))
            if is_lba_comp or (is_italy and not is_foreign_league):
                return "basket", "LBA Serie A"

            # C) NBA (USA: 1° Nazione/Sigla -> 2° Franchigie con prefisso cittadino o moniker univoci)
            is_usa = country in ("usa", "united states", "stati uniti", "us") or "🇺🇸" in eval_text
            has_nba_token = bool(re.search(r"\b(nba|nba-preseason|summer\s*league)\b", eval_text))

            # Franchigie NBA con vincolo geografico (città/stato) o moniker univoci NBA (nessuna parola generica isolata come 'rockets' o 'bulls')
            NBA_FRANCHISE_REGEX = (
                r"\b("
                r"boston\s*celtics|celtics|"
                r"brooklyn\s*nets|ny\s*knicks|new\s*york\s*knicks|knicks|"
                r"philadelphia\s*76ers|76ers|sixers|"
                r"toronto\s*raptors|"
                r"chicago\s*bulls|"
                r"cleveland\s*cavaliers|cavaliers|cavs|"
                r"detroit\s*pistons|"
                r"indiana\s*pacers|"
                r"milwaukee\s*bucks|"
                r"atlanta\s*hawks|"
                r"charlotte\s*hornets|"
                r"miami\s*heat|"
                r"orlando\s*magic|"
                r"washington\s*wizards|"
                r"denver\s*nuggets|"
                r"minnesota\s*timberwolves|timberwolves|twolves|"
                r"oklahoma\s*city\s*thunder|okc\s*thunder|"
                r"portland\s*trail\s*blazers|trail\s*blazers|blazers|"
                r"utah\s*jazz|"
                r"golden\s*state\s*warriors|golden\s*state|"
                r"la\s*clippers|los\s*angeles\s*clippers|"
                r"la\s*lakers|los\s*angeles\s*lakers|lakers|"
                r"phoenix\s*suns|"
                r"sacramento\s*kings|"
                r"dallas\s*mavericks|mavericks|mavs|"
                r"houston\s*rockets|"
                r"memphis\s*grizzlies|"
                r"new\s*orleans\s*pelicans|"
                r"san\s*antonio\s*spurs"
                r")\b"
            )

            if has_nba_token or (not is_foreign_country and re.search(NBA_FRANCHISE_REGEX, title_lower)):
                return "basket", "NBA"

            # D) Fallback per qualsiasi altro campionato nazionale / estero
            return "basket", "Campionati Esteri ed NBL"

        # ------------------------------------------------------------------
        # 4. VOLLEY (Dedicated Catalog: 'volley')
        # ------------------------------------------------------------------
        eval_volley = f"{title_lower} {comp}".strip()
        if (
            cat in ("volleyball", "volley")
            or silo == "volleyball"
            or bool(re.search(r"\b(volleyball|volley|pallavolo|superlega|cev|plusliga)\b", eval_volley))
        ):
            # 1. Volley Femminile
            if is_women or bool(re.search(r"\b(women(?:['’]s)?|femminile|femenil|dames|frauen|ladies|a1\s*femm|serie\s*a1\s*f)\b", eval_text)):
                return "volley", "Volley Femminile"

            # 2. Champions League Volley (Coppe Europee per Club)
            if re.search(r"\b(cev|champions\s*league|cev\s*cup|challenge\s*cup)\b", eval_text):
                return "volley", "Champions League Volley"

            # 3. Nazionali e Internazionale (VNL, Mondiali, Europei, tornei per nazioni)
            if re.search(r"\b(vnl|nations\s*league|mondiali|europei|olympics|world\s*championship|world\s*cup|qualif|national\s*team)\b", eval_text):
                return "volley", "Nazionali e Internazionale"

            # 4. Superlega e Serie A1 (Campionato Italiano Maschile)
            is_italy_volley = (
                country in ("italy", "italia")
                or "🇮🇹" in eval_text
                or bool(re.search(r"\b(italy|italia|italian|trentino|trento|perugia|civitanova|lube|modena|piacenza|verona|padova|cisterna|taranto|grottazzolina|superlega)\b", eval_text))
            )
            is_superlega_comp = bool(re.search(r"\b(superlega|serie\s*a[123]|coppa\s*italia\s*volley|supercoppa\s*volley)\b", eval_text))
            if is_superlega_comp or (is_italy_volley and not bool(re.search(r"\b(plusliga|pro\s*a|bundesliga|superliga\s*russia)\b", eval_text))):
                return "volley", "Superlega e Serie A1"

            # 5. Fallback per campionati esteri maschili (PlusLiga polacca, Francia, Brasile, ecc.)
            return "volley", "Nazionali e Internazionale"

        # ------------------------------------------------------------------
        # 5. FOOTBALL AMERICANO (Dedicated Catalog: 'football_americano')
        # ------------------------------------------------------------------
        eval_am = f"{title_lower} {comp}".strip()
        if (
            (
                cat in ("american-football", "american_football")
                or silo == "american-football"
                or ("football" in cat and cat != "football")
                or bool(re.search(r"\b(nfl|cfl|ufl|cfb|college\s*football|american\s*football|super\s*bowl)\b", eval_am))
            )
            and cat not in ("baseball", "basketball", "basket", "hockey", "tennis", "motor-sports", "volleyball")
        ):
            # 1. CFL (Canadian Football League) & Altre Leghe (UFL, ELF, ecc.)
            if re.search(r"\b(cfl|grey\s*cup|ufl|elf|european\s*league\s*of\s*football|xfl|arena\s*football|indoor\s*football|roughriders|blue\s*bombers|stampeders|tiger-cats|argonauts|alouettes|edmonton\s*elks)\b", eval_am):
                return "football_americano", "CFL e Altre Leghe"

            # 2. NCAA College Football (FBS, FCS, College)
            m_id = str(match.get("id") or "").lower()
            sources = match.get("sources") or []
            source_ids = " ".join(str(s.get("id", "")) for s in sources if isinstance(s, dict)).lower()

            is_explicit_college = (
                bool(re.search(r"\b(cfb|ncaa|college|univ|university|d-?iii|d-?ii|d-?1|fbs|fcs)\b", eval_am))
                or "_cfb_" in m_id
                or m_id.startswith("live_cfb_")
                or "_cfb_" in source_ids
            )

            # 3. NFL (NFL keyword, franchigie con vincolo geografico o moniker univoci, o ESPN cert)
            has_nfl_keyword = bool(re.search(r"\b(nfl|redzone|super\s*bowl|manningcast|pro\s*bowl)\b", eval_am))
            NFL_FRANCHISE_REGEX = (
                r"\b("
                r"arizona\s*cardinals|atlanta\s*falcons|baltimore\s*ravens|buffalo\s*bills|"
                r"carolina\s*panthers|chicago\s*bears|cincinnati\s*bengals|bengals|cleveland\s*browns|"
                r"dallas\s*cowboys|denver\s*broncos|detroit\s*lions|green\s*bay\s*packers|packers|"
                r"houston\s*texans|indianapolis\s*colts|jacksonville\s*jaguars|kansas\s*city\s*chiefs|chiefs|"
                r"las\s*vegas\s*raiders|oakland\s*raiders|la\s*chargers|los\s*angeles\s*chargers|"
                r"la\s*rams|los\s*angeles\s*rams|miami\s*dolphins|dolphins|"
                r"minnesota\s*vikings|vikings|new\s*england\s*patriots|patriots|new\s*orleans\s*saints|"
                r"ny\s*giants|new\s*york\s*giants|ny\s*jets|new\s*york\s*jets|philadelphia\s*eagles|"
                r"pittsburgh\s*steelers|steelers|san\s*francisco\s*49ers|sf\s*49ers|49ers|"
                r"seattle\s*seahawks|seahawks|tampa\s*bay\s*buccaneers|buccaneers|tennessee\s*titans|titans|"
                r"washington\s*commanders|commanders"
                r")\b"
            )
            nfl_match = bool(re.search(NFL_FRANCHISE_REGEX, title_lower))
            is_espn_nfl = bool(match.get("_espn_matched") and match.get("_genre") == "NFL")

            if not is_explicit_college:
                if has_nfl_keyword or nfl_match or is_espn_nfl:
                    return "football_americano", "NFL"

            # 4. Fallback: NCAA College Football
            return "football_americano", "NCAA College Football"

        # ------------------------------------------------------------------
        # 6. BASEBALL (Dedicated Catalog: 'baseball')
        # ------------------------------------------------------------------
        eval_base = f"{title_lower} {comp}".strip()
        if cat == "baseball" or silo == "baseball" or bool(re.search(r"\b(baseball|mlb|npb|kbo|lmb)\b", eval_base)):
            # 1. NCAA College Baseball
            if re.search(r"\b(college|ncaa|cws|college\s*world\s*series)\b", eval_base) or any(c in title_lower for c in self.NCAA_COLLEGES):
                return "baseball", "NCAA College Baseball"

            # 2. Campionati Internazionali (Giappone NPB, Corea KBO, Messico LMB, Taiwan, WBC)
            has_baseball_foreign_flag = any(flag in eval_text for flag in ("🇯🇵", "🇰🇷", "🇲🇽", "🇹🇼", "🇨🇺"))
            is_foreign_baseball = (
                country in ("japan", "giappone", "jp", "korea", "corea", "kr", "mexico", "messico", "mx", "taiwan", "tw", "cuba", "cu", "dominican", "república dominicana")
                or bool(re.search(r"\b(npb|kbo|lmb|cpbl|wbc|world\s*baseball|serie\s*del\s*caribe|japan|korea|mexican)\b", eval_base))
                or has_baseball_foreign_flag
            )
            if is_foreign_baseball:
                return "baseball", "Campionati Internazionali"

            # 3. MLB (Franchigie MLB con prefisso cittadino o moniker univoci MLB)
            MLB_FRANCHISE_REGEX = (
                r"\b("
                r"ny\s*yankees|new\s*york\s*yankees|yankees|"
                r"boston\s*red\s*sox|red\s*sox|"
                r"la\s*dodgers|los\s*angeles\s*dodgers|dodgers|"
                r"chicago\s*cubs|cubs|"
                r"houston\s*astros|astros|"
                r"ny\s*mets|new\s*york\s*mets|mets|"
                r"atlanta\s*braves|braves|"
                r"philadelphia\s*phillies|phillies|"
                r"sf\s*giants|san\s*francisco\s*giants|"
                r"st\s*louis\s*cardinals|"
                r"toronto\s*blue\s*jays|blue\s*jays|"
                r"baltimore\s*orioles|orioles|"
                r"tampa\s*bay\s*rays|tampa\s*bay|"
                r"chicago\s*white\s*sox|white\s*sox|"
                r"cleveland\s*guardians|guardians|"
                r"detroit\s*tigers|"
                r"kansas\s*city\s*royals|"
                r"minnesota\s*twins|"
                r"la\s*angels|los\s*angeles\s*angels|"
                r"oakland\s*athletics|athletics|"
                r"seattle\s*mariners|mariners|"
                r"texas\s*rangers|"
                r"miami\s*marlins|marlins|"
                r"washington\s*nationals|"
                r"cincinnati\s*reds|"
                r"milwaukee\s*brewers|brewers|"
                r"pittsburgh\s*pirates|"
                r"arizona\s*diamondbacks|diamondbacks|d-backs|"
                r"colorado\s*rockies|"
                r"san\s*diego\s*padres|padres"
                r")\b"
            )
            if bool(re.search(r"\b(mlb|world\s*series)\b", eval_base)) or (not is_foreign_baseball and re.search(MLB_FRANCHISE_REGEX, title_lower)):
                return "baseball", "MLB"

            # 4. Fallback per qualsiasi altro baseball
            return "baseball", "Campionati Internazionali"

        # ------------------------------------------------------------------
        # 7. HOCKEY (Dedicated Catalog: 'hockey')
        # ------------------------------------------------------------------
        eval_hockey = f"{title_lower} {comp}".strip()
        if (
            cat in ("hockey", "ice-hockey", "ice hockey")
            or silo == "hockey"
            or any(k in eval_hockey for k in ("hockey", "nhl", "khl", "shl", "ohl", "ushl"))
        ) and not is_football_context:
            # 1. Mondiali e Nazionali
            if re.search(r"\b(iihf|mondiali|olympics|world\s*championship|national\s*team|four\s*nations)\b", eval_hockey):
                return "hockey", "Mondiali e Nazionali"

            # 2. Leghe Europee (KHL, SHL svedese, Liiga finlandese, DEL tedesca, Extraliga ceca, Swiss NL, ecc.)
            has_hockey_eu_flag = any(flag in eval_text for flag in ("🇸🇪", "🇫🇮", "🇩🇪", "🇨🇿", "🇨🇭", "🇷🇺", "🇦🇹", "🇸🇰", "🇳🇴"))
            is_european_hockey = (
                country in ("sweden", "svezia", "se", "finland", "finlandia", "fi", "germany", "germania", "de", "czech", "cechia", "cz", "swiss", "switzerland", "svizzera", "ch", "russia", "ru", "austria", "at", "slovakia", "sk", "norway", "norvegia", "no", "uk", "great britain")
                or bool(re.search(r"\b(khl|del|liiga|shl|extraliga|tipsport|allsvenskan|national\s*league|icehl|ebel|alps|chl|champions\s*hockey|leghe\s*europee|eihl)\b", eval_hockey))
                or has_hockey_eu_flag
            )
            if is_european_hockey:
                return "hockey", "KHL e Leghe Europee"

            # 3. NHL (Franchigie NHL con prefisso cittadino o moniker univoci NHL)
            NHL_FRANCHISE_REGEX = (
                r"\b("
                r"boston\s*bruins|bruins|"
                r"buffalo\s*sabres|sabres|"
                r"detroit\s*red\s*wings|red\s*wings|"
                r"florida\s*panthers|"
                r"montreal\s*canadiens|canadiens|habs|"
                r"ottawa\s*senators|senators|"
                r"tampa\s*bay\s*lightning|lightning|"
                r"toronto\s*maple\s*leafs|maple\s*leafs|"
                r"carolina\s*hurricanes|hurricanes|"
                r"columbus\s*blue\s*jackets|blue\s*jackets|"
                r"new\s*jersey\s*devils|nj\s*devils|"
                r"ny\s*islanders|new\s*york\s*islanders|islanders|"
                r"ny\s*rangers|new\s*york\s*rangers|"
                r"philadelphia\s*flyers|flyers|"
                r"pittsburgh\s*penguins|penguins|"
                r"washington\s*capitals|capitals|caps|"
                r"chicago\s*blackhawks|blackhawks|"
                r"colorado\s*avalanche|avalanche|"
                r"dallas\s*stars|"
                r"minnesota\s*wild|"
                r"nashville\s*predators|predators|"
                r"st\s*louis\s*blues|"
                r"utah\s*hockey\s*club|"
                r"anaheim\s*ducks|"
                r"calgary\s*flames|"
                r"edmonton\s*oilers|"
                r"la\s*kings|los\s*angeles\s*kings|"
                r"san\s*jose\s*sharks|"
                r"seattle\s*kraken|kraken|"
                r"vancouver\s*canucks|canucks|"
                r"vegas\s*golden\s*knights|golden\s*knights|"
                r"winnipeg\s*jets"
                r")\b"
            )
            if bool(re.search(r"\b(nhl|stanley\s*cup)\b", eval_hockey)) or (not is_european_hockey and re.search(NHL_FRANCHISE_REGEX, title_lower)):
                return "hockey", "NHL"

            # 4. Fallback per qualsiasi altro hockey
            return "hockey", "KHL e Leghe Europee"

        # ------------------------------------------------------------------
        # 8. SPORT DA COMBATTIMENTO (Dedicated Catalog: 'combattimento')
        # ------------------------------------------------------------------
        eval_fight = f"{title_lower} {comp}".strip()
        if (
            cat in ("fight", "mma", "ufc", "boxing", "wrestling")
            or silo == "fight"
            or bool(re.search(r"\b(ufc|wwe|aew|bellator|boxing|boxe|mma|pfl|one\s*championship)\b", eval_fight))
        ):
            if re.search(r"\b(wwe|aew|nxt|smackdown|raw|tna|wrestling|wrestlemania|royal\s*rumble|summerslam|survivor\s*series)\b", eval_fight):
                return "combattimento", "Wrestling e WWE"
            if re.search(r"\b(ufc|dwcs|dana\s*white)\b", eval_fight):
                return "combattimento", "UFC"
            if re.search(r"\b(mma|bellator|pfl|one\s*championship|ksw|lfc|cage\s*warriors|combate|oktagon|rizin|invicta)\b", eval_fight):
                return "combattimento", "MMA"
            if re.search(r"\b(boxing|boxe|pugilato|bout|heavyweight|title\s*fight|wba|wbc|wbo|ibf|top\s*rank|matchroom|golden\s*boy|queensberry|boxxer)\b", eval_fight):
                return "combattimento", "Boxe"
            if "box" in eval_fight:
                return "combattimento", "Boxe"
            return "combattimento", "MMA"

        # ------------------------------------------------------------------
        # 9. ALTRI SPORT (Dedicated Catalog: 'altri_sport')
        # ------------------------------------------------------------------
        eval_altri = f"{title_lower} {comp}".strip()
        is_afl_match = (
            cat == "afl"
            or bool(re.search(r"\b(afl|aflw|australian\s*rules|footy)\b", eval_altri))
            or (any(t in eval_text for t in ("carlton blues", "hawthorn hawks", "collingwood magpies", "richmond tigers", "geelong cats", "fremantle dockers", "essendon bombers", "sydney swans", "brisbane lions", "adelaide crows", "st kilda", "port adelaide power", "western bulldogs", "gold coast suns", "gws giants", "north melbourne kangaroos", "west coast eagles", "melbourne demons")))
        )

        if (
            cat in ("golf", "darts", "rugby", "afl", "cricket", "handball", "waterpolo", "table-tennis", "badminton", "billiards", "cycling", "altri_sport")
            or silo in ("golf", "darts", "rugby", "afl", "aussie-rules", "cricket", "handball", "waterpolo", "table-tennis", "badminton", "billiards", "cycling")
            or is_afl_match
            or any(k in eval_altri for k in (
                "golf", "pga", "darts", "freccette", "rugby", "ciclismo", "cycling", "uci", "road race", "time trial", "snooker", "padel",
                "handball", "dhb pokal", "pallamano", "ihf", "ehf", "table tennis", "ping pong", "waterpolo", "pallanuoto", "badminton",
                "cricket", "test match", "one day international", "the hundred", "big bash", "ashes"
            ))
            or re.search(r"\b(cricket|odi|t20|twenty20|ipl|ashes|uci|ihf|ehf)\b", eval_altri)
        ):
            if is_afl_match or re.search(r"\b(rugby|afl|aflw|six\s*nations|sei\s*nazioni|nrl|top\s*14|premiership\s*rugby|urc|super\s*rugby|champions\s*cup\s*rugby)\b", eval_altri) or cat in ("rugby", "afl") or silo in ("rugby", "afl", "aussie-rules"):
                return "altri_sport", "Rugby e AFL"
            if re.search(r"\b(cricket|odi|t20|twenty20|test\s*match|ipl|one\s*day\s*international|the\s*hundred|big\s*bash|ashes)\b", eval_altri) or cat == "cricket" or silo == "cricket":
                return "altri_sport", "Cricket"
            if re.search(r"\b(golf|pga|lpga|ryder\s*cup|presidents\s*cup|liv|dp\s*world|masters\s*tournament)\b", eval_altri) or cat == "golf" or silo == "golf":
                return "altri_sport", "Golf"
            if re.search(r"\b(darts|freccette|pdc|world\s*darts)\b", eval_altri):
                return "altri_sport", "Freccette / Darts"
            if (re.search(r"\b(ciclismo|cycling|tour\s*de\s*france|giro\s*d['’]italia|vuelta|uci|road\s*race|time\s*trial)\b", eval_altri) or cat == "cycling" or silo == "cycling") and "hockey" not in title_lower:
                return "altri_sport", "Ciclismo"
            if re.search(r"\b(handball|pallamano|ihf|ehf|dhb\s*pokal|bundesliga\s*handball)\b", eval_altri) or cat == "handball" or silo == "handball":
                return "altri_sport", "Pallamano"
            return "altri_sport", "Biliardo, Padel e Altri"

        # ------------------------------------------------------------------
        # ------------------------------------------------------------------
        # 10. CALCIO (FOOTBALL / SOCCER)
        # ------------------------------------------------------------------
        # Guard: non-soccer sports (surf, sailing, boating, water sports, padel, billiards) must never enter football
        is_water_or_niche = bool(re.search(r"\b(surf|surfing|sailing|boating|water\s*sport|swimming|snooker|billiard|padel)\b", eval_text))
        is_explicit_other_cat = bool(cat and cat not in ("", "other", "unknown", "altri sport", "live sports", "football", "soccer"))
        
        is_ita_derby = any(derby in title_lower for derby in (
            "inter vs milan", "milan vs inter", "roma vs lazio", "lazio vs roma",
            "juventus vs torino", "torino vs juventus", "inter vs juventus", "juventus vs inter",
            "napoli vs roma", "roma vs napoli", "palermo vs bari", "bari vs palermo",
            "genoa vs sampdoria", "sampdoria vs genoa"
        ))

        # Candidate soccer match check: category/silo is football, classic derby, or classic "vs" match with club indicators
        is_soccer_candidate = (
            not is_water_or_niche
            and not is_explicit_other_cat
            and (
                cat in ("football", "soccer")
                or silo == "football"
                or is_ita_derby
                or (
                    "vs" in title_lower
                    and (
                        any(k in eval_text for k in ("fc", "cf", "calcio", "football", "soccer", "serie", "league", "cup", "copa", "championship", "campionato", "torneo", "division", "trofeo", "amichevole", "friendly", "primavera"))
                        or is_youth
                        or bool(self._EUROPEAN_LEAGUES_REGEX.search(eval_text))
                        or bool(self._EXTRA_EU_LEAGUES_REGEX.search(eval_text))
                    )
                )
            )
        )

        if is_soccer_candidate:
            has_extra_eu_flag = any(flag in eval_text for flag in ("🇧🇷", "🇦🇷", "🇨🇴", "🇺🇾", "🇵🇾", "🇨🇱", "🇵🇪", "🇪🇨", "🇲🇽", "🇺🇸", "🇸🇦", "🇯🇵", "🇰🇷", "🇦🇺"))
            is_extra_eu = bool(self._EXTRA_EU_CONFED_REGEX.search(eval_text) or self._EXTRA_EU_LEAGUES_REGEX.search(eval_text) or has_extra_eu_flag)
            is_foreign = bool(self._FOREIGN_LEAGUE_REGEX.search(eval_text)) or is_extra_eu or "inter miami" in title_lower
            is_extra_eu_confed = bool(self._EXTRA_EU_CONFED_REGEX.search(eval_text))

            # --------------------------------------------------------------
            # 1. YOUTH & COLLEGE (International / National youth / College)
            # Evaluated BEFORE national senior matches so "World Cup U17" or "U19 Friendly"
            # never pollutes senior "Nazionali e Amichevoli"
            # --------------------------------------------------------------
            if is_youth and not bool(re.search(r"\b(primavera|u23|next\s*gen|futuro)\b", eval_text)):
                return "calcio_estero", "Europei Under 21 e Nazionali Giovanili"

            # --------------------------------------------------------------
            # 2. NATIONAL TEAMS & AMICHEVOLI (e.g. Italia vs Spagna, Nations League, Qualificazioni)
            # Evaluated before domestic leagues so "Italia vs Spagna" never enters Serie A
            # --------------------------------------------------------------
            is_national_match = (
                bool(self._FRIENDLY_REGEX.search(eval_text))
                or bool(re.search(r"\b(nations\s*league|fifa|qualif|international|world\s*cup|european\s*championship|african\s*cup|afcon|copa\s*america|asian\s*cup|gold\s*cup|intercontinental)\b", eval_text))
            )
            if is_national_match:
                return "calcio_estero", "Nazionali e Amichevoli"

            # --------------------------------------------------------------
            # 3. EUROPEAN CUPS & UEFA TOURNAMENTS (Champions, Europa, Conference)
            # Evaluated first because teams come from different nations
            # --------------------------------------------------------------
            is_uefa_cup = bool(re.search(r"\b(uefa\s*champions|champions\s*league|ucl|uwcl|europa\s*league|uel|conference\s*league|uecl|uefa\s*super\s*cup)\b", eval_text))
            is_cross_national_clash = any(clash in title_lower for clash in (
                "manchester city vs inter", "inter vs manchester city",
                "real madrid vs bayern", "bayern vs real madrid",
                "barcelona vs psg", "psg vs barcelona",
                "liverpool vs real madrid", "real madrid vs liverpool",
                "arsenal vs bayern", "bayern vs arsenal",
                "milan vs liverpool", "liverpool vs milan",
                "inter vs arsenal", "arsenal vs inter",
                "juventus vs manchester", "manchester vs juventus",
                "psg vs real madrid", "real madrid vs psg",
                "bayern vs psg", "psg vs bayern"
            ))
            if (is_uefa_cup or is_cross_national_clash) and not is_extra_eu_confed:
                if is_cross_national_clash or re.search(r"\b(champions\s*league|uefa\s*champions|ucl|uwcl)\b", eval_text):
                    return "calcio_estero", "Champions League"
                return "calcio_estero", "Europa e Conference League"

            # --------------------------------------------------------------
            # 4. EXTRA-EU CONFEDERATIONS (CONMEBOL Libertadores, AFC Champions, CAF, CONCACAF)
            # --------------------------------------------------------------
            if is_extra_eu_confed:
                return "calcio_estero", "Americhe e Leghe Extra-UE"

            # --------------------------------------------------------------
            # 5. CALCIO ITALIANO (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
            # --------------------------------------------------------------
            is_ita_nation = (
                country in ("italia", "italy")
                or "🇮🇹" in eval_text
                or is_ita_derby
                or bool(re.search(r"\b(italy|italia|italian|coppa\s*italia|supercoppa\s*italiana|primavera)\b", eval_text))
            )
            if is_ita_nation and not is_foreign and not is_extra_eu:
                if is_women:
                    return "calcio_italiano", "Calcio Femminile"
                if is_youth or bool(re.search(r"\b(primavera|allievi|giovanissimi|u23|next\s*gen|futuro)\b", eval_text)):
                    return "calcio_italiano", "Primavera e Giovanili"
                if re.search(r"\b(coppa\s*italia|supercoppa\s*italiana|supercoppa)\b", eval_text):
                    return "calcio_italiano", "Coppa Italia e Supercoppa"
                if re.search(r"\bserie\s*c\b", eval_text):
                    return "calcio_italiano", "Serie C"
                if re.search(r"\bserie\s*b\b", eval_text) or any(d in title_lower for d in ("palermo vs bari", "bari vs palermo", "pisa vs spezia", "cosenza vs brescia")):
                    return "calcio_italiano", "Serie B"
                if re.search(r"\bserie\s*a\b", eval_text):
                    return "calcio_italiano", "Serie A"
                # Fallback for Italian clubs/derbies not in Serie A/B/C
                return "calcio_italiano", "Serie A"

            # --------------------------------------------------------------
            # 6. PREMIER LEAGUE (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
            # Gate 1 (Positively England / UK): positive country, flag, known derby, or English keyword
            # Gate 2 (Premier League / FA Cup / Carabao Cup)
            # SCARTO: English lower leagues / discards -> 'Altri Campionati Europei'
            # --------------------------------------------------------------
            is_epl_derby = any(d in title_lower for d in (
                "arsenal vs liverpool", "liverpool vs arsenal",
                "chelsea vs liverpool", "liverpool vs chelsea",
                "arsenal vs chelsea", "chelsea vs arsenal",
                "tottenham vs arsenal", "arsenal vs tottenham",
                "manchester united vs liverpool", "liverpool vs manchester united",
                "manchester city vs manchester united", "manchester united vs manchester city"
            ))
            is_england = (
                country in ("inghilterra", "england", "uk", "regno unito")
                or "🏴󠁧󠁢󠁥󠁮󠁧󠁿" in eval_text
                or is_epl_derby
                or bool(re.search(r"\b(england|inghilterra|uk|regno\s*unito|epl|english)\b", eval_text))
            )
            if is_england and not is_extra_eu:
                if is_youth or bool(re.search(r"\b(u21|u23|u18|premier\s*league\s*2)\b", eval_text)):
                    return "calcio_estero", "Europei Under 21 e Nazionali Giovanili"
                if is_women:
                    return "calcio_estero", "Altri Campionati Europei"
                if is_epl_derby or bool(re.search(r"\b(premier\s*league|epl|fa\s*cup|carabao|efl\s*cup)\b", eval_text)):
                    return "calcio_estero", "Premier League"
                # SCARTO: English domestic match that is not Premier League (e.g. Championship, League One, League Two)
                # Re-routed directly to the European catch-all
                return "calcio_estero", "Altri Campionati Europei"

            # --------------------------------------------------------------
            # 7. LA LIGA (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
            # Gate 1 (Positively Spain): positive country, flag, known derby, or Spanish keyword
            # Gate 2 (LaLiga / Primera Division / Copa del Rey / Supercopa)
            # SCARTO: Spanish lower leagues / discards -> 'Altri Campionati Europei'
            # --------------------------------------------------------------
            is_liga_derby = any(d in title_lower for d in (
                "real madrid vs barcelona", "barcelona vs real madrid", "el clasico",
                "real madrid vs atletico", "atletico vs real madrid",
                "sevilla vs betis", "betis vs sevilla"
            ))
            is_spain = (
                country in ("spagna", "spain")
                or "🇪🇸" in eval_text
                or is_liga_derby
                or bool(re.search(r"\b(spain|spagna|espana|españa|spanish|laliga)\b", eval_text))
            )
            if is_spain and not is_extra_eu:
                if is_youth or bool(re.search(r"\b(u21|u23|u18|u19)\b", eval_text)):
                    return "calcio_estero", "Europei Under 21 e Nazionali Giovanili"
                if is_women:
                    return "calcio_estero", "Altri Campionati Europei"
                is_segunda = bool(re.search(r"\b(segunda|laliga\s*2|hypermotion|primera\s*rfef|rfef)\b", eval_text))
                if not is_segunda and (is_liga_derby or bool(re.search(r"\b(laliga|la\s*liga|primera\s*division|copa\s*del\s*rey|supercopa)\b", eval_text))):
                    return "calcio_estero", "La Liga"
                # SCARTO: Spanish domestic match that is not LaLiga (e.g. Segunda División, Hypermotion)
                # Re-routed directly to the European catch-all
                return "calcio_estero", "Altri Campionati Europei"

            # --------------------------------------------------------------
            # 8. BUNDESLIGA E LIGUE 1 (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
            # Gate 1 (Positively Germany or France)
            # Gate 2 (Bundesliga / DFB-Pokal or Ligue 1 / Coupe de France)
            # SCARTO: German or French lower leagues / discards -> 'Altri Campionati Europei'
            # --------------------------------------------------------------
            is_germany = (
                country in ("germania", "germany")
                or "🇩🇪" in eval_text
                or bool(re.search(r"\b(germany|germania|deutschland|german)\b", eval_text))
            )
            is_france = (
                country in ("francia", "france")
                or "🇫🇷" in eval_text
                or bool(re.search(r"\b(france|francia|french)\b", eval_text))
            )
            if (is_germany or is_france) and not is_extra_eu:
                if is_youth or bool(re.search(r"\b(u21|u23|u18|u19)\b", eval_text)):
                    return "calcio_estero", "Europei Under 21 e Nazionali Giovanili"
                if is_women:
                    return "calcio_estero", "Altri Campionati Europei"
                is_lower_div = bool(re.search(r"\b(2\.?\s*bundesliga|3\.?\s*liga|ligue\s*2|national)\b", eval_text))
                if not is_lower_div and bool(re.search(r"\b(bundesliga|dfb\s*pokal|ligue\s*1|coupe\s*de\s*france|trophee\s*des\s*champions)\b", eval_text)):
                    return "calcio_estero", "Bundesliga e Ligue 1"
                # SCARTO: German / French lower division match (e.g. 2. Bundesliga, Ligue 2)
                # Re-routed directly to the European catch-all
                return "calcio_estero", "Altri Campionati Europei"

            # --------------------------------------------------------------
            # 9. ALTRI CAMPIONATI EUROPEI (Strict Gate: European nation/league/flag required)
            # --------------------------------------------------------------
            has_eu_flag = any(flag in eval_text for flag in self._EUROPEAN_FLAGS)
            is_european_country = country in self._EUROPEAN_COUNTRIES
            is_european_league = bool(self._EUROPEAN_LEAGUES_REGEX.search(eval_text))
            if (is_european_league or is_european_country or has_eu_flag) and not is_extra_eu:
                return "calcio_estero", "Altri Campionati Europei"

            # --------------------------------------------------------------
            # 10. AMERICHE E LEGHE EXTRA-UE (Fallback naturale per tutto il calcio non europeo)
            # --------------------------------------------------------------
            return "calcio_estero", "Americhe e Leghe Extra-UE"

        # ------------------------------------------------------------------
        # 11. FALLBACK
        # ------------------------------------------------------------------
        return "altri_sport", "Biliardo, Padel e Altri"

    def classify_genre(self, match: Dict[str, Any]) -> str:
        """Helper returning only the genre string for backward compatibility."""
        _, genre = self.classify(match)
        return genre

genre_classifier = GenreClassifier()
