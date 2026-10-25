import re
from typing import Any, Dict, Optional, Tuple

# ==============================================================================
# CANONICAL COMPETITION TAXONOMY
# Comprehensive multi-sport catalog coverage: Calcio, Basket, Tennis, Motori,
# American Football, Baseball, Hockey, Combattimento, Volley, Altri Sport.
# ==============================================================================

CANONICAL_LEAGUES = {
    # --------------------------------------------------------------------------
    # CALCIO / SOCCER (football)
    # --------------------------------------------------------------------------
    # Latin American leagues sharing 'Serie A / Serie B / Serie C' nomenclature
    # MUST be evaluated BEFORE Italian Serie A/B/C to avoid misclassification
    "football_brasileirao": {
        "display": "Brasileirão Série A",
        "silo": "football",
        "patterns": [
            r"brasileir[aã]o(?:\s*serie\s*a)?\b",
            r"brazil\s*(?:-\s*)?serie\s*a\b",
            r"brasil\s*(?:-\s*)?serie\s*a\b",
            r"serie\s*a\s*(?:brazil|brasil)\b",
            r"campeonato\s*brasileiro(?:\s*serie\s*a)?\b",
            r"🇧🇷.*?serie\s*a\b",
            r"\bserie\s*a.*?🇧🇷",
        ],
    },
    "football_brazil_serie_b": {
        "display": "Brasileirão Série B",
        "silo": "football",
        "patterns": [
            r"brazil\s*(?:-\s*)?serie\s*b\b",
            r"brasil\s*(?:-\s*)?serie\s*b\b",
            r"serie\s*b\s*(?:brazil|brasil)\b",
            r"brasileir[aã]o\s*(?:serie\s*)?b\b",
            r"campeonato\s*brasileiro\s*(?:serie\s*)?b\b",
            r"🇧🇷.*?serie\s*b\b",
            r"\bserie\s*b.*?🇧🇷",
        ],
    },
    "football_brazil_serie_c": {
        "display": "Brasileirão Série C",
        "silo": "football",
        "patterns": [
            r"brazil\s*(?:-\s*)?serie\s*c\b",
            r"brasil\s*(?:-\s*)?serie\s*c\b",
            r"serie\s*c\s*(?:brazil|brasil)\b",
            r"brasileir[aã]o\s*(?:serie\s*)?c\b",
            r"campeonato\s*brasileiro\s*(?:serie\s*)?c\b",
            r"🇧🇷.*?serie\s*c\b",
            r"\bserie\s*c.*?🇧🇷",
        ],
    },
    "football_ecuador_serie_a": {
        "display": "LigaPro Serie A",
        "silo": "football",
        "patterns": [
            r"ecuador\s*(?:-\s*)?serie\s*a\b",
            r"serie\s*a\s*ecuador\b",
            r"ligapro\s*(?:serie\s*)?a\b",
            r"🇪🇨.*?serie\s*a\b",
            r"\bserie\s*a.*?🇪🇨",
        ],
    },
    "football_ecuador_serie_b": {
        "display": "LigaPro Serie B",
        "silo": "football",
        "patterns": [
            r"ecuador\s*(?:-\s*)?serie\s*b\b",
            r"serie\s*b\s*ecuador\b",
            r"ligapro\s*(?:serie\s*)?b\b",
            r"🇪🇨.*?serie\s*b\b",
            r"\bserie\s*b.*?🇪🇨",
        ],
    },
    "football_serie_a_women": {
        "display": "Serie A Femminile",
        "silo": "football",
        "patterns": [
            r"serie\s*a\s*(?:women|femminile|femm|frauen|dames|femenino)",
            r"(?:italian|italy)\s*women['']?s?\s*serie\s*a",
            r"serie\s*a\s*femminile",
        ],
    },
    "football_serie_a": {
        "display": "Serie A",
        "silo": "football",
        "patterns": [
            r"italy\s*-\s*serie\s*a\b(?!\s*women)(?!\s*femminile)",
            r"italy\s+serie\s*a\b(?!\s*women)(?!\s*femminile)",
            r"(?<!brazil\s)(?<!brazil\s-\s)(?<!brasil\s)(?<!brasil\s-\s)(?<!ecuador\s)(?<!ecuador\s-\s)\bserie\s*a\b(?!\s*brazil)(?!\s*brasil)(?!\s*ecuador)(?!\s*women)(?!\s*femminile)(?!\s*femm)(?!\s*frauen)(?!\s*dames)",
        ],
    },
    "football_serie_b": {
        "display": "Serie B",
        "silo": "football",
        "patterns": [
            r"italy\s*-\s*serie\s*b",
            r"italy\s+serie\s*b",
            r"(?<!brazil\s)(?<!brazil\s-\s)(?<!brasil\s)(?<!brasil\s-\s)(?<!ecuador\s)(?<!ecuador\s-\s)\bserie\s*b\b(?!\s*brazil)(?!\s*brasil)(?!\s*ecuador)",
        ],
    },
    "football_serie_c": {
        "display": "Serie C",
        "silo": "football",
        "patterns": [
            r"italy\s*-\s*serie\s*c",
            r"italy\s+serie\s*c",
            r"(?<!brazil\s)(?<!brazil\s-\s)(?<!brasil\s)(?<!brasil\s-\s)(?<!ecuador\s)(?<!ecuador\s-\s)\bserie\s*c\b(?!\s*brazil)(?!\s*brasil)(?!\s*ecuador)",
        ],
    },
    "football_coppa_italia": {
        "display": "Coppa Italia",
        "silo": "football",
        "patterns": [r"coppa\s*italia", r"supercoppa\s*italiana"],
    },
    "football_primavera": {
        "display": "Campionato Primavera",
        "silo": "football",
        "patterns": [r"\bprimavera\b", r"u-?19\s*campionato"],
    },
    "football_premier_league": {
        "display": "Premier League",
        "silo": "football",
        "patterns": [
            r"england\s*-\s*premier\s*league\b(?!\s*2\b)(?!\s*cup)(?!\s*u\d+)",
            r"inghilterra:\s*premier\s*league\b(?!\s*2\b)(?!\s*cup)(?!\s*u\d+)",
            r"(?<!northern\s)(?<!southern\s)english\s*premier\s*league\b(?!\s*2\b)(?!\s*cup)(?!\s*u\d+)",
            r"\bepl\b",
            r"premier-league\b",
            r"^(?:🏆\s*)?premier\s*league(?:\s*-\s*(?!2\b|cup|u\d+).*)?$",
            r"\((?:🏆\s*)?premier\s*league\)",
        ],
    },
    "football_championship": {
        "display": "Championship",
        "silo": "football",
        "patterns": [
            r"england\s*-\s*championship",
            r"inghilterra:\s*championship",
            r"efl\s*championship",
            r"(?<!scottish\s)(?<!scotland\s)(?<!scozia\s)(?<!ireland\s)(?<!northern\s)(?<!usl\s)(?<!women\s)\bchampionship\b(?!\s*scotland)(?!\s*ireland)",
        ],
    },
    "football_fa_cup": {
        "display": "FA Cup",
        "silo": "football",
        "patterns": [r"\bfa\s*cup\b", r"\bcarabao\s*cup\b", r"efl\s*cup"],
    },
    "football_la_liga": {
        "display": "LaLiga",
        "silo": "football",
        "patterns": [
            r"spain\s*-\s*la\s*liga\b(?!\s*2\b)",
            r"spagna:\s*la\s*liga\b(?!\s*2\b)",
            r"spagna:\s*laliga\b(?!\s*2\b)",
            r"spanish\s*la\s*liga\b(?!\s*2\b)",
            r"(?<!copa de )(?<!chilean )(?<!argentina )(?<!uruguay )(?<!nicaragua )\bla\s*liga\b(?!\s*2\b)(?!\s*hypermotion)",
            r"(?:spain|spagna)\s*-\s*primera\s*division",
        ],
    },
    "football_segunda": {
        "display": "Segunda División",
        "silo": "football",
        "patterns": [
            r"segunda\s*division",
            r"spain\s*-\s*segunda",
            r"spagna:\s*laliga2",
            r"spagna:\s*la\s*liga\s*2",
            r"\bla\s*liga\s*2\b",
            r"\bla\s*liga\s*hypermotion\b",
        ],
    },
    "football_copa_del_rey": {
        "display": "Copa del Rey",
        "silo": "football",
        "patterns": [r"copa\s*del\s*rey", r"supercopa\s*de\s*espana"],
    },
    "football_bundesliga": {
        "display": "Bundesliga",
        "silo": "football",
        "patterns": [
            r"germany\s*-\s*bundesliga\b(?!\s*2\b)(?!\s*women\b)(?!\s*frauen\b)",
            r"germania:\s*bundesliga\b(?!\s*2\b)(?!\s*-\s*femminile)(?!\s*women\b)(?!\s*frauen\b)",
            r"german\s*bundesliga\b(?!\s*2\b)(?!\s*women\b)(?!\s*frauen\b)",
            r"(?<!austria\s)(?<!austrian\s)(?<!austria:\s)(?<!handball\s)(?<!handball-)(?<!women\s)(?<!femminile\s)\bbundesliga\b(?!\s*austria)(?!\s*2\b)(?!\s*-\s*femminile)(?!\s*women\b)(?!\s*frauen\b)",
        ],
    },
    "football_austrian_bundesliga": {
        "display": "Austrian Bundesliga",
        "silo": "football",
        "patterns": [
            r"austria:\s*bundesliga",
            r"austria\s*-\s*bundesliga",
            r"austrian\s*bundesliga",
        ],
    },
    "football_2_bundesliga": {
        "display": "2. Bundesliga",
        "silo": "football",
        "patterns": [r"2\.\s*bundesliga", r"bundesliga\s*2"],
    },
    "football_dfb_pokal": {
        "display": "DFB-Pokal",
        "silo": "football",
        "patterns": [r"dfb\s*pokal", r"dfb-pokal"],
    },
    "football_ligue_1": {
        "display": "Ligue 1",
        "silo": "football",
        "patterns": [r"ligue\s*1", r"france\s*-\s*ligue\s*1"],
    },
    "football_ligue_2": {
        "display": "Ligue 2",
        "silo": "football",
        "patterns": [r"ligue\s*2", r"france\s*-\s*ligue\s*2"],
    },
    "football_coupe_de_france": {
        "display": "Coupe de France",
        "silo": "football",
        "patterns": [r"coupe\s*de\s*france"],
    },
    "football_uefa_youth_league": {
        "display": "UEFA Youth League",
        "silo": "football",
        "patterns": [r"uefa-youth-league", r"uefa\s*youth\s*league", r"youth\s*league"],
    },
    "football_uwcl": {
        "display": "UEFA Women's Champions League",
        "silo": "football",
        "patterns": [r"uwcl", r"women['’]?s\s*champions\s*league", r"uefa\s*women\s*champions"],
    },
    "football_uefa_cl": {
        "display": "UEFA Champions League",
        "silo": "football",
        "patterns": [
            r"uefa-champions-league",
            r"uefa\s*champions\s*league",
            r"\bucl\b",
            r"(?<!afc\s)(?<!caf\s)(?<!ehf\s)(?<!hockey\s)champions\s*league"
        ],
    },
    "football_uefa_el": {
        "display": "UEFA Europa League",
        "silo": "football",
        "patterns": [r"uefa-europa-league", r"europa\s*league", r"uefa\s*europa\s*league", r"\buel\b"],
    },
    "football_uefa_ecl": {
        "display": "UEFA Conference League",
        "silo": "football",
        "patterns": [r"uefa-conference-league", r"conference\s*league", r"uefa\s*conference\s*league", r"\buecl\b"],
    },
    "football_uefa_super_cup": {
        "display": "UEFA Super Cup",
        "silo": "football",
        "patterns": [r"uefa\s*super\s*cup", r"super\s*cup\s*uefa"],
    },
    "football_uefa_nations": {
        "display": "UEFA Nations League",
        "silo": "football",
        "patterns": [r"nations\s*league", r"uefa\s*nations\s*league"],
    },
    "football_eredivisie": {
        "display": "Eredivisie",
        "silo": "football",
        "patterns": [r"eredivisie", r"netherlands\s*-\s*eredivisie", r"holland\s*eredivisie"],
    },
    "football_primeira_liga": {
        "display": "Primeira Liga",
        "silo": "football",
        "patterns": [r"primeira\s*liga", r"liga\s*portugal", r"portugal\s*-\s*primeira"],
    },
    "football_scottish_prem": {
        "display": "Scottish Premiership",
        "silo": "football",
        "patterns": [r"scottish\s*premiership", r"scotland\s*-\s*premiership", r"\bspfl\b"],
    },
    "football_super_lig": {
        "display": "Süper Lig",
        "silo": "football",
        "patterns": [
            r"turkey\s*-\s*super\s*lig\b",
            r"turchia:\s*super\s*lig\b",
            r"turkish\s*super\s*lig\b",
            r"\bs[uü]per\s*lig\b",
        ],
    },
    "football_danish_superliga": {
        "display": "Danish Superliga",
        "silo": "football",
        "patterns": [
            r"danimarca:\s*superliga",
            r"denmark\s*-\s*superliga",
            r"danish\s*superliga",
            r"\bsuperligaen\b",
        ],
    },
    "football_swiss_super_league": {
        "display": "Swiss Super League",
        "silo": "football",
        "patterns": [
            r"svizzera:\s*super\s*league",
            r"switzerland\s*-\s*super\s*league",
            r"swiss\s*super\s*league",
        ],
    },
    "football_belgian_pro": {
        "display": "Belgian Pro League",
        "silo": "football",
        "patterns": [r"belgian\s*pro\s*league", r"jupiler\s*pro\s*league", r"belgium\s*-\s*first\s*division"],
    },
    "football_efl_league_one": {
        "display": "EFL League One",
        "silo": "football",
        "patterns": [r"league\s*one", r"england\s*-\s*league\s*one"],
    },
    "football_efl_league_two": {
        "display": "EFL League Two",
        "silo": "football",
        "patterns": [r"league\s*two", r"england\s*-\s*league\s*two"],
    },
    "football_copa_libertadores": {
        "display": "Copa Libertadores",
        "silo": "football",
        "patterns": [r"copa\s*libertadores", r"libertadores"],
    },
    "football_copa_sudamericana": {
        "display": "Copa Sudamericana",
        "silo": "football",
        "patterns": [r"copa\s*sudamericana", r"sudamericana"],
    },
    "football_mls": {
        "display": "Major League Soccer",
        "silo": "football",
        "patterns": [r"\bmls\b", r"major\s*league\s*soccer"],
    },
    "football_liga_mx": {
        "display": "Liga MX",
        "silo": "football",
        "patterns": [r"liga\s*mx", r"mexico\s*-\s*liga\s*mx"],
    },
    "football_liga_profesional_arg": {
        "display": "Liga Profesional Argentina",
        "silo": "football",
        "patterns": [r"liga\s*profesional", r"argentina\s*-\s*primera\s*division", r"primera\s*division\s*argentina"],
    },
    "football_saudi_pro": {
        "display": "Saudi Pro League",
        "silo": "football",
        "patterns": [r"saudi\s*pro\s*league", r"\broshn\b"],
    },
    "football_afc_cl": {
        "display": "AFC Champions League",
        "silo": "football",
        "patterns": [r"afc\s*champions", r"afc\s*champions\s*league"],
    },
    "football_world_cup_qual": {
        "display": "Qualificazioni Mondiali",
        "silo": "football",
        "patterns": [r"world\s*cup\s*qual", r"qualificazioni\s*mondiali", r"wc\s*qual"],
    },
    "football_world_cup": {
        "display": "FIFA World Cup",
        "silo": "football",
        "patterns": [r"fifa\s*world\s*cup", r"world\s*cup", r"coppa\s*del\s*mondo"],
    },
    "football_euro_qual": {
        "display": "Qualificazioni Europei",
        "silo": "football",
        "patterns": [r"euro\s*qual", r"uefa\s*euro\s*qual", r"qualificazioni\s*europei"],
    },
    "football_euro": {
        "display": "UEFA Euro",
        "silo": "football",
        "patterns": [r"uefa\s*euro\b", r"european\s*championship", r"\beuro\s*202[48]\b"],
    },
    "football_copa_america": {
        "display": "Copa América",
        "silo": "football",
        "patterns": [r"copa\s*am[eé]rica"],
    },
    "football_afcon": {
        "display": "Coppa d'Africa (AFCON)",
        "silo": "football",
        "patterns": [r"africa\s*cup\s*of\s*nations", r"\bafcon\b", r"coppa\s*d['’]africa"],
    },
    "football_gold_cup": {
        "display": "CONCACAF Gold Cup",
        "silo": "football",
        "patterns": [r"gold\s*cup", r"concacaf\s*gold\s*cup"],
    },
    "football_club_world_cup": {
        "display": "FIFA Club World Cup",
        "silo": "football",
        "patterns": [r"club\s*world\s*cup", r"coppa\s*del\s*mondo\s*per\s*club"],
    },
    "football_international_friendly": {
        "display": "Amichevoli Nazionali",
        "silo": "football",
        "patterns": [r"international\s*friendly", r"amichevole\s*internazionale", r"amichevoli\s*internazionali"],
    },

    # --------------------------------------------------------------------------
    # BASKETBALL (basketball)
    # --------------------------------------------------------------------------
    "basketball_nba": {
        "display": "NBA",
        "silo": "basketball",
        "patterns": [
            r"\bnba\b",
            r"nba-preseason",
            r"nba\s*preseason",
            r"\b(trail\s*blazers|timberwolves|76ers|sixers|cavaliers|mavericks)\b",
            r"\b(boston\s*celtics|chicago\s*bulls|houston\s*rockets|los\s*angeles\s*lakers|la\s*lakers|golden\s*state|sacramento\s*kings|la\s*clippers|los\s*angeles\s*clippers|ny\s*knicks|new\s*york\s*knicks|okc\s*thunder|oklahoma\s*city\s*thunder|orlando\s*magic|miami\s*heat|milwaukee\s*bucks|san\s*antonio\s*spurs|toronto\s*raptors|utah\s*jazz|washington\s*wizards|brooklyn\s*nets|charlotte\s*hornets|atlanta\s*hawks|denver\s*nuggets|detroit\s*pistons|indiana\s*pacers|memphis\s*grizzlies|phoenix\s*suns|new\s*orleans\s*pelicans)\b",
        ],
    },
    "basketball_cba": {
        "display": "CBA Cina",
        "silo": "basketball",
        "patterns": [r"\bcba\b", r"chinese\s*basketball", r"chinese\s*cba", r"china\s*cba"],
    },
    "basketball_wnba": {
        "display": "WNBA",
        "silo": "basketball",
        "patterns": [r"\bwnba\b", r"liga\s*femenina", r"tauihi"],
    },
    "basketball_euroleague": {
        "display": "EuroLeague",
        "silo": "basketball",
        "patterns": [r"euroleague", r"eurolega"],
    },
    "basketball_eurocup": {
        "display": "EuroCup",
        "silo": "basketball",
        "patterns": [r"eurocup"],
    },
    "basketball_bcl": {
        "display": "Basketball Champions League",
        "silo": "basketball",
        "patterns": [r"basketball\s*champions\s*league", r"\bbcl\b", r"fiba\s*europe\s*cup"],
    },
    "basketball_lba_serie_a": {
        "display": "LBA Serie A",
        "silo": "basketball",
        "patterns": [r"\blba\b", r"serie\s*a\s*basket", r"italy\s*-\s*lba", r"lega\s*basket", r"coppa\s*italia\s*basket", r"serie\s*a2"],
    },
    "basketball_acb": {
        "display": "Liga ACB",
        "silo": "basketball",
        "patterns": [r"\bacb\b", r"liga\s*acb", r"liga\s*endesa", r"spagna:\s*acb", r"primera\s*feb"],
    },
    "basketball_nbl": {
        "display": "NBL Australia",
        "silo": "basketball",
        "patterns": [r"\bnbl\b", r"australian\s*nbl"],
    },
    "basketball_bbl": {
        "display": "BBL Germania",
        "silo": "basketball",
        "patterns": [r"\bbbl\b", r"german\s*cup", r"bbl-pokal"],
    },
    "basketball_lnb": {
        "display": "LNB Pro A",
        "silo": "basketball",
        "patterns": [r"\blnb\b", r"betclic\s*elite", r"\bpro\s*a\b"],
    },
    "basketball_greece": {
        "display": "GBL Grecia",
        "silo": "basketball",
        "patterns": [r"basket\s*league", r"greece\s*basket", r"\bgbl\b"],
    },
    "basketball_turkey": {
        "display": "BSL Turchia",
        "silo": "basketball",
        "patterns": [r"s[üu]per\s*ligi", r"super\s*lig", r"\bbsl\b"],
    },
    "basketball_aba": {
        "display": "ABA League",
        "silo": "basketball",
        "patterns": [r"aba\s*league", r"admiralbet\s*aba"],
    },
    "basketball_fiba": {
        "display": "FIBA",
        "silo": "basketball",
        "patterns": [r"\bfiba\b", r"\b3x3\b", r"world\s*tour", r"eurobasket", r"world\s*cup"],
    },
    "basketball_ncaa": {
        "display": "NCAA Basketball",
        "silo": "basketball",
        "patterns": [r"ncaa\s*basketball", r"college\s*basketball", r"march\s*madness"],
    },

    # --------------------------------------------------------------------------
    # TENNIS (tennis)
    # --------------------------------------------------------------------------
    "tennis_grand_slam": {
        "display": "Grandi Slam",
        "silo": "tennis",
        "patterns": [r"\bwimbledon\b", r"us\s*open", r"roland\s*garros", r"australian\s*open"],
    },
    "tennis_atp": {
        "display": "ATP",
        "silo": "tennis",
        "patterns": [r"\batp\b", r"atp\s*1000", r"atp\s*500", r"atp\s*250", r"atp\s*finals"],
    },
    "tennis_wta": {
        "display": "WTA",
        "silo": "tennis",
        "patterns": [r"\bwta\b", r"wta\s*1000", r"wta\s*500", r"wta\s*250", r"wta\s*finals"],
    },
    "tennis_davis_bjk": {
        "display": "Coppa Davis & BJK",
        "silo": "tennis",
        "patterns": [r"davis\s*cup", r"coppa\s*davis", r"billie\s*jean\s*king", r"\bbjk\s*cup\b", r"laver\s*cup"],
    },
    "tennis_challenger": {
        "display": "Challenger & ITF",
        "silo": "tennis",
        "patterns": [r"\bchallenger\b", r"\bitf\b"],
    },

    # --------------------------------------------------------------------------
    # MOTORI (motor-sports)
    # --------------------------------------------------------------------------
    "motorsport_f1": {
        "display": "Formula 1",
        "silo": "motor-sports",
        "patterns": [r"formula\s*1", r"formula\s*one", r"\bf1\b", r"f1\s*grand\s*prix", r"formula\s*1\s*gp"],
    },
    "motorsport_motogp": {
        "display": "MotoGP",
        "silo": "motor-sports",
        "patterns": [r"motogp", r"moto\s*gp", r"\bmoto2\b", r"\bmoto3\b"],
    },
    "motorsport_sbk": {
        "display": "Superbike (WSBK)",
        "silo": "motor-sports",
        "patterns": [r"superbike", r"\bwsbk\b", r"\bssp\b", r"supersport"],
    },
    "motorsport_nascar": {
        "display": "NASCAR",
        "silo": "motor-sports",
        "patterns": [r"\bnascar\b", r"cup\s*series", r"xfinity\s*series", r"craftsman\s*truck"],
    },
    "motorsport_indycar": {
        "display": "IndyCar",
        "silo": "motor-sports",
        "patterns": [r"indycar", r"indy\s*500"],
    },
    "motorsport_rally_wrc": {
        "display": "Rally (WRC)",
        "silo": "motor-sports",
        "patterns": [r"\bwrc\b", r"world\s*rally", r"\brally\b"],
    },
    "motorsport_wec": {
        "display": "WEC & Le Mans",
        "silo": "motor-sports",
        "patterns": [r"\bwec\b", r"le\s*mans", r"24\s*hours\s*of\s*le\s*mans"],
    },

    # --------------------------------------------------------------------------
    # FOOTBALL AMERICANO (american-football)
    # --------------------------------------------------------------------------
    "am_football_nfl": {
        "display": "NFL",
        "silo": "american-football",
        "patterns": [r"\bnfl\b", r"redzone", r"red\s*zone", r"super\s*bowl"],
    },
    "am_football_ncaa": {
        "display": "NCAA College Football",
        "silo": "american-football",
        "patterns": [
            r"college\s*football",
            r"\bcfb\b",
            r"ncaa\s*football",
            r"\bncaa\s*fbs\b",
            r"\bncaaf\b",
            r"\bncaa\b",
            r"\bfbs\b",
        ],
    },
    "am_football_cfl": {
        "display": "CFL",
        "silo": "american-football",
        "patterns": [r"\bcfl\b", r"canadian\s*football"],
    },
    "am_football_ufl": {
        "display": "UFL",
        "silo": "american-football",
        "patterns": [r"\bufl\b"],
    },

    # --------------------------------------------------------------------------
    # BASEBALL (baseball)
    # --------------------------------------------------------------------------
    "baseball_mlb": {
        "display": "MLB",
        "silo": "baseball",
        "patterns": [r"\bmlb\b", r"major\s*league\s*baseball", r"world\s*series"],
    },
    "baseball_ncaa": {
        "display": "NCAA College Baseball",
        "silo": "baseball",
        "patterns": [r"college\s*baseball", r"college\s*world\s*series"],
    },

    # --------------------------------------------------------------------------
    # HOCKEY (hockey)
    # --------------------------------------------------------------------------
    "hockey_chl": {
        "display": "Champions Hockey League",
        "silo": "hockey",
        "patterns": [r"champions\s*hockey\s*league", r"\bchl\b", r"champions\s*league"],
    },
    "hockey_nhl": {
        "display": "NHL",
        "silo": "hockey",
        "patterns": [r"\bnhl\b", r"stanley\s*cup"],
    },
    "hockey_khl": {
        "display": "KHL",
        "silo": "hockey",
        "patterns": [r"\bkhl\b", r"russia\s*khl"],
    },
    "hockey_shl": {
        "display": "SHL",
        "silo": "hockey",
        "patterns": [r"\bshl\b", r"swedish\s*hockey\s*league", r"svezia\s*shl", r"sweden\s*shl"],
    },
    "hockey_extraliga": {
        "display": "Czech Extraliga",
        "silo": "hockey",
        "patterns": [r"czech\s*extraliga", r"tipsport\s*extraliga", r"\bextraliga\b"],
    },
    "hockey_liiga": {
        "display": "Liiga",
        "silo": "hockey",
        "patterns": [r"\bliiga\b", r"sm-liiga", r"finnish\s*liiga"],
    },
    "hockey_del": {
        "display": "DEL",
        "silo": "hockey",
        "patterns": [r"\bdel\b", r"deutsche\s*eishockey\s*liga"],
    },
    "hockey_euro": {
        "display": "Leghe Europee Hockey",
        "silo": "hockey",
        "patterns": [r"leghe\s*europee", r"european\s*leagues?", r"national\s*league", r"icehl", r"ebel", r"alps\s*hockey"],
    },

    # --------------------------------------------------------------------------
    # COMBATTIMENTO (fight)
    # --------------------------------------------------------------------------
    "fight_ufc": {
        "display": "UFC",
        "silo": "fight",
        "patterns": [r"\bufc\b", r"dana\s*white"],
    },
    "fight_boxing": {
        "display": "Boxe",
        "silo": "fight",
        "patterns": [r"\bboxing\b", r"pugilato", r"title\s*fight"],
    },
    "fight_wwe": {
        "display": "WWE & Wrestling",
        "silo": "fight",
        "patterns": [r"\bwwe\b", r"\baew\b", r"raw\b", r"smackdown", r"wrestlemania"],
    },
    "fight_mma": {
        "display": "MMA",
        "silo": "fight",
        "patterns": [r"\bmma\b", r"\bbellator\b", r"\bpfl\b", r"one\s*championship"],
    },

    # --------------------------------------------------------------------------
    # VOLLEYBALL (volley)
    # --------------------------------------------------------------------------
    "volleyball_superlega": {
        "display": "SuperLega",
        "silo": "volley",
        "patterns": [
            r"superlega",
            r"super\s*lega",
            r"serie\s*a1\s*maschile",
            r"italian\s*volleyball\s*league",
            r"italy\s*super\s*lega",
            r"italy\s*-\s*superlega",
            r"lega\s*pallavolo\s*serie\s*a",
        ],
    },
    "volleyball_serie_a2": {
        "display": "Serie A2 Volley",
        "silo": "volley",
        "patterns": [
            r"italy\s*serie\s*a2\b",
            r"serie\s*a2\s*volley",
            r"serie\s*a2\s*maschile",
            r"serie\s*a2\s*femm",
        ],
    },
    "volleyball_serie_a1_femm": {
        "display": "Serie A1 Femminile Volley",
        "silo": "volley",
        "patterns": [
            r"serie\s*a1\s*femminile",
            r"a1\s*femminile\s*volley",
            r"italy\s*serie\s*a1\s*women",
            r"italian\s*serie\s*a1\s*women",
            r"serie\s*a1\s*women",
            r"serie\s*a1\s*w\b",
            r"a1\s*femm\b",
        ],
    },
    "volleyball_cev_cl": {
        "display": "CEV Champions League",
        "silo": "volley",
        "patterns": [r"cev\s*champions\s*league", r"volleyball\s*champions\s*league"],
    },

    # --------------------------------------------------------------------------
    # ALTRI SPORT (altri_sport)
    # --------------------------------------------------------------------------
    "rugby_six_nations": {
        "display": "Sei Nazioni",
        "silo": "altri_sport",
        "patterns": [r"six\s*nations", r"sei\s*nazioni"],
    },
    "rugby_top_14": {
        "display": "Top 14 & Rugby Club",
        "silo": "altri_sport",
        "patterns": [r"top\s*14", r"premiership\s*rugby", r"\burc\b", r"united\s*rugby\s*championship"],
    },
    "afl_footy": {
        "display": "AFL (Australian Rules)",
        "silo": "altri_sport",
        "patterns": [r"\bafl\b", r"australian\s*rules", r"\bfooty\b"],
    },
    "golf_pga": {
        "display": "PGA Tour",
        "silo": "altri_sport",
        "patterns": [r"pga\s*tour", r"\bpga\b", r"masters\s*tournament", r"the\s*open"],
    },
    "golf_liv": {
        "display": "LIV Golf",
        "silo": "altri_sport",
        "patterns": [r"liv\s*golf", r"\bliv\b"],
    },
    "darts_pdc": {
        "display": "Freccette (PDC)",
        "silo": "altri_sport",
        "patterns": [r"\bpdc\b", r"premier\s*league\s*darts", r"world\s*darts"],
    },
    "cycling_grand_tours": {
        "display": "Grandi Giri Ciclismo",
        "silo": "altri_sport",
        "patterns": [r"giro\s*d['’]italia", r"tour\s*de\s*france", r"vuelta\s*a\s*espana"],
    },
    "handball_ehf_cl": {
        "display": "EHF Champions League",
        "silo": "altri_sport",
        "patterns": [r"ehf\s*champions\s*league", r"\behf\b"],
    },
}

