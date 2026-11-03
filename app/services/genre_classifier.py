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
        r"\b(women(?:[’']s)?|femminile|ladies|frauen|féminine|femenino|donne|dames|damen|w-|wta|uwcl)\b|(?<=\w)\s+[dwf]\b|\([dwf]\)",
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
        comp = (match.get("competition") or match.get("_competition") or match.get("league") or "").lower().strip()
        country = (match.get("country") or match.get("_country") or "").lower().strip()
        raw_title_lower = (match.get("raw_title") or "").strip().lower()
        eval_text = f"{title_lower} {country} {comp} {raw_title_lower}".strip()
        is_women = bool(match.get("is_women")) or bool(self._WOMEN_REGEX.search(eval_text))
        is_youth = bool(match.get("is_youth")) or bool(self._YOUTH_REGEX.search(eval_text))

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
            event_silo = (match.get("_silo") or match.get("category") or match.get("silo") or "").lower().strip()
            if event_silo in ("soccer",): event_silo = "football"
            if event_silo in ("basket",): event_silo = "basketball"
            if event_silo in ("motorsport", "motori"): event_silo = "motor-sports"
            if event_silo in ("volleyball",): event_silo = "volley"
            if event_silo in ("combattimento", "boxe", "mma"): event_silo = "fight"
            DEDICATED_SILOS = {"hockey", "basketball", "tennis", "motor-sports", "volley", "baseball", "fight", "american-football"}
            if event_silo in DEDICATED_SILOS and canon_silo and canon_silo != event_silo:
                canon = None

        if canon:
            # Women & Youth safety guards on canonical routing:
            # A men's senior canonical competition must NEVER hijack a women or youth event!
            if is_women:
                if canon.startswith("football_"):
                    country_clean = (match.get("country") or "").strip().lower()
                    is_ita = country_clean in ("italia", "italy") or "🇮🇹" in eval_text or canon == "football_serie_a_women"
                    if is_ita:
                        return "calcio_italiano", "Calcio Femminile"
                    if canon == "football_uwcl" or bool(re.search(r"\b(uwcl|champions\s*league|uefa)\b", eval_text)):
                        return "calcio_estero", "Champions League Femminile"
                    if bool(self._EXTRA_EU_CONFED_REGEX.search(eval_text) or self._EXTRA_EU_LEAGUES_REGEX.search(eval_text)):
                        return "calcio_estero", "Americhe e Leghe Extra-UE"
                    return "calcio_estero", "Altri Campionati Europei" 
                if canon.startswith("volleyball_"):
                    return "volley", "Volley Femminile"
                if canon.startswith("basketball_"):
                    return "basket", "WNBA e Femminile"
                if canon.startswith("tennis_"):
                    return "tennis", "WTA"
            if is_youth:
                # Never hijack senior professional Serie C matches (e.g. Atalanta U23, Juventus Next Gen, Milan Futuro)
                if canon == "football_serie_c":
                    pass
                elif canon.startswith("football_"):
                    country_clean = (match.get("country") or "").strip().lower()
                    is_ita_youth = (
                        (country_clean in ("italia", "italy") or "🇮🇹" in eval_text)
                        and not bool(self._FOREIGN_LEAGUE_REGEX.search(eval_text))
                    ) or bool(re.search(r"\b(primavera|allievi|giovanissimi)\b", eval_text))
                    if is_ita_youth:
                        return "calcio_italiano", "Primavera e Giovanili"
                    return "calcio_estero", "Europei Under 21 e Nazionali Giovanili"

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
                "football_fa_cup": ("calcio_estero", "Premier League"),
                "football_championship": ("calcio_estero", "Altri Campionati Europei"),
                "football_la_liga": ("calcio_estero", "La Liga"),
                "football_copa_del_rey": ("calcio_estero", "La Liga"),
                "football_segunda": ("calcio_estero", "Altri Campionati Europei"),
                "football_bundesliga": ("calcio_estero", "Bundesliga e Ligue 1"),
                "football_dfb_pokal": ("calcio_estero", "Bundesliga e Ligue 1"),
                "football_austrian_bundesliga": ("calcio_estero", "Altri Campionati Europei"),
                "football_danish_superliga": ("calcio_estero", "Altri Campionati Europei"),
                "football_swiss_super_league": ("calcio_estero", "Altri Campionati Europei"),
                "football_2_bundesliga": ("calcio_estero", "Altri Campionati Europei"),
                "football_ligue_1": ("calcio_estero", "Bundesliga e Ligue 1"),
                "football_uefa_youth_league": ("calcio_estero", "Europei Under 21 e Nazionali Giovanili"),
                "football_uwcl": ("calcio_estero", "Champions League Femminile"),
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
                "basketball_serie_a2": ("basket", "LBA Serie A"),
                "basketball_serie_a1_femm": ("basket", "WNBA e Femminile"),
                "basketball_serie_a2_femm": ("basket", "WNBA e Femminile"),
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
                "motorsport_wec": ("motori", "NASCAR e IndyCar"),
                "motorsport_rally_wrc": ("motori", "Rally e WRC"),
                "am_football_nfl": ("football_americano", "NFL"),
                "am_football_ncaa": ("football_americano", "NCAA College Football"),
                "am_football_cfl": ("football_americano", "CFL e Altre Leghe"),
                "am_football_ufl": ("football_americano", "CFL e Altre Leghe"),
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
                "volleyball_superlega": ("volley", "Superlega e Serie A1"),
                "volleyball_serie_a2": ("volley", "Superlega e Serie A1"),
                "volleyball_serie_a1_femm": ("volley", "Volley Femminile"),
                "volleyball_serie_a2_femm": ("volley", "Volley Femminile"),
                "volleyball_cev_cl": ("volley", "Champions League Volley"),
                "rugby_serie_a_elite": ("altri_sport", "Rugby e AFL"),
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

        # 2. Canonical Sport Silo Resolution
        resolved_silo = self.resolve_sport_silo(match, eval_text)

        # 3. Dedicated Silo Dispatch (Clean & Modular)
        if resolved_silo == "tennis":
            return self._classify_tennis(match, eval_text, is_women)
        elif resolved_silo == "motor-sports":
            return self._classify_motorsport(match, eval_text)
        elif resolved_silo == "basketball":
            return self._classify_basketball(match, eval_text, is_women, is_youth)
        elif resolved_silo == "volley":
            return self._classify_volleyball(match, eval_text, is_women)
        elif resolved_silo == "american-football":
            return self._classify_american_football(match, eval_text, is_youth)
        elif resolved_silo == "baseball":
            return self._classify_baseball(match, eval_text, is_youth)
        elif resolved_silo == "hockey":
            return self._classify_hockey(match, eval_text)
        elif resolved_silo == "fight":
            return self._classify_fight(match, eval_text)
        elif resolved_silo == "altri_sport":
            return self._classify_altri_sport(match, eval_text)
        elif resolved_silo == "football":
            return self._classify_football(match, eval_text, is_women, is_youth)

        return "altri_sport", "Biliardo, Padel e Altri"

    def resolve_sport_silo(self, match: Dict[str, Any], eval_text: str) -> str:
        """
        Single canonical method resolving the sport silo.
        Eliminates fragmented patchwork guards across individual sport blocks.
        """
        cat = (match.get("category") or "").lower().strip()
        silo = (match.get("_silo") or match.get("silo") or "").lower().strip()
        sport = (match.get("sport") or "").lower().strip()

        # 1. Direct explicit category/silo mapping
        for val in (silo, cat, sport):
            if val in ("tennis",):
                return "tennis"
            if val in ("motor-sports", "motorsports", "motorsport", "motori"):
                return "motor-sports"
            if val in ("basketball", "basket"):
                return "basketball"
            if val in ("volleyball", "volley"):
                return "volley"
            if val in ("american-football", "american_football") or ("football" in val and val != "football"):
                return "american-football"
            if val in ("baseball",):
                return "baseball"
            if val in ("hockey", "ice-hockey", "ice hockey"):
                return "hockey"
            if val in ("fight", "combattimento", "boxe", "boxing", "mma", "ufc", "wrestling"):
                return "fight"
            if val in (
                "golf", "cycling", "ciclismo", "rugby", "afl", "aussie-rules",
                "cricket", "darts", "handball", "pallamano", "waterpolo",
                "table-tennis", "badminton", "billiards", "snooker", "padel",
                "squash", "altri_sport"
            ):
                return "altri_sport"

        # 2. Football explicit OR match with soccer league indicators
        is_youth = bool(match.get("is_youth")) or bool(self._YOUTH_REGEX.search(eval_text))
        is_football_candidate = (
            cat in ("football", "soccer")
            or silo == "football"
            or "calcio" in sport
            or (
                ("vs" in eval_text or " - " in eval_text)
                and (
                    any(k in eval_text for k in ("fc", "cf", "calcio", "football", "soccer", "serie", "league", "cup", "copa", "championship", "campionato", "torneo", "division", "trofeo", "amichevole", "friendly", "primavera"))
                    or is_youth
                    or bool(self._EUROPEAN_LEAGUES_REGEX.search(eval_text))
                    or bool(self._EXTRA_EU_LEAGUES_REGEX.search(eval_text))
                    or bool(self._FOREIGN_LEAGUE_REGEX.search(eval_text))
                )
            )
        )
        if is_football_candidate:
            # Guard against other sports mislabeled under generic terms or 'vs' with tournament tokens
            if re.search(r"\b(snooker|q\s*tour|billiards|biliardo|padel|golf|pga|lpga|ryder\s*cup|masters\s*tournament|darts|freccette|pdc|rugby|six\s*nations|sei\s*nazioni|nrl|top\s*14|premiership\s*rugby|urc|super\s*rugby|afl|aflw|cricket|odi|t20|twenty20|test\s*match|ipl|one\s*day\s*international|the\s*hundred|big\s*bash|ashes|ciclismo|cycling|tour\s*de\s*france|giro\s*d['’]italia|vuelta|uci|road\s*race|time\s*trial|handball|pallamano|ehf|ihf|dhb\s*pokal|waterpolo|pallanuoto|table\s*tennis|ping\s*pong|badminton)\b", eval_text):
                return "altri_sport"
            return "football"

        # 3. Unlabeled / Generic items: inspect eval_text for definitive sport tokens
        if re.search(r"\b(formula\s*1|formula\s*one|f1|motogp|moto\s*gp|moto2|moto3|superbike|wsbk|nascar|indycar|rally|wrc|dakar)\b", eval_text):
            return "motor-sports"
        if re.search(r"\b(atp|wta|bjk|davis\s*cup|wimbledon|us\s*open|roland\s*garros|australian\s*open|french\s*open)\b", eval_text):
            return "tennis"
        if re.search(r"\b(nba|wnba|euroleague|eurocup|lba|nbl|fiba|cba)\b", eval_text):
            return "basketball"
        if re.search(r"\b(ufc|wwe|aew|bellator|boxing|boxe|mma|pfl|one\s*championship)\b", eval_text):
            return "fight"
        # Note: DEL requires hockey context so 'Corea del Nord' or 'Giro del Trentino' never triggers hockey!
        if re.search(r"\b(nhl|khl|shl|liiga|extraliga|ice\s*hockey)\b", eval_text) or (re.search(r"\bdel\b", eval_text) and any(k in eval_text for k in ("hockey", "eishockey"))):
            return "hockey"
        if re.search(r"\b(nfl|cfl|ufl|cfb|college\s*football|american\s*football|super\s*bowl)\b", eval_text):
            return "american-football"
        if re.search(r"\b(baseball|mlb|npb|kbo|lmb)\b", eval_text):
            return "baseball"
        if re.search(r"\b(volleyball|volley|pallavolo|superlega|plusliga)\b", eval_text):
            return "volley"
        if re.search(r"\b(golf|pga|lpga|darts|freccette|rugby|six\s*nations|afl|cricket|odi|t20|ciclismo|cycling|tour\s*de\s*france|giro\s*d['’]italia|handball|pallamano|snooker|padel)\b", eval_text):
            return "altri_sport"

        return "altri_sport"

    def _classify_tennis(self, match: Dict[str, Any], eval_text: str, is_women: bool) -> Tuple[str, str]:
        if re.search(r"\b(wimbledon|us\s*open|roland\s*garros|australian\s*open|french\s*open|slam)\b", eval_text):
            return "tennis", "Grandi Slam"
        if re.search(r"\b(davis|bjk|billie\s*jean|laver\s*cup|united\s*cup|hopman\s*cup)\b", eval_text):
            return "tennis", "Coppa Davis e BJK Cup"
        if "atp" in eval_text and "wta" in eval_text:
            return "tennis", "Challenger e Altri"
        if is_women or "wta" in eval_text or re.search(r"\b(women|femminile|femenino)\b", eval_text):
            return "tennis", "WTA"
        if "atp" in eval_text:
            return "tennis", "ATP"
        if re.search(r"\b(challenger|itf|exhibition|esibizione|uts|ultimate\s*tennis)\b", eval_text):
            return "tennis", "Challenger e Altri"
        return "tennis", "ATP"

    def _classify_motorsport(self, match: Dict[str, Any], eval_text: str) -> Tuple[str, str]:
        if re.search(r"\b(f1|formula\s*1|formula\s*one)\b", eval_text):
            return "motori", "Formula 1"
        if re.search(r"\b(motogp|moto\s*gp|moto2|moto3|superbike|wsbk|worldsbk|civ)\b", eval_text):
            return "motori", "MotoGP e Superbike"
        if re.search(r"\b(rally|wrc|dakar|erc)\b", eval_text):
            return "motori", "Rally e WRC"
        if re.search(r"\b(nascar|indycar|indy\s*500|supercars|dtm|le\s*mans|wec|imsa|gt\s*world|bathurst)\b", eval_text):
            return "motori", "NASCAR e IndyCar"
        return "motori", "NASCAR e IndyCar"

    def _classify_basketball(self, match: Dict[str, Any], eval_text: str, is_women: bool, is_youth: bool) -> Tuple[str, str]:
        # 1. WNBA & Basket Femminile
        if is_women or re.search(r"\b(wnba|liga\s*femenina|tauihi|wlb|women|femminile|femenino|wbbl|wnbl)\b", eval_text):
            return "basket", "WNBA e Femminile"

        # 2. FIBA, 3x3 e Tornei Nazionali
        if re.search(r"\b(fiba|3x3|world\s*tour|world\s*cup|mondiali|eurobasket|qualif|national\s*team|intercontinental\s*cup|afrobasket|americup|asia\s*cup)\b", eval_text):
            return "basket", "FIBA e Tornei Nazionali"

        # 3. Eurolega & Eurocup
        if re.search(r"\b(euroleague|eurocup|eurolega|bcl|basketball\s*champions|fiba\s*europe\s*cup)\b", eval_text):
            return "basket", "Eurolega ed Eurocup"

        # 4. NCAA & College Basket
        if is_youth or re.search(r"\b(ncaa|college\s*basketball|march\s*madness)\b", eval_text):
            return "basket", "NCAA e College Basket"

        # LIVELLO 2: Campionati Nazionali (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
        country = (match.get("country") or match.get("_country") or "").lower().strip()
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

        # A) Segnali espliciti di lega/nazione estera -> Campionati Esteri
        if (is_foreign_country and country not in ("usa", "united states", "italia", "italy")) or is_foreign_league:
            return "basket", "Campionati Esteri ed NBL"

        # B) LBA Serie A (Italia: 1° Nazione -> 2° Torneo)
        is_italy = (
            country in ("italy", "italia")
            or "🇮🇹" in eval_text
            or bool(re.search(r"\b(italy|italia|italian)\b", eval_text))
        )
        is_lba_comp = bool(re.search(r"\b(lba|lega\s*a|serie\s*a\s*basket|lega\s*basket|serie\s*a2|coppa\s*italia\s*basket|supercoppa\s*basket)\b", eval_text))
        if is_lba_comp or (is_italy and not is_foreign_league):
            return "basket", "LBA Serie A"

        # C) NBA (USA: 1° Nazione -> 2° Torneo / Token NBA)
        is_usa = country in ("usa", "united states", "stati uniti", "us") or "🇺🇸" in eval_text
        has_nba_token = bool(re.search(r"\b(nba|nba-preseason|summer\s*league|national\s*basketball\s*association)\b", eval_text))

        if has_nba_token or (is_usa and not is_foreign_league):
            return "basket", "NBA"

        return "basket", "Campionati Esteri ed NBL"

    def _classify_volleyball(self, match: Dict[str, Any], eval_text: str, is_women: bool) -> Tuple[str, str]:
        # 1. Volley Femminile
        if is_women or bool(re.search(r"\b(women(?:['’]s)?|femminile|femenil|dames|frauen|ladies|a1\s*femm|serie\s*a1\s*f)\b", eval_text)):
            return "volley", "Volley Femminile"

        # 2. Champions League Volley
        if re.search(r"\b(cev|champions\s*league|cev\s*cup|challenge\s*cup)\b", eval_text):
            return "volley", "Champions League Volley"

        # 3. Nazionali e Internazionale
        if re.search(r"\b(vnl|nations\s*league|mondiali|europei|olympics|world\s*championship|world\s*cup|qualif|national\s*team)\b", eval_text):
            return "volley", "Nazionali e Internazionale"

        # LIVELLO 2: Campionati Nazionali Maschili (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
        country = (match.get("country") or match.get("_country") or "").lower().strip()
        is_foreign_country = country in (
            "poland", "polonia", "pl", "turkey", "turchia", "tr", "russia", "ru",
            "brazil", "brasile", "br", "france", "francia", "fr", "germany", "germania", "de",
            "japan", "giappone", "jp", "belgium", "belgio", "be", "austria", "at",
            "czech", "cechia", "cz", "slovenia", "si", "serbia", "rs"
        )
        is_foreign_league = bool(re.search(
            r"\b(plusliga|tauron|sultanlar|superliga\s*russia|pro\s*a|bundesliga|volley\s*league|extraliga)\b",
            eval_text
        ))

        if is_foreign_country and country not in ("italia", "italy"):
            return "volley", "Nazionali e Internazionale"
        if is_foreign_league:
            return "volley", "Nazionali e Internazionale"

        is_italy = (
            country in ("italy", "italia")
            or "🇮🇹" in eval_text
            or bool(re.search(r"\b(italy|italia|italian)\b", eval_text))
        )
        is_superlega_comp = bool(re.search(
            r"\b(superlega|super\s*lega|serie\s*a[123]|italian\s*volleyball\s*league|italy\s*super\s*lega|coppa\s*italia\s*volley|supercoppa\s*volley)\b",
            eval_text
        ))
        if is_superlega_comp or (is_italy and not is_foreign_league):
            return "volley", "Superlega e Serie A1"

        return "volley", "Nazionali e Internazionale"

    def _classify_american_football(self, match: Dict[str, Any], eval_text: str, is_youth: bool) -> Tuple[str, str]:
        country = (match.get("country") or match.get("_country") or "").lower().strip()
        # 1. CFL & Altre Leghe
        is_canada = country in ("canada", "ca") or "🇨🇦" in eval_text
        is_cfl = bool(re.search(r"\b(cfl|grey\s*cup|canadian\s*football|ufl|elf|european\s*league\s*of\s*football|xfl|arena\s*football|indoor\s*football)\b", eval_text))
        if is_cfl or (is_canada and not is_youth):
            return "football_americano", "CFL e Altre Leghe"

        # 2. NCAA College Football
        m_id = str(match.get("id") or "").lower()
        sources = match.get("sources") or []
        source_ids = " ".join(str(s.get("id", "")) for s in sources if isinstance(s, dict)).lower()

        is_explicit_college = (
            is_youth
            or bool(re.search(r"\b(cfb|ncaa|college|univ|university|d-?iii|d-?ii|d-?1|fbs|fcs)\b", eval_text))
            or "_cfb_" in m_id
            or m_id.startswith("live_cfb_")
            or "_cfb_" in source_ids
        )

        # 3. NFL
        is_usa = country in ("usa", "united states", "stati uniti", "us") or "🇺🇸" in eval_text
        has_nfl_keyword = bool(re.search(r"\b(nfl|redzone|super\s*bowl|manningcast|pro\s*bowl)\b", eval_text))
        is_espn_nfl = bool(match.get("_espn_matched") and match.get("_genre") == "NFL")

        if not is_explicit_college:
            if has_nfl_keyword or is_espn_nfl or (is_usa and not is_cfl):
                return "football_americano", "NFL"
        return "football_americano", "NCAA College Football"

    def _classify_baseball(self, match: Dict[str, Any], eval_text: str, is_youth: bool) -> Tuple[str, str]:
        # 1. NCAA College Baseball
        is_college = is_youth or bool(re.search(r"\b(college|ncaa|cws|college\s*world\s*series)\b", eval_text))
        if is_college:
            return "baseball", "NCAA College Baseball"

        # 2. Campionati Internazionali
        country = (match.get("country") or match.get("_country") or "").lower().strip()
        has_baseball_foreign_flag = any(flag in eval_text for flag in ("🇯🇵", "🇰🇷", "🇲🇽", "🇹🇼", "🇨🇺"))
        is_foreign_baseball = (
            country in ("japan", "giappone", "jp", "korea", "corea", "kr", "mexico", "messico", "mx", "taiwan", "tw", "cuba", "cu", "dominican", "república dominicana")
            or bool(re.search(r"\b(npb|kbo|lmb|cpbl|wbc|world\s*baseball|serie\s*del\s*caribe|japan|korea|mexican)\b", eval_text))
            or has_baseball_foreign_flag
        )
        if is_foreign_baseball:
            return "baseball", "Campionati Internazionali"

        # 3. MLB
        is_usa = country in ("usa", "united states", "us") or "🇺🇸" in eval_text
        has_mlb_keyword = bool(re.search(r"\b(mlb|major\s*league\s*baseball|world\s*series)\b", eval_text))
        if has_mlb_keyword or (is_usa and not is_foreign_baseball and not is_college):
            return "baseball", "MLB"

        return "baseball", "Campionati Internazionali"

    def _classify_hockey(self, match: Dict[str, Any], eval_text: str) -> Tuple[str, str]:
        # 1. Mondiali e Nazionali
        if re.search(r"\b(iihf|mondiali|olympics|world\s*championship|national\s*team|four\s*nations)\b", eval_text):
            return "hockey", "Mondiali e Nazionali"

        # 2. Leghe Europee
        country = (match.get("country") or match.get("_country") or "").lower().strip()
        has_hockey_eu_flag = any(flag in eval_text for flag in ("🇸🇪", "🇫🇮", "🇩🇪", "🇨🇿", "🇨🇭", "🇷🇺", "🇦🇹", "🇸🇰", "🇳🇴"))
        is_european_hockey = (
            country in ("sweden", "svezia", "se", "finland", "finlandia", "fi", "germany", "germania", "de", "czech", "cechia", "cz", "swiss", "switzerland", "svizzera", "ch", "russia", "ru", "austria", "at", "slovakia", "sk", "norway", "norvegia", "no", "uk", "great britain")
            or bool(re.search(r"\b(khl|del|liiga|shl|extraliga|tipsport|allsvenskan|national\s*league|icehl|ebel|alps|chl|champions\s*hockey|leghe\s*europee|eihl)\b", eval_text))
            or has_hockey_eu_flag
        )
        if is_european_hockey:
            return "hockey", "KHL e Leghe Europee"

        # 3. NHL
        is_usa_or_ca = country in ("usa", "united states", "us", "canada", "ca") or any(f in eval_text for f in ("🇺🇸", "🇨🇦"))
        has_nhl_keyword = bool(re.search(r"\b(nhl|national\s*hockey\s*league|stanley\s*cup)\b", eval_text))
        if has_nhl_keyword or (is_usa_or_ca and not is_european_hockey):
            return "hockey", "NHL"

        return "hockey", "KHL e Leghe Europee"

    def _classify_fight(self, match: Dict[str, Any], eval_text: str) -> Tuple[str, str]:
        sport_str = f"{match.get('sport') or ''} {match.get('category') or ''}".lower()
        if re.search(r"\b(wwe|aew|nxt|smackdown|raw|tna|wrestling|wrestlemania|royal\s*rumble|summerslam|survivor\s*series)\b", eval_text):
            return "combattimento", "Wrestling e WWE"
        if re.search(r"\b(ufc|dwcs|dana\s*white)\b", eval_text):
            return "combattimento", "UFC"
        if "boxe" in sport_str or "boxing" in sport_str:
            return "combattimento", "Boxe"
        if re.search(r"\b(boxing|boxe|pugilato|bout|heavyweight|title\s*fight|wba|wbc|wbo|ibf|top\s*rank|matchroom|golden\s*boy|queensberry|boxxer)\b", eval_text):
            return "combattimento", "Boxe"
        if re.search(r"\b(mma|bellator|pfl|one\s*championship|ksw|lfc|cage\s*warriors|combate|oktagon|rizin|invicta)\b", eval_text):
            return "combattimento", "MMA"
        if "box" in eval_text:
            return "combattimento", "Boxe"
        return "combattimento", "MMA"

    def _classify_altri_sport(self, match: Dict[str, Any], eval_text: str) -> Tuple[str, str]:
        cat = (match.get("category") or "").lower().strip()
        silo = (match.get("_silo") or match.get("category") or match.get("silo") or "").lower().strip()
        country = (match.get("country") or match.get("_country") or "").lower().strip()
        title_lower = (match.get("title") or "").lower().strip()

        is_afl_match = (
            re.search(r"\b(afl|aussie\s*rules|australian\s*football|vfl|sanfl|wafl)\b", eval_text)
            or (country in ("australia", "australian") and bool(re.search(r"\b(premiership|football\s*league)\b", eval_text)))
        )
        if is_afl_match or re.search(r"\b(rugby|afl|aflw|six\s*nations|sei\s*nazioni|nrl|top\s*14|premiership\s*rugby|urc|super\s*rugby|champions\s*cup\s*rugby)\b", eval_text) or cat in ("rugby", "afl") or silo in ("rugby", "afl", "aussie-rules"):
            return "altri_sport", "Rugby e AFL"
        if re.search(r"\b(cricket|odi|t20|twenty20|test\s*match|ipl|one\s*day\s*international|the\s*hundred|big\s*bash|ashes)\b", eval_text) or cat == "cricket" or silo == "cricket":
            return "altri_sport", "Cricket"
        if re.search(r"\b(golf|pga|lpga|ryder\s*cup|presidents\s*cup|liv|dp\s*world|masters\s*tournament)\b", eval_text) or cat == "golf" or silo == "golf":
            return "altri_sport", "Golf"
        if re.search(r"\b(darts|freccette|pdc|world\s*darts)\b", eval_text):
            return "altri_sport", "Freccette / Darts"
        if (re.search(r"\b(ciclismo|cycling|tour\s*de\s*france|giro\s*d['’]italia|vuelta|uci|road\s*race|time\s*trial)\b", eval_text) or cat == "cycling" or silo == "cycling") and "hockey" not in title_lower:
            return "altri_sport", "Ciclismo"
        if re.search(r"\b(handball|pallamano|ihf|ehf|dhb\s*pokal|bundesliga\s*handball)\b", eval_text) or cat == "handball" or silo == "handball":
            return "altri_sport", "Pallamano"
        return "altri_sport", "Biliardo, Padel e Altri"

    def _classify_football(self, match: Dict[str, Any], eval_text: str, is_women: bool, is_youth: bool) -> Tuple[str, str]:
        has_extra_eu_flag = any(flag in eval_text for flag in ("🇧🇷", "🇦🇷", "🇨🇴", "🇺🇾", "🇵🇾", "🇨🇱", "🇵🇪", "🇪🇨", "🇲🇽", "🇺🇸", "🇸🇦", "🇯🇵", "🇰🇷", "🇦🇺"))
        is_extra_eu = bool(self._EXTRA_EU_CONFED_REGEX.search(eval_text) or self._EXTRA_EU_LEAGUES_REGEX.search(eval_text) or has_extra_eu_flag)
        is_foreign = bool(self._FOREIGN_LEAGUE_REGEX.search(eval_text)) or is_extra_eu
        is_extra_eu_confed = bool(self._EXTRA_EU_CONFED_REGEX.search(eval_text))
        country = (match.get("country") or match.get("_country") or "").lower().strip()

        # 1. YOUTH & COLLEGE
        if is_youth:
            is_ita_youth = (
                (country in ("italia", "italy") or "🇮🇹" in eval_text)
                and not is_foreign
                and not is_extra_eu
            ) or bool(re.search(r"\b(primavera|allievi|giovanissimi)\b", eval_text))
            if not is_ita_youth:
                return "calcio_estero", "Europei Under 21 e Nazionali Giovanili"

        # 2. NATIONAL TEAMS & AMICHEVOLI
        is_national_match = (
            bool(self._FRIENDLY_REGEX.search(eval_text))
            or bool(re.search(r"\b(nations\s*league|fifa|qualif|international|world\s*cup|european\s*championship|african\s*cup|afcon|copa\s*america|asian\s*cup|gold\s*cup|intercontinental)\b", eval_text))
        )
        if is_national_match:
            return "calcio_estero", "Nazionali e Amichevoli"

        # 3. EUROPEAN CUPS & UEFA TOURNAMENTS
        is_uefa_cup = bool(re.search(r"\b(uefa\s*champions|champions\s*league|ucl|uwcl|europa\s*league|uel|conference\s*league|uecl|uefa\s*super\s*cup)\b", eval_text))
        if is_uefa_cup and not is_extra_eu_confed:
            if re.search(r"\b(champions\s*league|uefa\s*champions|ucl|uwcl)\b", eval_text):
                if is_women or re.search(r"\b(uwcl|women|femminile|donne)\b", eval_text):
                    return "calcio_estero", "Champions League Femminile"
                return "calcio_estero", "Champions League"
            if is_women:
                return "calcio_estero", "Champions League Femminile"
            return "calcio_estero", "Europa e Conference League"

        # 4. EXTRA-EU CONFEDERATIONS
        if is_extra_eu_confed:
            return "calcio_estero", "Americhe e Leghe Extra-UE"

        # 5. CALCIO ITALIANO (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
        is_ita_nation = (
            country in ("italia", "italy")
            or "🇮🇹" in eval_text
            or bool(re.search(r"\b(italy|italia|italian|serie\s*[abc]|coppa\s*italia|supercoppa\s*italiana|primavera)\b", eval_text))
        )
        if is_ita_nation and not is_foreign and not is_extra_eu:
            if is_women:
                return "calcio_italiano", "Calcio Femminile"
            if re.search(r"\b(coppa\s*italia|supercoppa\s*italiana|supercoppa)\b", eval_text):
                return "calcio_italiano", "Coppa Italia e Supercoppa"
            if re.search(r"\bserie\s*c\b", eval_text):
                return "calcio_italiano", "Serie C"
            if re.search(r"\bserie\s*b\b", eval_text):
                return "calcio_italiano", "Serie B"
            if re.search(r"\bserie\s*a\b", eval_text):
                return "calcio_italiano", "Serie A"
            if is_youth or bool(re.search(r"\b(primavera|allievi|giovanissimi|u23|next\s*gen|futuro)\b", eval_text)):
                return "calcio_italiano", "Primavera e Giovanili"
            return "calcio_italiano", "Serie A"

        # 6. PREMIER LEAGUE (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
        is_explicit_epl = bool(re.search(r"\b(english\s*premier\s*league|epl|premier-league)\b|\((?:🏆\s*)?premier\s*league\)|(?:^|:\s*)premier\s*league\b", eval_text))
        is_england = (
            country in ("inghilterra", "england", "uk", "regno unito")
            or "🏴󠁧󠁢󠁥󠁮󠁧󠁿" in eval_text
            or is_explicit_epl
            or bool(re.search(r"\b(england|inghilterra|uk|regno\s*unito|epl|english)\b", eval_text))
        )
        if is_england and not is_extra_eu:
            if is_youth or bool(re.search(r"\b(u21|u23|u18|premier\s*league\s*2)\b", eval_text)):
                return "calcio_estero", "Europei Under 21 e Nazionali Giovanili"
            if is_women:
                return "calcio_estero", "Altri Campionati Europei"
            if bool(re.search(r"\b(premier\s*league|epl|fa\s*cup|carabao|efl\s*cup)\b", eval_text)):
                return "calcio_estero", "Premier League"
            return "calcio_estero", "Altri Campionati Europei"

        # 7. LA LIGA (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
        is_spain = (
            country in ("spagna", "spain")
            or "🇪🇸" in eval_text
            or bool(re.search(r"\b(spain|spagna|espana|españa|spanish)\b", eval_text))
            or (bool(re.search(r"\b(laliga|la\s*liga|primera\s*division|copa\s*del\s*rey|supercopa)\b", eval_text)) and not is_extra_eu and not bool(self._FOREIGN_LEAGUE_REGEX.search(eval_text)))
        )
        if is_spain and not is_extra_eu:
            if is_youth or bool(re.search(r"\b(u21|u23|u18|u19)\b", eval_text)):
                return "calcio_estero", "Europei Under 21 e Nazionali Giovanili"
            if is_women:
                return "calcio_estero", "Altri Campionati Europei"
            is_segunda = bool(re.search(r"\b(segunda|laliga\s*2|hypermotion|primera\s*rfef|rfef)\b", eval_text))
            if not is_segunda and bool(re.search(r"\b(laliga|la\s*liga|primera\s*division|copa\s*del\s*rey|supercopa)\b", eval_text)):
                return "calcio_estero", "La Liga"
            return "calcio_estero", "Altri Campionati Europei"

        # 8. BUNDESLIGA E LIGUE 1 (DOPPIO FILTRO: 1° Nazione -> 2° Torneo)
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
            return "calcio_estero", "Altri Campionati Europei"

        # 9. ALTRI CAMPIONATI EUROPEI
        has_eu_flag = any(flag in eval_text for flag in self._EUROPEAN_FLAGS)
        is_european_country = country in self._EUROPEAN_COUNTRIES
        is_european_league = bool(self._EUROPEAN_LEAGUES_REGEX.search(eval_text))
        if (is_european_league or is_european_country or has_eu_flag) and not is_extra_eu:
            return "calcio_estero", "Altri Campionati Europei"

        # 10. AMERICHE E LEGHE EXTRA-UE
        return "calcio_estero", "Americhe e Leghe Extra-UE"

    def classify_genre(self, match: Dict[str, Any]) -> str:
        """Helper returning only the genre string for backward compatibility."""
        _, genre = self.classify(match)
        return genre

genre_classifier = GenreClassifier()
