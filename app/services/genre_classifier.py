import re
from typing import Any, Dict, Optional, Tuple

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
        r"brazil|brasil|brasileir[ao]|serie\s*a\s*brazil|copa\s*do\s*brasil|paulista|carioca|"
        r"argentina|primera\s*division\s*argentina|copa\s*de\s*la\s*liga|"
        r"colombia|chile|peru|uruguay|ecuador|paraguay|venezuela|bolivia|costa\s*rica|"
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

    # Serie A Teams
    SERIE_A_TEAMS = {
        "atalanta", "bologna", "cagliari", "como", "empoli", "fiorentina",
        "genoa", "hellas verona", "verona", "inter", "internazionale",
        "juventus", "lazio", "lecce", "milan", "ac milan", "monza",
        "napoli", "parma", "roma", "as roma", "torino", "udinese", "venezia"
    }

    # Serie B Teams
    SERIE_B_TEAMS = {
        "bari", "brescia", "carrarese", "catanzaro", "cesena", "cittadella",
        "cosenza", "cremonese", "frosinone", "juve stabia", "mantova",
        "modena", "palermo", "pisa", "reggiana", "salernitana", "sampdoria",
        "sassuolo", "spezia", "sudtirol", "virtus entella"
    }

    # Serie C Teams
    SERIE_C_TEAMS = {
        "avellino", "benevento", "catania", "foggia", "trapani", "padova", "vicenza",
        "triestina", "spal", "perugia", "pescara", "torres", "monopoli", "audace cerignola",
        "cerignola", "crotone", "casertana", "potenza", "picerno", "sorrento", "cavese",
        "taranto", "turris", "latina", "messina", "team altamura", "altamura", "giugliano",
        "atalanta u23", "juventus next gen", "milan futuro", "albinoleffe", "alcione milano",
        "arzignano", "caldiero", "feralpisalo", "giana erminio", "lecco", "lumezzane",
        "novara", "pro patria", "pro vercelli", "renate", "trento", "virtus verona",
        "arezzo", "campobasso", "carpi", "gubbio", "legnago", "lucchese", "pianese",
        "pineto", "pontedera", "rimini", "sestri levante", "ternana", "vis pesaro",
        "clodiense"
    }

    # Top European Leagues
    PREMIER_LEAGUE_TEAMS = {
        "arsenal", "aston villa", "bournemouth", "brentford", "brighton",
        "chelsea", "crystal palace", "everton", "fulham", "ipswich", "ipswich town",
        "leicester", "leicester city", "liverpool", "manchester city", "man city",
        "manchester united", "man united", "man utd", "newcastle", "newcastle united",
        "nottingham forest", "southampton", "tottenham", "tottenham hotspur",
        "spurs", "west ham", "west ham united", "wolverhampton", "wolves"
    }

    LALIGA_TEAMS = {
        "alaves", "athletic bilbao", "athletic club", "atletico madrid", "barcelona",
        "celta vigo", "celta", "espanyol", "getafe", "girona", "las palmas", "leganes",
        "mallorca", "osasuna", "rayo vallecano", "real betis", "betis",
        "real madrid", "real sociedad", "sevilla", "valencia", "valladolid", "villarreal"
    }

    BUNDESLIGA_TEAMS = {
        "augsburg", "bayer leverkusen", "leverkusen", "bayern munich", "bayern munchen",
        "bayern", "bochum", "borussia dortmund", "dortmund", "borussia monchengladbach",
        "monchengladbach", "eintracht frankfurt", "frankfurt", "freiburg", "heidenheim",
        "hoffenheim", "holstein kiel", "kiel", "mainz", "rb leipzig", "leipzig",
        "st pauli", "st. pauli", "stuttgart", "union berlin", "werder bremen", "wolfsburg"
    }

    LIGUE1_TEAMS = {
        "angers", "auxerre", "brest", "le havre", "lens", "lille", "lyon", "olympique lyonnais",
        "marseille", "olympique marseille", "monaco", "as monaco", "montpellier",
        "nantes", "nice", "psg", "paris saint-germain", "paris sg", "reims",
        "rennes", "saint-etienne", "strasbourg", "toulouse"
    }

    # NBA Franchises
    NBA_TEAMS = {
        "hawks", "celtics", "nets", "hornets", "bulls", "cavaliers", "mavericks",
        "nuggets", "pistons", "warriors", "rockets", "pacers", "clippers", "lakers",
        "grizzlies", "heat", "bucks", "timberwolves", "pelicans", "knicks", "thunder",
        "magic", "76ers", "sixers", "suns", "trail blazers", "blazers", "kings",
        "spurs", "raptors", "jazz", "wizards"
    }

    # WNBA Franchises
    WNBA_TEAMS = {
        "liberty", "new york liberty", "mercury", "phoenix mercury", "mystics", "washington mystics",
        "sparks", "los angeles sparks", "chicago sky", "fever", "indiana fever", "minnesota lynx", "lynx",
        "las vegas aces", "aces", "seattle storm", "storm", "valkyries", "golden state valkyries",
        "dallas wings", "wings", "connecticut sun", "sun", "atlanta dream", "dream", "toronto tempo"
    }

    # NBL Franchises (Australia / NZ)
    NBL_TEAMS = {
        "adelaide 36ers", "36ers", "brisbane bullets", "bullets", "cairns taipans", "taipans",
        "illawarra hawks", "melbourne united", "new zealand breakers", "breakers",
        "perth wildcats", "wildcats", "south east melbourne phoenix", "sydney kings",
        "tasmania jackjumpers", "jackjumpers"
    }

    # LBA Basket Italiano
    LBA_BASKET_TEAMS = {
        "olimpia milano", "ea7 milano", "virtus bologna", "segafredo bologna", "rewer venezia",
        "venezia", "dinamo sassari", "sassari", "germani brescia", "brescia basket", "aquila trento",
        "trento", "pallacanestro varese", "varese", "derthona", "tortona", "unahotels reggio emilia",
        "reggiana", "pistoia", "givova scafati", "scafati", "vanoli cremona", "trapani shark",
        "napoli basket", "gevi napoli", "pallacanestro trieste", "trieste", "cantù", "fortitudo"
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
        cat = (match.get("category") or "").lower().strip()
        silo = (match.get("_silo") or "").lower().strip()
        comp = (match.get("competition") or match.get("_competition") or "").lower().strip()
        title = (match.get("title") or "").strip()
        title_lower = title.lower()
        eval_text = f"{title_lower} {comp}".strip()

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

        is_women = bool(self._WOMEN_REGEX.search(title_lower))
        is_youth = bool(self._YOUTH_REGEX.search(title_lower))

        # Guard: Cue sports (Snooker, Q Tour, Billiards) often mislabeled upstream
        if re.search(r"\b(snooker|q\s*tour|billiards|biliardo)\b", title_lower):
            return "altri_sport", "Biliardo, Padel e Altri"

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
            cat == "basketball"
            or "basketball" in title_lower
            or "nba" in title_lower
            or "euroleague" in title_lower
            or "wnba" in title_lower
            or "nbl" in title_lower
        ):
            # A) WNBA & Basket Femminile
            is_wnba_title = bool(re.search(r"\b(wnba)\b", title_lower)) or any(t in title_lower for t in self.WNBA_TEAMS)
            if is_wnba_title or (is_women and "nba" not in title_lower):
                return "basket", "WNBA e Femminile"

            # B) FIBA, Intercontinental Cup, Nazionali & 3x3
            if re.search(r"\b(fiba|intercontinental\s*cup|coppa\s*intercontinentale|asian\s*games|mondiali|world\s*cup|eurobasket|qualif|national\s*team|3x3|caravan)\b", title_lower):
                return "basket", "FIBA e Tornei Nazionali"

            # C) Eurolega & Eurocup (Coppe Europee)
            if re.search(r"\b(euroleague|eurocup|eurolega|bcl|basketball\s*champions|fiba\s*europe\s*cup)\b", title_lower):
                return "basket", "Eurolega ed Eurocup"

            # D) LBA Serie A (Italiana)
            if re.search(r"\b(lba|serie\s*a\s*basket|lega\s*a|coppa\s*italia\s*basket|serie\s*a2)\b", title_lower) or any(t in home or t in away or t in title_lower for t in self.LBA_BASKET_TEAMS):
                return "basket", "LBA Serie A"

            # E) NCAA & College Basket
            if re.search(r"\b(ncaa|college\s*basketball|march\s*madness)\b", title_lower):
                return "basket", "NCAA e College Basket"

            # F) Campionati Esteri & NBL
            if "nbl" in title_lower or any(t in title_lower for t in self.NBL_TEAMS) or re.search(r"\b(acb|endesa|cba|super\s*cup|skl|bsl|bbl|liga\s*femenina|pro\s*a|aba\s*league)\b", title_lower):
                return "basket", "Campionati Esteri ed NBL"

            # G) NBA (Men's professional USA league)
            if re.search(r"\b(nba)\b", title_lower) or any(t in home or t in away or t in title_lower for t in self.NBA_TEAMS):
                return "basket", "NBA"

            # Default fallback for unlisted foreign basketball games
            return "basket", "Campionati Esteri ed NBL"

        # ------------------------------------------------------------------
        # 4. VOLLEY (Dedicated Catalog: 'volley')
        # ------------------------------------------------------------------
        if cat in ("volleyball", "volley") or any(k in title_lower for k in ("volleyball", "pallavolo", "superlega")):
            if is_women or "femminile" in title_lower:
                return "volley", "Volley Femminile"
            if re.search(r"\b(champions|cev)\b", title_lower):
                return "volley", "Champions League Volley"
            if re.search(r"\b(superlega|serie\s*a1)\b", title_lower) or any(t in home or t in away or t in title_lower for t in self.SUPERLEGA_VOLLEY_TEAMS):
                return "volley", "Superlega e Serie A1"
            if re.search(r"\b(nazionale|vnl|mondiali|europei)\b", title_lower):
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
        if cat in ("hockey", "ice-hockey", "ice hockey") or "nhl" in title_lower:
            if re.search(r"\b(nhl|stanley\s*cup)\b", title_lower) or any(t in home or t in away or t in title_lower for t in self.NHL_TEAMS):
                return "hockey", "NHL"
            if re.search(r"\b(khl|del|liiga|shl)\b", title_lower):
                return "hockey", "KHL e Leghe Europee"
            if re.search(r"\b(iihf|mondiali|olympics)\b", title_lower):
                return "hockey", "Mondiali e Nazionali"
            return "hockey", "NHL"

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
        if (
            cat in ("golf", "darts", "rugby", "afl", "cricket", "handball", "waterpolo", "table-tennis", "badminton", "billiards", "cycling")
            or any(k in title_lower for k in (
                "golf", "pga", "darts", "freccette", "rugby", "ciclismo", "cycling", "uci", "road race", "time trial", "snooker", "padel",
                "handball", "dhb pokal", "pallamano", "ihf", "ehf", "table tennis", "ping pong", "waterpolo", "pallanuoto", "badminton",
                "cricket", "test match", "one day international", "the hundred", "big bash", "ashes"
            ))
            or re.search(r"\b(cricket|odi|t20|twenty20|ipl|ashes|uci|ihf|ehf)\b", title_lower)
        ):
            if re.search(r"\b(cricket|odi|t20|twenty20|test\s*match|ipl|one\s*day\s*international|the\s*hundred|big\s*bash|ashes)\b", title_lower) or cat == "cricket":
                return "altri_sport", "Cricket"
            if re.search(r"\b(golf|pga|ryder\s*cup|presidents\s*cup|liv)\b", title_lower) or cat == "golf":
                return "altri_sport", "Golf"
            if re.search(r"\b(darts|freccette|pdc)\b", title_lower):
                return "altri_sport", "Freccette / Darts"
            if re.search(r"\b(rugby|afl|six\s*nations|nrl|top\s*14)\b", title_lower) or cat == "rugby":
                return "altri_sport", "Rugby e AFL"
            if re.search(r"\b(ciclismo|cycling|tour\s*de\s*france|giro\s*d['’]italia|vuelta|uci|road\s*race|time\s*trial)\b", title_lower) or cat == "cycling":
                return "altri_sport", "Ciclismo"
            if re.search(r"\b(handball|pallamano|ihf|ehf|dhb\s*pokal)\b", title_lower) or cat == "handball":
                return "altri_sport", "Pallamano"
            return "altri_sport", "Biliardo, Padel e Altri"

        # ------------------------------------------------------------------
        # 10. CALCIO (FOOTBALL / SOCCER)
        # ------------------------------------------------------------------
        if cat in ("football", "soccer") or silo == "football" or "vs" in title_lower or any(k in title_lower for k in ("fc ", "cf ", "sc ", "ac ", "league", "copa", "cup")):
            is_extra_eu = bool(self._EXTRA_EU_CONFED_REGEX.search(eval_text) or self._EXTRA_EU_LEAGUES_REGEX.search(eval_text))
            is_foreign = bool(self._FOREIGN_LEAGUE_REGEX.search(eval_text)) or is_extra_eu or "inter miami" in title_lower
            is_extra_eu_confed = bool(self._EXTRA_EU_CONFED_REGEX.search(eval_text))

            # Team match checks for Italian leagues (MUST use word boundaries to avoid 'kostroma' matching 'roma', and exclude 'inter miami')
            home_ita_check = re.sub(r"\binter\s*miami\b", "", home).strip()
            away_ita_check = re.sub(r"\binter\s*miami\b", "", away).strip()
            all_ita_teams = self.SERIE_A_TEAMS | self.SERIE_B_TEAMS | self.SERIE_C_TEAMS
            is_ita_home = bool(home_ita_check) and (home_ita_check in all_ita_teams or any(re.search(rf"\b{re.escape(t)}\b", home_ita_check) for t in all_ita_teams))
            is_ita_away = bool(away_ita_check) and (away_ita_check in all_ita_teams or any(re.search(rf"\b{re.escape(t)}\b", away_ita_check) for t in all_ita_teams))
            has_explicit_ita_keyword = bool(re.search(r"\b(serie\s*a|serie\s*b|serie\s*c|coppa\s*italia|supercoppa\s*italiana|primavera)\b", title_lower)) or "italy -" in title_lower or "italia -" in title_lower
            is_italian = (is_ita_home or is_ita_away or has_explicit_ita_keyword) and not is_foreign and not is_extra_eu

            top_leagues = [
                ("Serie A", self.SERIE_A_TEAMS),
                ("Premier League", self.PREMIER_LEAGUE_TEAMS),
                ("LaLiga", self.LALIGA_TEAMS),
                ("Bundesliga", self.BUNDESLIGA_TEAMS),
                ("Ligue 1", self.LIGUE1_TEAMS),
            ]
            home_leagues = [name for name, teams in top_leagues if home in teams]
            away_leagues = [name for name, teams in top_leagues if away in teams]

            # Cross-league (e.g. Manchester City [Premier] vs Inter [Serie A]) -> Champions League / Coppe
            is_cross_league = bool(home_leagues and away_leagues and home_leagues[0] != away_leagues[0])
            is_euro_cup = (
                (bool(re.search(r"\b(champions\s*league|europa\s*league|conference\s*league|uefa\s*super\s*cup|uefa)\b", title_lower)) or is_cross_league)
                and not is_extra_eu_confed
            )

            # ---------------------------
            # A) CALCIO ITALIANO
            # ---------------------------
            # Distinguish Italian club youth (Primavera, U23, Next Gen, Futuro) vs National Youth (Italy U21)
            is_club_youth = bool(re.search(r"\b(primavera|u23|next\s*gen|futuro|allievi|giovanissimi)\b", title_lower))
            is_national_youth = is_youth and not is_club_youth

            if is_italian and not is_euro_cup and not is_national_youth and not self._FRIENDLY_REGEX.search(title_lower):
                if is_women:
                    return "calcio_italiano", "Calcio Femminile"
                if is_youth:
                    return "calcio_italiano", "Primavera e Giovanili"
                if re.search(r"\b(coppa\s*italia|supercoppa\s*italiana)\b", title_lower):
                    return "calcio_italiano", "Coppa Italia e Supercoppa"
                if re.search(r"\b(serie\s*c|italy\s*-\s*serie\s*c)\b", title_lower) or home in self.SERIE_C_TEAMS or away in self.SERIE_C_TEAMS or any(re.search(rf"\b{re.escape(t)}\b", home) or re.search(rf"\b{re.escape(t)}\b", away) for t in self.SERIE_C_TEAMS):
                    return "calcio_italiano", "Serie C"
                if re.search(r"\b(serie\s*b|italy\s*-\s*serie\s*b)\b", title_lower) or home in self.SERIE_B_TEAMS or away in self.SERIE_B_TEAMS or any(re.search(rf"\b{re.escape(t)}\b", home) or re.search(rf"\b{re.escape(t)}\b", away) for t in self.SERIE_B_TEAMS):
                    return "calcio_italiano", "Serie B"
                if re.search(r"\b(serie\s*a|italy\s*-\s*serie\s*a)\b", title_lower) or home in self.SERIE_A_TEAMS or away in self.SERIE_A_TEAMS or any(re.search(rf"\b{re.escape(t)}\b", home) or re.search(rf"\b{re.escape(t)}\b", away) for t in self.SERIE_A_TEAMS):
                    return "calcio_italiano", "Serie A"
                # If neither Serie A, B nor C team matches, it is NOT Italian Serie A!
                return "calcio_estero", "Altri Campionati Europei"

            # ---------------------------
            # B) CALCIO INTERNAZIONALE E COPPE
            # ---------------------------
            # 1. International Youth & Under-21 tournaments
            if is_youth:
                return "calcio_estero", "Europei Under 21 e Nazionali Giovanili"

            # 2. National teams & friendlies
            is_national_match = (
                bool(self._FRIENDLY_REGEX.search(title_lower))
                or bool(re.search(r"\b(nations\s*league|fifa|qualif|international|world\s*cup|european\s*championship|african\s*cup|afcon|copa\s*america|asian\s*cup|gold\s*cup|intercontinental)\b", title_lower))
            )
            if is_national_match:
                return "calcio_estero", "Nazionali e Amichevoli"

            # 3. North/South American & Extra-EU Soccer Leagues
            if is_extra_eu_confed or bool(self._EXTRA_EU_LEAGUES_REGEX.search(eval_text)):
                return "calcio_estero", "Americhe e Leghe Extra-UE"

            # 4. European Cups (UEFA Champions, Europa, Conference League)
            if is_euro_cup or bool(re.search(r"\b(uefa\s*champions|champions\s*league)\b", eval_text)):
                return "calcio_estero", "Champions League"
            if re.search(r"\b(europa\s*league|conference\s*league)\b", eval_text):
                return "calcio_estero", "Europa e Conference League"
            if re.search(r"\b(premier\s*league|epl|england\s*-\s*premier)\b", eval_text) or home in self.PREMIER_LEAGUE_TEAMS or away in self.PREMIER_LEAGUE_TEAMS:
                return "calcio_estero", "Premier League"
            if re.search(r"\b(laliga|la\s*liga|spain\s*-\s*laliga|primera\s*division)\b", eval_text) or home in self.LALIGA_TEAMS or away in self.LALIGA_TEAMS:
                return "calcio_estero", "La Liga"
            if re.search(r"\b(bundesliga|germany\s*-\s*bundesliga|ligue\s*1|france\s*-\s*ligue\s*1)\b", eval_text) or home in self.BUNDESLIGA_TEAMS or away in self.BUNDESLIGA_TEAMS or home in self.LIGUE1_TEAMS or away in self.LIGUE1_TEAMS:
                return "calcio_estero", "Bundesliga e Ligue 1"

            # 5. All other European and international club football defaults to "Altri Campionati Europei"
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