# Precompile all regex patterns for ultra-fast lookup
_COMPILED_PATTERNS = []
for _slug, _data in CANONICAL_LEAGUES.items():
    _regexes = [re.compile(p, re.IGNORECASE) for p in _data["patterns"]]
    _COMPILED_PATTERNS.append((_slug, _data["silo"], _data["display"], _regexes))


SIMULCAST_PATTERN = re.compile(
    r"\b(red\s*zone|redzone|golazo(?:\s*show)?|simulcast|diretta\s*gol|direttagol|zona\s*gol|"
    r"zona\s*serie\s*[ab]|zona\s*dazn|zona\s*europa|konferenz|multifoot|multiplex|"
    r"multiliga|multichampions|multipremier|multigoal|goal\s*rush|diretta\s*basket|"
    r"diretta\s*eurolega|diretta\s*tennis|early\s*rounds|tennis\s*channel|whip\s*around)\b",
    re.I,
)


def is_simulcast_event(text: str) -> bool:
    """
    Detects if an event is a multi-match / whip-around broadcast show or multi-court session
    (e.g. NFL RedZone, The Golazo Show, UEFA Simulcast, Diretta Gol, Tennis Multi-Court).
    """
    if not text:
        return False
    t = str(text)
    if SIMULCAST_PATTERN.search(t):
        return True
    # Tennis multi-tournament concatenation (e.g. 'ATP 250 ... & WTA 500 ...') without vs
    if re.search(r"\b(atp|wta)\b", t, re.I) and bool(re.search(r"[&+,]", t)) and not bool(re.search(r"\s+(?:vs\.?|v\.?)\s+", t, re.I)):
        return True
    return False


