import re
from typing import Any, Dict, Optional, Tuple
from app.services.canonical_competition_service import (
    is_redzone_event,
    is_simulcast_event,
    get_simulcast_info,
)

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
        r"\b(u-?1[5-9]|under\s*1[5-9]|u-?2[01]|under\s*2[01]|primavera|youth)\b",
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
        r"saudi|roshn|saudi\s*pro\s*league|king\s*cup\s*champions|"
        r"qatar|stars\s*league|uae|pro\s*league\s*uae|"
        r"j-?league|j1\s*league|k-?league|csl|chinese\s*super\s*league|"
        r"a-?league|australia|indian\s*super\s*league|isl|"
        r"egypt|morocco|botola|south\s*africa|psl"
        r")\b",
        re.IGNORECASE,
    )

    # European leagues outside top 5 (e.g. EFL, Portugal, Netherlands, Turkey, Greece, Denmark, etc.)
    _EUROPEAN_LEAGUES_REGEX = re.compile(
        r"\b(england|efl|championship|league\s*one|league\s*two|fa\s*cup|carabao|trophy|portugal|primeira\s*liga|eredivisie|netherlands|scotland|premiership|belgium|jupiler|pro\s*league|turkey|sper\s*lig|super\s*lig|greece|super\s*league|denmark|superliga|1\.\s*division|sweden|allsvenskan|superettan|norway|eliteserien|austria|bundesliga\s*austria|switzerland|super\s*league|poland|ekstraklasa|czech|croatia|hnl|serbia|prva\s*liga|superliga|romania|liga\s*i|bulgaria|parva\s*liga|hungary|ukraine|ireland|irish|finland|finnish|cyprus|slovenia|slovakia)\b",
        re.IGNORECASE,
    )

    # Friendly / Exhibition matches
    _FRIENDLY_REGEX = re.compile(
        r"\b(friendly|club\s*friendly|amichevole|trofeo)\b",
        re.IGNORECASE,
    )


    # NBA Franchises
    NBA_TEAMS = {
        "hawks", "celtics", "nets", "hornets", "bulls", "cavaliers", "mavericks",
        "nuggets", "pistons", "warriors", "rockets", "pacers", "clippers", "lakers",
        "grizzlies", "heat", "bucks", "timberwolves", "pelicans", "knicks", "thunder",
        "magic", "76ers", "sixers", "suns", "trail blazers", "blazers", "kings",
        "spurs", "raptors", "jazz", "wizards"
    }


    # Superlega Volley
    SUPERLEGA_VOLLEY_TEAMS = {
        "sir safety perugia", "perugia volley", "perugia", "itas trentino", "trentino volley",
        "trento", "cucine lube civitanova", "civitanova", "lube", "valsa group modena", "modena volley",
        "modena", "allianz milano", "milano volley", "mint vero volley monza", "monza volley",
        "gas sales piacenza", "piacenza volley", "rana verona", "verona volley", "sonepar padova",
        "padova volley", "cisterna volley", "cisterna", "gioiella prisma taranto", "taranto volley",
        "yuasa battery grottazzolina", "grottazzolina", "imoco conegliano", "conegliano",
        "savino del bene scandicci", "scandicci", "igor gorgonzola novara", "novara volley",
        "reale mutua fenera chieri", "chieri"
    }

    # CFL Franchises (Canadian Football League)
    CFL_TEAMS = {
        "blue bombers", "winnipeg", "roughriders", "saskatchewan", "stampeders", "calgary",
        "bc lions", "elks", "edmonton elks", "tiger-cats", "ticats", "hamilton tiger-cats",
        "argonauts", "toronto argonauts", "redblacks", "ottawa redblacks", "alouettes", "montreal alouettes"
    }

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

    # MLB Teams
    MLB_TEAMS = {
        "yankees", "red sox", "dodgers", "cubs", "astros", "mets", "braves", "phillies",
        "giants", "cardinals", "blue jays", "orioles", "rays", "white sox", "guardians",
        "tigers", "royals", "twins", "angels", "athletics", "mariners", "rangers",
        "marlins", "nationals", "reds", "brewers", "pirates", "diamondbacks", "rockies", "padres"
    }

    # NHL Teams
    NHL_TEAMS = {
        "bruins", "sabres", "red wings", "panthers", "canadiens", "senators", "lightning",
        "maple leafs", "hurricanes", "blue jackets", "devils", "islanders", "rangers",
        "flyers", "penguins", "capitals", "blackhawks", "avalanche", "stars", "wild",
        "predators", "blues", "coyotes", "flames", "oilers", "kings", "sharks", "kraken",
        "canucks", "golden knights", "jets", "utah hockey club"
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
            # Country-specific guard for homonym league names (Serie A, Serie B, Serie C)
            if canon in ("football_serie_a", "football_serie_b", "football_serie_c"):
                raw_eval = f"{title_lower} {(match.get('competition') or '').lower()}"
                if any(x in raw_eval for x in ("brazil", "brasil", "ecuador", "colombia", "chile", "peru", "argentina", "mexico", "usa", "🇧🇷", "🇪🇨")):
                    return "calcio_estero", "Americhe e Leghe Extra-UE"

            if canon == "football_la_liga":
                raw_eval = f"{title_lower} {(match.get('competition') or '').lower()}"
                if any(x in raw_eval for x in ("chile", "argentina", "uruguay", "nicaragua", "peru", "colombia", "mexico")):
                    return "calcio_estero", "Americhe e Leghe Extra-UE"

            if canon == "football_premier_league":
                raw_eval = f"{title_lower} {(match.get('competition') or '').lower()}"
                if any(x in raw_eval for x in ("canada", "canadian", "egitto", "egypt", "armenia", "iraq", "jamaica", "giamaica", "kuwait", "bahrain", "bangladesh", "azerbaijan", "antigua", "ethiopia", "etiopia", "eswatini")):
                    return "calcio_estero", "Americhe e Leghe Extra-UE"

            canon_map = {
                "football_serie_a": ("calcio_italiano", "Serie A"),
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
            home = self._normalize_name(teams.get("home", {}).get("name", ""))
            away = self._normalize_name(teams.get("away", {}).get("name", ""))
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
        if not is_football_context and (
            cat == "tennis"
            or silo == "tennis"
            or "tennis" in title_lower
            or any(k in title_lower for k in ("atp", "wta", "bjk", "davis cup", "wimbledon", "us open", "roland garros", "australian open"))
        ):
            if re.search(r"\b(wimbledon|us\s*open|roland\s*garros|australian\s*open|slam)\b", title_lower):
                return "tennis", "Grandi Slam"
            if re.search(r"\b(davis|bjk|billie\s*jean|laver\s*cup)\b", title_lower):
                return "tennis", "Coppa Davis e BJK Cup"
            if "atp" in title_lower and "wta" in title_lower:
                return "tennis", "Challenger e Altri"
            if is_women or "wta" in title_lower:
                return "tennis", "WTA"
            if "atp" in title_lower:
                return "tennis", "ATP"
            if re.search(r"\b(challenger|itf|exhibition|esibizione)\b", title_lower):
                return "tennis", "Challenger e Altri"
            return "tennis", "ATP"

        # ------------------------------------------------------------------
        # 2. MOTORI (Dedicated Catalog: 'motori')
        # ------------------------------------------------------------------
        if cat in ("motor-sports", "motorsports", "motorsport") or any(k in title_lower for k in ("formula 1", "motogp", "nascar", "rally", "f1")):
            if re.search(r"\b(f1|formula\s*1|formula\s*one)\b", title_lower) or "formula 1" in title_lower:
                return "motori", "Formula 1"
            if re.search(r"\b(motogp|moto\s*gp|moto2|moto3|superbike|wsbk)\b", title_lower):
                return "motori", "MotoGP e Superbike"
            if re.search(r"\b(rally|wrc|dakar)\b", title_lower):
                return "motori", "Rally e WRC"
            if re.search(r"\b(nascar|indycar|indy\s*500|supercars|dtm|le\s*mans)\b", title_lower):
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
            or re.search(r"\b(nba|wnba|euroleague|eurocup|lba|nbl|fiba)\b", eval_text)
        ) and not is_football_context:
            # LIVELLO 1: Tornei Internazionali e Acronimi Globali

            # 1. WNBA & Basket Femminile
            if is_women or re.search(r"\b(wnba|liga\s*femenina|tauihi|wlb|women|femminile|femenino)\b", eval_text):
                return "basket", "WNBA e Femminile"

            # 2. FIBA, 3x3, Tornei Nazionali
            if re.search(r"\b(fiba|3x3|world\s*tour|world\s*cup|mondiali|eurobasket|qualif|national\s*team|intercontinental\s*cup)\b", eval_text):
                return "basket", "FIBA e Tornei Nazionali"

            # 3. Eurolega & Eurocup (Coppe Europee)
            if re.search(r"\b(euroleague|eurocup|eurolega|bcl|basketball\s*champions|fiba\s*europe\s*cup)\b", eval_text):
                return "basket", "Eurolega ed Eurocup"

            # 4. NCAA & College Basket
            if re.search(r"\b(ncaa|college\s*basketball|march\s*madness)\b", eval_text):
                return "basket", "NCAA e College Basket"

            # 5. NBA (USA Men's Professional League)
            if re.search(r"\b(nba)\b", eval_text) or any(t in home or t in away or t in eval_text for t in self.NBA_TEAMS):
                return "basket", "NBA"

            # LIVELLO 2: Campionati Nazionali (Country / Bandiere / Leghe Nazionali)

            # A) LBA Serie A (Italia)
            # Riconosce: country Italia, bandiera 🇮🇹, o sigle del basket italiano
            is_italy_basket = (
                country in ("italy", "italia")
                or "🇮🇹" in title
                or bool(re.search(r"\b(lba|serie\s*a\s*basket|lega\s*basket|serie\s*a2|coppa\s*italia\s*basket)\b", eval_text))
            )
            if is_italy_basket:
                return "basket", "LBA Serie A"

            # B) Campionati Esteri ed NBL (Tutti gli altri campionati nazionali)
            # Spagna (ACB, FEB, 🇪🇸), Australia/NZ (NBL, 🇦🇺/🇳🇿), Germania (BBL, 🇩🇪),
            # Francia (LNB, Pro A, 🇫🇷), Grecia (GBL, 🇬🇷), Turchia (BSL, 🇹🇷),
            # Balcani (ABA League), Russia (VTB), Brasile (NBB), ecc.
            return "basket", "Campionati Esteri ed NBL"

        # ------------------------------------------------------------------
        # 4. VOLLEY (Dedicated Catalog: 'volley')
        # ------------------------------------------------------------------
        if cat in ("volleyball", "volley") or any(k in eval_text for k in ("volleyball", "pallavolo", "superlega")):
            if is_women or "femminile" in eval_text:
                return "volley", "Volley Femminile"
            if re.search(r"\b(champions|cev)\b", eval_text):
                return "volley", "Champions League Volley"
            if re.search(r"\b(superlega|serie\s*a1|italy\s*-\s*superlega)\b", eval_text) or any(t in home or t in away or t in eval_text for t in self.SUPERLEGA_VOLLEY_TEAMS):
                return "volley", "Superlega e Serie A1"
            if re.search(r"\b(nazionale|vnl|mondiali|europei)\b", eval_text):
                return "volley", "Nazionali e Internazionale"
            return "volley", "Superlega e Serie A1"

        # ------------------------------------------------------------------
        # 5. FOOTBALL AMERICANO (Dedicated Catalog: 'football_americano')
        # ------------------------------------------------------------------
        if cat == "american-football" or ("football" in cat and cat != "football") or "nfl" in title_lower or "cfl" in title_lower:
            # 1. CFL (Canadian Football League)
            if "cfl" in title_lower or "grey cup" in title_lower or any(t in title_lower for t in self.CFL_TEAMS):
                return "football_americano", "CFL e Altre Leghe"

            # 2. UFL & Altre Leghe minori
            if re.search(r"\b(ufl|elf|european\s*league\s*of\s*football|stallions|renegades|defenders|roughnecks|showboats|brahmas|battlehawks)\b", title_lower):
                return "football_americano", "CFL e Altre Leghe"

            # 3. NFL (Strict: full franchise names, unambiguous NFL keywords, or ESPN certification)
            m_id = str(match.get("id") or "").lower()
            sources = match.get("sources") or []
            source_ids = " ".join(str(s.get("id", "")) for s in sources if isinstance(s, dict)).lower()
            comp_lower = comp.lower()

            is_explicit_college = (
                bool(re.search(r"\b(cfb|ncaa|college|univ|university|d-?iii|d-?ii|d-?1|fbs|fcs)\b", title_lower))
                or bool(re.search(r"\b(cfb|ncaa|college)\b", comp_lower))
                or "_cfb_" in m_id
                or m_id.startswith("live_cfb_")
                or "_cfb_" in source_ids
            )

            has_nfl_keyword = bool(re.search(r"\b(nfl|redzone|super\s*bowl|manningcast|pro\s*bowl)\b", title_lower)) or "nfl" in comp_lower
            nfl_full = any(team in title_lower for team in self.NFL_FULL_TEAMS)
            is_espn_nfl = bool(match.get("_espn_matched") and match.get("_genre") == "NFL")

            if not is_explicit_college:
                if has_nfl_keyword or nfl_full or is_espn_nfl:
                    return "football_americano", "NFL"

            # 4. All other American Football (including Division I/II/III and regional colleges) belongs to NCAA!
            return "football_americano", "NCAA College Football"

        # ------------------------------------------------------------------
        # 6. BASEBALL (Dedicated Catalog: 'baseball')
        # ------------------------------------------------------------------
        if cat == "baseball" or "baseball" in title_lower or "mlb" in title_lower:
            if (
                re.search(r"\b(college|ncaa|cws|college\s*world\s*series)\b", title_lower)
                or any(c in title_lower for c in self.NCAA_COLLEGES)
            ):
                return "baseball", "NCAA College Baseball"
            if re.search(r"\b(mlb)\b", title_lower) or any(t in home or t in away or t in title_lower for t in self.MLB_TEAMS):
                return "baseball", "MLB"
            return "baseball", "Campionati Internazionali"

        # ------------------------------------------------------------------
        # 7. HOCKEY (Dedicated Catalog: 'hockey')
        # ------------------------------------------------------------------
        if (
            cat in ("hockey", "ice-hockey", "ice hockey")
            or silo == "hockey"
            or any(k in title_lower for k in ("hockey", "nhl", "khl", "shl", "ohl", "ushl"))
        ) and not is_football_context:
            if re.search(r"\b(nhl|stanley\s*cup)\b", eval_text) or any(t in home or t in away or t in title_lower for t in self.NHL_TEAMS):
                return "hockey", "NHL"
            if re.search(r"\b(khl|del|liiga|shl|extraliga|tipsport|allsvenskan|national\s*league|icehl|ebel|alps|leghe\s*europee|sweden|svezia|czech|cechia|finland|germany|deutsch|swiss|svizzera|austria)\b", eval_text):
                return "hockey", "KHL e Leghe Europee"
            if re.search(r"\b(iihf|mondiali|olympics|world\s*championship|roller|field)\b", eval_text):
                return "hockey", "Mondiali e Nazionali"
            return "hockey", "KHL e Leghe Europee"

        # ------------------------------------------------------------------
        # 8. SPORT DA COMBATTIMENTO (Dedicated Catalog: 'combattimento')
        # ------------------------------------------------------------------
        if cat in ("fight", "mma", "ufc", "boxing", "wrestling") or any(k in title_lower for k in ("ufc", "wwe", "aew", "bellator", "boxing")):
            if re.search(r"\b(wwe|aew|nxt|smackdown|raw|tna|wrestling|wrestlemania)\b", title_lower):
                return "combattimento", "Wrestling e WWE"
            if re.search(r"\b(ufc|dwcs|dana\s*white)\b", title_lower):
                return "combattimento", "UFC"
            if re.search(r"\b(mma|bellator|pfl|one\s*championship|ksw|lfc)\b", title_lower):
                return "combattimento", "MMA"
            if re.search(r"\b(boxing|pugilato|bout|heavyweight|title\s*fight)\b", title_lower):
                return "combattimento", "Boxe"
            return "combattimento", "UFC"

        # ------------------------------------------------------------------
        # 9. ALTRI SPORT (Dedicated Catalog: 'altri_sport')
        # ------------------------------------------------------------------
        is_afl_match = (
            cat == "afl"
            or bool(re.search(r"\b(afl|aflw|australian\s*rules|footy)\b", title_lower))
            or (any(t in eval_text for t in ("carlton blues", "hawthorn hawks", "collingwood magpies", "richmond tigers", "geelong cats", "fremantle dockers", "essendon bombers", "sydney swans", "brisbane lions", "adelaide crows", "st kilda", "port adelaide power", "western bulldogs", "gold coast suns", "gws giants", "north melbourne kangaroos", "west coast eagles", "melbourne demons")))
        )

        if (
            cat in ("golf", "darts", "rugby", "afl", "cricket", "handball", "waterpolo", "table-tennis", "badminton", "billiards", "cycling", "altri_sport")
            or is_afl_match
            or any(k in title_lower for k in (
                "golf", "pga", "darts", "freccette", "rugby", "ciclismo", "cycling", "uci", "road race", "time trial", "snooker", "padel",
                "handball", "dhb pokal", "pallamano", "ihf", "ehf", "table tennis", "ping pong", "waterpolo", "pallanuoto", "badminton",
                "cricket", "test match", "one day international", "the hundred", "big bash", "ashes"
            ))
            or re.search(r"\b(cricket|odi|t20|twenty20|ipl|ashes|uci|ihf|ehf)\b", title_lower)
        ):
            if is_afl_match or re.search(r"\b(rugby|afl|aflw|six\s*nations|nrl|top\s*14|premiership\s*rugby)\b", title_lower) or cat in ("rugby", "afl"):
                return "altri_sport", "Rugby e AFL"
            if re.search(r"\b(cricket|odi|t20|twenty20|test\s*match|ipl|one\s*day\s*international|the\s*hundred|big\s*bash|ashes)\b", title_lower) or cat == "cricket":
                return "altri_sport", "Cricket"
            if re.search(r"\b(golf|pga|ryder\s*cup|presidents\s*cup|liv)\b", title_lower) or cat == "golf":
                return "altri_sport", "Golf"
            if re.search(r"\b(darts|freccette|pdc)\b", title_lower):
                return "altri_sport", "Freccette / Darts"
            if (re.search(r"\b(ciclismo|cycling|tour\s*de\s*france|giro\s*d['’]italia|vuelta|uci|road\s*race|time\s*trial)\b", title_lower) or cat == "cycling") and "hockey" not in title_lower:
                return "altri_sport", "Ciclismo"
            if re.search(r"\b(handball|pallamano|ihf|ehf|dhb\s*pokal)\b", title_lower) or cat == "handball":
                return "altri_sport", "Pallamano"
            return "altri_sport", "Biliardo, Padel e Altri"

        # ------------------------------------------------------------------
        # ------------------------------------------------------------------
        # 10. CALCIO (FOOTBALL / SOCCER)
        # ------------------------------------------------------------------
        # Guard: non-soccer sports (surf, sailing, boating, water sports, padel, billiards) must never enter football
        is_water_or_niche = bool(re.search(r"\b(surf|surfing|sailing|boating|water\s*sport|swimming|snooker|billiard|padel)\b", eval_text))
        is_explicit_other_cat = bool(cat and cat not in ("", "other", "unknown", "altri sport", "live sports", "football", "soccer"))
        
        # Candidate soccer match check: category/silo is football, or classic "vs" match with club indicators
        is_soccer_candidate = (
            not is_water_or_niche
            and not is_explicit_other_cat
            and (
                cat in ("football", "soccer")
                or silo == "football"
                or (
                    "vs" in title_lower
                    and any(k in eval_text for k in ("fc", "cf", "calcio", "football", "soccer", "serie", "league", "cup", "copa", "championship"))
                )
            )
        )

        if is_soccer_candidate:
            has_extra_eu_flag = any(flag in eval_text for flag in ("🇧🇷", "🇦🇷", "🇨🇴", "🇺🇾", "🇵🇾", "🇨🇱", "🇵🇪", "🇪🇨", "🇲🇽", "🇺🇸", "🇸🇦", "🇯🇵", "🇰🇷", "🇦🇺"))
            is_extra_eu = bool(self._EXTRA_EU_CONFED_REGEX.search(eval_text) or self._EXTRA_EU_LEAGUES_REGEX.search(eval_text) or has_extra_eu_flag)
            is_foreign = bool(self._FOREIGN_LEAGUE_REGEX.search(eval_text)) or is_extra_eu or "inter miami" in title_lower
            is_extra_eu_confed = bool(self._EXTRA_EU_CONFED_REGEX.search(eval_text))

            # --------------------------------------------------------------
            # 1. EUROPEAN CUPS & UEFA TOURNAMENTS (Champions, Europa, Conference)
            # Evaluated first because teams come from different nations
            # --------------------------------------------------------------
            is_uefa_cup = bool(re.search(r"\b(uefa\s*champions|champions\s*league|europa\s*league|conference\s*league|uefa\s*super\s*cup)\b", eval_text))
            if is_uefa_cup and not is_extra_eu_confed:
                if re.search(r"\b(champions\s*league|uefa\s*champions)\b", eval_text):
                    return "calcio_estero", "Champions League"
                return "calcio_estero", "Europa e Conference League"

            # --------------------------------------------------------------
            # 2. NATIONAL TEAMS & AMICHEVOLI (e.g. Italia vs Spagna, Nations League, Qualificazioni)
            # Evaluated before domestic leagues so "Italia vs Spagna" never enters Serie A
            # --------------------------------------------------------------
            is_national_match = (
                bool(self._FRIENDLY_REGEX.search(title_lower))
                or bool(re.search(r"\b(nations\s*league|fifa|qualif|international|world\s*cup|european\s*championship|african\s*cup|afcon|copa\s*america|asian\s*cup|gold\s*cup|intercontinental)\b", eval_text))
            )
            if is_national_match:
                return "calcio_estero", "Nazionali e Amichevoli"

            # Youth tournaments (international/club)
            if is_youth and not bool(re.search(r"\b(primavera|u23|next\s*gen|futuro)\b", eval_text)):
                return "calcio_estero", "Europei Under 21 e Nazionali Giovanili"

            # --------------------------------------------------------------
            # 3. EXTRA-EU & AMERICHE (CONMEBOL, AFC, CAF, MLS, Brasile, Perù, Argentina, ecc.)
            # Evaluated before European domestic leagues so Peruvian/Brazilian leagues route cleanly
            # --------------------------------------------------------------
            if is_extra_eu_confed or bool(self._EXTRA_EU_LEAGUES_REGEX.search(eval_text)) or has_extra_eu_flag:
                return "calcio_estero", "Americhe e Leghe Extra-UE"

            # --------------------------------------------------------------
            # 4. CALCIO ITALIANO (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
            # --------------------------------------------------------------
            is_ita_nation = (
                country in ("italia", "italy")
                or bool(re.search(r"\b(italy|italia|italian)\b", eval_text))
                or bool(re.search(r"\b(coppa\s*italia|supercoppa\s*italiana|primavera)\b", eval_text))
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
                if re.search(r"\bserie\s*b\b", eval_text):
                    return "calcio_italiano", "Serie B"
                if re.search(r"\bserie\s*a\b", eval_text):
                    return "calcio_italiano", "Serie A"
                # Fallback for Italian clubs/derbies not in Serie A/B/C
                return "calcio_italiano", "Serie A"

            # --------------------------------------------------------------
            # 4. PREMIER LEAGUE (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
            # Gate 1: Country is England/UK OR explicit English indicator in text
            # Gate 2: League is Premier League / FA Cup / Carabao
            # --------------------------------------------------------------
            is_england = (
                country in ("inghilterra", "england", "uk")
                or bool(re.search(r"\b(england|inghilterra|uk|epl)\b", eval_text))
            )
            if is_england and not is_extra_eu:
                if re.search(r"\b(premier\s*league|epl|fa\s*cup|carabao|efl\s*cup)\b", eval_text):
                    return "calcio_estero", "Premier League"

            # --------------------------------------------------------------
            # 5. LA LIGA (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
            # Gate 1: Country is Spain OR explicit Spain indicator in text
            # Gate 2: League is LaLiga / Primera Division / Copa del Rey
            # --------------------------------------------------------------
            is_spain = (
                country in ("spagna", "spain")
                or bool(re.search(r"\b(spain|spagna|espana|españa|laliga)\b", eval_text))
            )
            if is_spain and not is_extra_eu:
                if re.search(r"\b(laliga|la\s*liga|primera\s*division|copa\s*del\s*rey)\b", eval_text):
                    return "calcio_estero", "La Liga"

            # --------------------------------------------------------------
            # 6. BUNDESLIGA E LIGUE 1 (DOPPIO FILTRO)
            # --------------------------------------------------------------
            is_germany = (
                country in ("germania", "germany")
                or bool(re.search(r"\b(germany|germania|deutschland)\b", eval_text))
            )
            is_france = (
                country in ("francia", "france")
                or bool(re.search(r"\b(france|francia)\b", eval_text))
            )
            if (is_germany or is_france) and not is_extra_eu:
                if re.search(r"\b(bundesliga|dfb\s*pokal|ligue\s*1|coupe\s*de\s*france)\b", eval_text):
                    return "calcio_estero", "Bundesliga e Ligue 1"

            # --------------------------------------------------------------
            # 7. EXTRA-EU & AMERICHE
            # --------------------------------------------------------------
            if is_extra_eu_confed or bool(self._EXTRA_EU_LEAGUES_REGEX.search(eval_text)) or has_extra_eu_flag:
                return "calcio_estero", "Americhe e Leghe Extra-UE"

            # --------------------------------------------------------------
            # 8. TUTTI GLI ALTRI CAMPIONATI EUROPEI (Fallback naturale europeo)
            # --------------------------------------------------------------
            return "calcio_estero", "Altri Campionati Europei"

        # ------------------------------------------------------------------
        # 11. FALLBACK
        # ------------------------------------------------------------------
        return "altri_sport", "Biliardo, Padel e Altri"

    def classify_genre(self, match: Dict[str, Any]) -> str:
        """Helper returning only the genre string for backward compatibility."""
        _, genre = self.classify(match)
        return genre

genre_classifier = GenreClassifier()