def is_redzone_event(title: str) -> bool:
    """Detects if an event is NFL RedZone."""
    t = (title or "").lower()
    return bool(re.search(r"\bred\s*zone\b", t)) or "redzone" in t


def get_simulcast_info(text: str, hint: Optional[str] = None) -> Optional[Dict[str, str]]:
    """
    Returns normalized metadata for a simulcast show:
    {
        'title': display_title,
        'competition': comp_name,
        'silo': sport_silo,
        'canonical': canonical_slug,
        'genre': genre_name,
        'catalog': catalog_id
    }
    """
    if not is_simulcast_event(text):
        return None

    raw_combined = f"{hint or ''} {text}".lower()
    t = raw_combined.replace("-", " ").replace("_", " ")

    # 1. NFL RedZone
    if is_redzone_event(text):
        return {
            "title": "NFL RedZone",
            "competition": "NFL",
            "silo": "american-football",
            "canonical": "am_football_nfl",
            "genre": "NFL",
            "catalog": "football_americano",
        }

    # 2. Golazo Show
    if "golazo" in t:
        is_uel = any(k in t for k in ("europa", "conference", "uel", "uecl"))
        return {
            "title": "The Golazo Show",
            "competition": "UEFA Europa League" if is_uel else "UEFA Champions League",
            "silo": "football",
            "canonical": "football_uefa_el" if is_uel else "football_uefa_cl",
            "genre": "Europa e Conference League" if is_uel else "Champions League",
            "catalog": "calcio_estero",
        }

    # 3. Diretta Gol / Simulcast Serie A & Zona Serie A
    if re.search(r"\b(serie\s*a|zona\s*serie\s*a)\b", t):
        is_zona = "zona" in t
        return {
            "title": "Zona Serie A" if is_zona else "Diretta Gol Serie A",
            "competition": "Serie A",
            "silo": "football",
            "canonical": "football_serie_a",
            "genre": "Serie A",
            "catalog": "calcio_italiano",
        }

    # 4. Diretta Gol / Simulcast Serie B & Zona Serie B
    if re.search(r"\b(serie\s*b|zona\s*serie\s*b)\b", t):
        is_zona = "zona" in t
        return {
            "title": "Zona Serie B" if is_zona else "Diretta Gol Serie B",
            "competition": "Serie B",
            "silo": "football",
            "canonical": "football_serie_b",
            "genre": "Serie B",
            "catalog": "calcio_italiano",
        }

    # 5. Champions League Simulcast / Diretta Gol / MultiChampions
    if re.search(r"\b(champions|ucl|multichampions)\b", t):
        title = "Diretta Gol Champions League" if "diretta" in t else ("MultiChampions" if "multichampions" in t else "Simulcast (UEFA Champions League)")
        return {
            "title": title,
            "competition": "UEFA Champions League",
            "silo": "football",
            "canonical": "football_uefa_cl",
            "genre": "Champions League",
            "catalog": "calcio_estero",
        }

    # 6. Europa / Conference League Simulcast / Diretta Gol
    if re.search(r"\b(europa|conference|uel|uecl)\b", t):
        title = "Diretta Gol Europa League" if "diretta" in t else "Simulcast (UEFA Europa League)"
        return {
            "title": title,
            "competition": "UEFA Europa League",
            "silo": "football",
            "canonical": "football_uefa_el",
            "genre": "Europa e Conference League",
            "catalog": "calcio_estero",
        }

    # 7. Premier League Multi / Goal Rush
    if re.search(r"\b(premier|multipremier|goal\s*rush)\b", t):
        title = "Goal Rush (Premier League)" if "goal" in t else ("MultiPremier" if "multipremier" in t else "Diretta Gol Premier League")
        return {
            "title": title,
            "competition": "Premier League",
            "silo": "football",
            "canonical": "football_premier_league",
            "genre": "Premier League",
            "catalog": "calcio_estero",
        }

    # 8. Bundesliga Konferenz
    if re.search(r"\b(bundesliga|konferenz)\b", t):
        return {
            "title": "Bundesliga Konferenz",
            "competition": "Bundesliga",
            "silo": "football",
            "canonical": "football_bundesliga",
            "genre": "Bundesliga e Ligue 1",
            "catalog": "calcio_estero",
        }

    # 9. LaLiga MultiLiga
    if re.search(r"\b(laliga|la\s*liga|multiliga)\b", t):
        return {
            "title": "MultiLiga (LaLiga)",
            "competition": "La Liga",
            "silo": "football",
            "canonical": "football_la_liga",
            "genre": "La Liga",
            "catalog": "calcio_estero",
        }

    # 10. Ligue 1 Multifoot
    if re.search(r"\b(ligue\s*1|multifoot|multiplex)\b", t):
        return {
            "title": "Multifoot Ligue 1",
            "competition": "Ligue 1",
            "silo": "football",
            "canonical": "football_ligue_1",
            "genre": "Bundesliga e Ligue 1",
            "catalog": "calcio_estero",
        }

    # 11. Diretta Basket / Eurolega
    if re.search(r"\b(basket|eurolega|euroleague)\b", t):
        is_el = "euro" in t
        return {
            "title": "Diretta Eurolega" if is_el else "Diretta Basket",
            "competition": "EuroLeague" if is_el else "LBA Serie A",
            "silo": "basketball",
            "canonical": "basketball_euroleague" if is_el else "basketball_lba_serie_a",
            "genre": "Eurolega ed Eurocup" if is_el else "LBA Serie A",
            "catalog": "basket",
        }

    # 12. Tennis Multi-Court & Simulcast
    has_vs = bool(re.search(r"\s+(?:vs\.?|v\.?)\s+", text, re.I))
    is_tennis_sim = not has_vs and (
        bool(re.search(r"\b(diretta\s*tennis|early\s*rounds|tennis\s*channel)\b", t)) or (
            bool(re.search(r"\b(atp|wta|tennis)\b", t)) and bool(re.search(r"[&+,]", text))
        )
    )
    if is_tennis_sim:
        has_atp = "atp" in t
        has_wta = "wta" in t
        comp = "ATP & WTA" if (has_atp and has_wta) else ("WTA Tour" if has_wta else "ATP Tour")
        clean_t = re.sub(r"^[🎾\s\:\-]+", "", text).replace("🎾", "").strip()
        clean_t = re.sub(r"\s*\(tennis\)$", "", clean_t, flags=re.I).strip()
        genre = "Challenger e Altri" if (has_atp and has_wta) else ("WTA" if has_wta else "ATP")
        return {
            "title": clean_t,
            "competition": comp,
            "silo": "tennis",
            "canonical": "tennis_atp" if has_atp else "tennis_wta",
            "genre": genre,
            "catalog": "tennis",
        }

    # 13. Generic Diretta Gol / Zona Gol / Zona DAZN
    is_dazn = "dazn" in t
    title = "Zona DAZN" if is_dazn else ("Zona Gol" if "zona" in t else "Diretta Gol")
    is_italian = any(k in t for k in ("italia", "serie", "dazn", "sky"))
    return {
        "title": title,
        "competition": "Diretta Gol",
        "silo": "football",
        "canonical": "football_serie_a" if is_italian else "football_uefa_cl",
        "genre": "Serie A" if is_italian else "Champions League",
        "catalog": "calcio_italiano" if is_italian else "calcio_estero",
    }


def detect_canonical_competition(text: str, silo_hint: Optional[str] = None) -> Optional[str]:
    """
    Scans a combined string (title, raw category, ID, sources) for a canonical competition.
    If silo_hint is provided, prioritizes leagues in the same silo.
    """
    if not text:
        return None

    # Normalization: replace underscores and slashes with dashes/spaces
    t = text.lower().replace("_", "-").replace("/", " ")

    # 1. Match within silo_hint if provided
    if silo_hint:
        norm_silo = silo_hint.lower().strip()
        if norm_silo in ("soccer",): norm_silo = "football"
        if norm_silo in ("basket",): norm_silo = "basketball"
        if norm_silo in ("motorsport", "motori"): norm_silo = "motor-sports"
        if norm_silo in ("volleyball",): norm_silo = "volley"
        if norm_silo in ("combattimento", "boxe", "mma"): norm_silo = "fight"

        for slug, s_silo, display, regexes in _COMPILED_PATTERNS:
            if s_silo == norm_silo:
                for rx in regexes:
                    if rx.search(t):
                        return slug

        # Strict Silo Boundary: if an event belongs to a known sport silo (including 'altri_sport'),
        # it must NEVER cross-match into a completely different sport (e.g. handball/altri_sport -> football).
        if norm_silo not in ("all", "other", "unknown", ""):
            return None

    # 2. Match across all leagues ONLY if no specific sport silo was known
    for slug, s_silo, display, regexes in _COMPILED_PATTERNS:
        # Never match football if text explicitly mentions another sport
        if s_silo == "football" and re.search(r"\b(handball|hbl|ehf|volleyball|pallamano|pallavolo|rugby)\b", t):
            continue
        for rx in regexes:
            if rx.search(t):
                return slug

    return None


def get_canonical_display_name(slug: Optional[str]) -> Optional[str]:
    """Returns the human-friendly display name of a canonical competition slug."""
    if not slug or slug not in CANONICAL_LEAGUES:
        return None
    return CANONICAL_LEAGUES[slug]["display"]


def get_canonical_silo(slug: Optional[str]) -> Optional[str]:
    """Returns the sport silo of a canonical competition slug."""
    if not slug or slug not in CANONICAL_LEAGUES:
        return None
    return CANONICAL_LEAGUES[slug]["silo"]


def is_competition_conflict(canon1: Optional[str], canon2: Optional[str]) -> bool:
    """
    Returns True ONLY if BOTH events have a canonical competition defined,
    they belong to the SAME sport silo, and they are MUTUALLY EXCLUSIVE.
    (e.g. Serie A vs Serie B, or ATP vs WTA, or NBA vs Euroleague).
    """
    if not canon1 or not canon2:
        return False
    if canon1 == canon2:
        return False

    silo1 = get_canonical_silo(canon1)
    silo2 = get_canonical_silo(canon2)

    # If both belong to the same sport silo but are different competitions, it's a conflict!
    return silo1 is not None and silo2 is not None and silo1 == silo2
