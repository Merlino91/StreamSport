from __future__ import annotations
import asyncio
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Set
import httpx

from app.config import ENABLE_TVVOO, TVVOO_CACHE_INTERVAL

logger = logging.getLogger("streamsport.tvvoo_service")


class TvVooService:
    """
    Manages live international sports channels from the Vavoo / TvVoo network
    organized into strict national silos (Italy, Spain, Germany, France, United Kingdom)
    with official LCN channel numbering and strict numeric matching.
    Refreshes channel mappings every 6 hours (configurable via TVVOO_CACHE_INTERVAL).
    """

    PING_URL = "https://www.vypn.net/api/app/ping"
    CATALOG_URL = "https://vavoo.to/mediahubmx-catalog.json"
    RESOLVE_URL = "https://vavoo.to/mediahubmx-resolve.json"

    # Supported Vavoo country groups for sports broadcasting
    TVVOO_COUNTRY_GROUPS: List[str] = ["Italy", "Spain", "Germany", "France", "United Kingdom"]

    # Country suffix mapping for recognizing geographic broadcasts
    COUNTRY_SUFFIX_MAP: Dict[str, str] = {
        "it": "Italy",
        "italy": "Italy",
        "italia": "Italy",
        "de": "Germany",
        "germany": "Germany",
        "deutschland": "Germany",
        "es": "Spain",
        "spain": "Spain",
        "espana": "Spain",
        "fr": "France",
        "france": "France",
        "uk": "United Kingdom",
        "gb": "United Kingdom",
    }

    # Unsupported countries that must NOT fall back to European/Italian channels
    UNSUPPORTED_COUNTRY_SUFFIXES: Set[str] = {
        "ca", "canada",
        "us", "usa",
        "br", "brazil", "brasil",
        "nl", "netherlands",
        "pl", "poland", "polska",
        "ro", "romania",
        "hr", "croatia",
        "rs", "serbia",
        "gr", "greece",
        "cy", "cyprus",
        "il", "israel",
        "bg", "bulgaria",
        "al", "albania",
        "az", "azerbaijan",
        "pt", "portugal",
        "dk", "denmark",
        "se", "sweden",
        "no", "norway",
        "cz", "czech",
        "sk", "slovakia",
        "hu", "hungary",
        "tr", "turkey",
        "ru", "russia",
        "ua", "ukraine",
        "ar", "argentina",
        "mx", "mexico",
        "au", "australia",
        "nz", "new zealand",
        "za", "south africa",
    }

    # Country-specific alias mapping (Silos) including official LCN numbering
    COUNTRY_ALIASES: Dict[str, Dict[str, str]] = {
        "Italy": {
            # Sky Sport 24 (LCN 200)
            "sky sport 24": "sky sport 24",
            "sky sports 24": "sky sport 24",
            "sky sport24": "sky sport 24",
            "ss24": "sky sport 24",
            "sky sport 200": "sky sport 24",
            "sky 200": "sky sport 24",
            "200": "sky sport 24",

            # Sky Sport Uno (LCN 201)
            "sky sport uno": "sky sport uno",
            "sky sport 1": "sky sport uno",
            "sky sport 1 hd": "sky sport uno",
            "sky 1": "sky sport uno",
            "sky sport 201": "sky sport uno",
            "sky 201": "sky sport uno",
            "201": "sky sport uno",

            # Sky Sport Calcio (LCN 202)
            "sky sport calcio": "sky sport calcio",
            "sky sport calcio hd": "sky sport calcio",
            "sky calcio": "sky sport calcio",
            "sky sport 202": "sky sport calcio",
            "sky 202": "sky sport calcio",
            "202": "sky sport calcio",

            # Sky Sport Tennis (LCN 203)
            "sky sport tennis": "sky sport tennis",
            "sky sport tennis hd": "sky sport tennis",
            "sky tennis": "sky sport tennis",
            "sky sport 203": "sky sport tennis",
            "sky 203": "sky sport tennis",
            "203": "sky sport tennis",

            # Sky Sport Arena (LCN 204)
            "sky sport arena": "sky sport arena",
            "sky sport arena hd": "sky sport arena",
            "sky arena": "sky sport arena",
            "sky sport 204": "sky sport arena",
            "sky 204": "sky sport arena",
            "204": "sky sport arena",

            # Sky Sport Golf (LCN 205)
            "sky sport golf": "sky sport golf",
            "sky sport golf hd": "sky sport golf",
            "sky golf": "sky sport golf",
            "sky sport 205": "sky sport golf",
            "sky 205": "sky sport golf",
            "205": "sky sport golf",

            # Sky Sport Max (LCN 206)
            "sky sport max": "sky sport max",
            "sky sport max hd": "sky sport max",
            "sky max": "sky sport max",
            "sky sport 206": "sky sport max",
            "sky 206": "sky sport max",
            "206": "sky sport max",

            # Sky Sport F1 (LCN 207)
            "sky sport f1": "sky sport f1",
            "sky sport f1 hd": "sky sport f1",
            "sky f1": "sky sport f1",
            "sky sports f1": "sky sport f1",
            "sky sport 207": "sky sport f1",
            "sky 207": "sky sport f1",
            "207": "sky sport f1",

            # Sky Sport MotoGP (LCN 208)
            "sky sport motogp": "sky sport motogp",
            "sky sport moto gp": "sky sport motogp",
            "sky sport motogp hd": "sky sport motogp",
            "sky motogp": "sky sport motogp",
            "sky sport 208": "sky sport motogp",
            "sky 208": "sky sport motogp",
            "208": "sky sport motogp",

            # Sky Sport NBA (LCN 209)
            "sky sport nba": "sky sport nba",
            "sky sport nba hd": "sky sport nba",
            "sky nba": "sky sport nba",
            "sky sport 209": "sky sport nba",
            "sky 209": "sky sport nba",
            "209": "sky sport nba",

            # Sky Sport Mix & Serie A
            "sky sport mix": "sky sport mix",
            "sky sport serie a": "sky sport serie a",

            # Sky Sport Event Feed (Dedicated separate feed, NOT collapsed into Uno!)
            "sky sport": "sky sport",
            "sky sport it": "sky sport",
            "sky sport eventi": "sky sport",

            # Eurosport (LCN 210, 211)
            "eurosport": "eurosport 1",
            "eurosport 1": "eurosport 1",
            "eurosport 1 hd": "eurosport 1",
            "eurosport 210": "eurosport 1",
            "sky 210": "eurosport 1",
            "sky sport 210": "eurosport 1",
            "210": "eurosport 1",
            "eurosport 2": "eurosport 2",
            "eurosport 2 hd": "eurosport 2",
            "eurosport 211": "eurosport 2",
            "sky 211": "eurosport 2",
            "sky sport 211": "eurosport 2",
            "211": "eurosport 2",

            # SuperTennis (LCN 212)
            "supertennis": "supertennis",
            "super tennis": "supertennis",
            "supertennis hd": "supertennis",
            "sky super tennis": "supertennis",
            "supertennis 212": "supertennis",
            "sky 212": "supertennis",
            "sky sport 212": "supertennis",
            "212": "supertennis",

            # DAZN Italia (LCN 214, 215)
            "dazn": "dazn 1",
            "dazn 1": "dazn 1",
            "dazn 1 it": "dazn 1",
            "dazn 1 italia": "dazn 1",
            "dazn it": "dazn 1",
            "dazn italia": "dazn 1",
            "dazn 1 hd": "dazn 1",
            "zona dazn": "dazn 1",
            "zona dazn 214": "dazn 1",
            "dazn 214": "dazn 1",
            "sky 214": "dazn 1",
            "sky sport 214": "dazn 1",
            "214": "dazn 1",
            "dazn 2": "dazn 2",
            "dazn 2 it": "dazn 2",
            "dazn 2 italia": "dazn 2",
            "zona dazn 2": "dazn 2",
            "zona dazn 215": "dazn 2",
            "dazn 215": "dazn 2",
            "sky 215": "dazn 2",
            "sky sport 215": "dazn 2",
            "215": "dazn 2",

            # Rai Sport & Generalist FTA
            "rai sport": "rai sport",
            "rai sport +": "rai sport",
            "rai sport+": "rai sport",
            "rai sport + hd": "rai sport",
            "rai sport hd": "rai sport",
            "rai sport 1": "rai sport",
            "sportitalia": "sportitalia",
            "sportitalia plus": "sportitalia plus",
            "sportitalia solocalcio": "sportitalia solocalcio",
            "tv8": "tv8",
            "tv 8": "tv8",
            "cielo": "cielo",
            "canale 5": "canale 5",
            "canale 5 hd": "canale 5",
            "italia 1": "italia 1",
            "italia 1 hd": "italia 1",
            "rai 1": "rai 1",
            "rai 1 hd": "rai 1",
            "rai 2": "rai 2",
            "rai 2 hd": "rai 2",
            "rai 3": "rai 3",
            "rai 3 hd": "rai 3",
        },

        "Germany": {
            # Magenta Sport (1..10 and receiver channels 301..310)
            # Unnumbered 'magenta sport' ghost feed disabled by directive
            "magenta sport 1": "magenta sport 1",
            "magenta sport 301": "magenta sport 1",
            "301": "magenta sport 1",
            "magenta sport 2": "magenta sport 2",
            "magenta sport 302": "magenta sport 2",
            "302": "magenta sport 2",
            "magenta sport 3": "magenta sport 3",
            "magenta sport 303": "magenta sport 3",
            "303": "magenta sport 3",
            "magenta sport 4": "magenta sport 4",
            "magenta sport 304": "magenta sport 4",
            "304": "magenta sport 4",
            "magenta sport 5": "magenta sport 5",
            "magenta sport 305": "magenta sport 5",
            "305": "magenta sport 5",
            "magenta sport 6": "magenta sport 6",
            "magenta sport 306": "magenta sport 6",
            "306": "magenta sport 6",
            "magenta sport 7": "magenta sport 7",
            "magenta sport 307": "magenta sport 7",
            "307": "magenta sport 7",
            "magenta sport 8": "magenta sport 8",
            "magenta sport 308": "magenta sport 8",
            "308": "magenta sport 8",
            "magenta sport 9": "magenta sport 9",
            "magenta sport 309": "magenta sport 9",
            "309": "magenta sport 9",
            "magenta sport 10": "magenta sport 10",
            "magenta sport 310": "magenta sport 10",
            "310": "magenta sport 10",

            # Sky Sport Bundesliga DE
            "sky sport bundesliga": "sky sport bundesliga",
            "sky bundesliga": "sky sport bundesliga",
            "sky sport bundesliga 1": "sky sport bundesliga 1",
            "sky sport bundesliga 2": "sky sport bundesliga 2",
            "sky sport bundesliga 3": "sky sport bundesliga 3",
            "sky sport bundesliga 4": "sky sport bundesliga 4",
            "sky sport bundesliga 5": "sky sport bundesliga 5",
            "sky sport bundesliga 6": "sky sport bundesliga 6",
            "sky sport bundesliga 7": "sky sport bundesliga 7",
            "sky sport bundesliga 8": "sky sport bundesliga 8",
            "sky sport bundesliga 9": "sky sport bundesliga 9",
            "sky sport bundesliga 10": "sky sport bundesliga 10",

            # Sky Sport DE
            "sky sport top event": "sky sport top event",
            "sky top event": "sky sport top event",
            "sky sport premier league": "sky sport premier league de",
            "sky sport premier league de": "sky sport premier league de",
            "sky premier league de": "sky sport premier league de",
            "sky sport 1": "sky sport 1 de",
            "sky sport 1 de": "sky sport 1 de",
            "sky sport 2": "sky sport 2 de",
            "sky sport 2 de": "sky sport 2 de",
            "sky sport 3": "sky sport 3 de",
            "sky sport 3 de": "sky sport 3 de",
            "sky sport 4": "sky sport 4 de",
            "sky sport 4 de": "sky sport 4 de",
            "sky sport 5": "sky sport 5 de",
            "sky sport 5 de": "sky sport 5 de",
            "sky sport 6": "sky sport 6 de",
            "sky sport 6 de": "sky sport 6 de",
            "sky sport 7": "sky sport 7 de",
            "sky sport 7 de": "sky sport 7 de",
            "sky sport 8": "sky sport 8 de",
            "sky sport 8 de": "sky sport 8 de",
            "sky sport 9": "sky sport 9 de",
            "sky sport 9 de": "sky sport 9 de",
            "sky sport 10": "sky sport 10 de",
            "sky sport 10 de": "sky sport 10 de",
            "sky sport 11": "sky sport 11 de",
            "sky sport 11 de": "sky sport 11 de",
            "sky sport f1": "sky sport f1 de",
            "sky sport f1 de": "sky sport f1 de",
            "sky sport f1 germany": "sky sport f1 de",
            "sky sport tennis": "sky sport tennis de",
            "sky sport tennis de": "sky sport tennis de",
            "sky sport golf": "sky sport golf de",
            "sky sport golf de": "sky sport golf de",
            "sky sport mix": "sky sport mix de",
            "sky sport mix de": "sky sport mix de",

            # DAZN DE
            "dazn 1": "dazn 1 de",
            "dazn 1 de": "dazn 1 de",
            "dazn 1 germany": "dazn 1 de",
            "dazn 2": "dazn 2 de",
            "dazn 2 de": "dazn 2 de",
            "dazn 2 germany": "dazn 2 de",
            "dazn de": "dazn 1 de",
            "dazn germany": "dazn 1 de",
            "sport1": "sport1 de",
            "sport 1": "sport1 de",
            "sport1 de": "sport1 de",
            "sport 1 de": "sport1 de",
            "sport1 germany": "sport1 de",
        },

        "Spain": {
            # Movistar LaLiga
            "movistar laliga": "movistar laliga",
            "movistar laliga 1": "movistar laliga 1",
            "movistar laliga 2": "movistar laliga 2",
            "movistar laliga 3": "movistar laliga 3",
            "movistar laliga 4": "movistar laliga 4",
            "laliga tv por movistar plus": "movistar laliga",

            # Movistar Liga de Campeones
            "movistar liga de campeones": "movistar liga campeones",
            "movistar liga campeones": "movistar liga campeones",
            "movistar campeones": "movistar liga campeones",
            "movistar liga campeones 1": "movistar liga campeones 1",
            "movistar liga campeones 2": "movistar liga campeones 2",
            "movistar liga campeones 3": "movistar liga campeones 3",
            "movistar liga campeones 4": "movistar liga campeones 4",
            "movistar liga campeones 5": "movistar liga campeones 5",
            "movistar liga campeones 6": "movistar liga campeones 6",
            "movistar liga campeones 7": "movistar liga campeones 7",
            "movistar liga campeones 8": "movistar liga campeones 8",
            "movistar liga campeones 9": "movistar liga campeones 9",
            "movistar liga campeones 10": "movistar liga campeones 10",

            # Movistar Deportes & Vamos
            "movistar deportes": "movistar deportes",
            "movistar deporte 1": "movistar deportes 1",
            "movistar deportes 1": "movistar deportes 1",
            "movistar deportes 2": "movistar deportes 2",
            "movistar deportes 3": "movistar deportes 3",
            "movistar deportes 4": "movistar deportes 4",
            "movistar deportes 5": "movistar deportes 5",
            "movistar golf": "movistar golf",
            "movistar vamos": "movistar vamos",
            "movistar #vamos": "movistar vamos",

            # DAZN ES
            "dazn 1": "dazn 1 es",
            "dazn 1 es": "dazn 1 es",
            "dazn 1 esp": "dazn 1 es",
            "dazn 1 spain": "dazn 1 es",
            "dazn 2": "dazn 2 es",
            "dazn 2 es": "dazn 2 es",
            "dazn 2 esp": "dazn 2 es",
            "dazn 2 spain": "dazn 2 es",
            "dazn 3": "dazn 3 es",
            "dazn 3 es": "dazn 3 es",
            "dazn 3 spain": "dazn 3 es",
            "dazn 4": "dazn 4 es",
            "dazn 4 es": "dazn 4 es",
            "dazn 4 spain": "dazn 4 es",
            "dazn es": "dazn 1 es",
            "dazn esp": "dazn 1 es",
            "dazn espana": "dazn 1 es",
            "dazn spain": "dazn 1 es",
            "dazn laliga": "dazn laliga",
            "dazn laliga 1": "dazn laliga 1",
            "dazn laliga 2": "dazn laliga 2",
            "dazn f1": "dazn f1 es",
            "dazn f1 es": "dazn f1 es",
            "gol play": "gol play",
            "gol": "gol play",
            "teledeporte": "teledeporte",
            "tve teledeporte": "teledeporte",
            "bein sports la liga": "bein sports la liga",
        },

        "France": {
            # Canal+
            "canal+ sport": "canal plus sport",
            "canal sport": "canal plus sport",
            "canal plus sport": "canal plus sport",
            "canal+ foot": "canal plus foot",
            "canal foot": "canal plus foot",
            "canal plus foot": "canal plus foot",
            "canal+ 360": "canal plus 360",
            "canal 360": "canal plus 360",
            "canal plus 360": "canal plus 360",
            "canal+": "canal plus",
            "canal plus": "canal plus",
            "canal+ live 1": "canal plus live 1",
            "canal plus live 1": "canal plus live 1",
            "canal+ live 2": "canal plus live 2",
            "canal plus live 2": "canal plus live 2",
            "canal+ live 3": "canal plus live 3",
            "canal plus live 3": "canal plus live 3",

            # beIN Sports FR
            "bein sports 1": "bein sports 1 fr",
            "bein sports 1 fr": "bein sports 1 fr",
            "bein sports 1 france": "bein sports 1 fr",
            "bein sports hd 1 france": "bein sports 1 fr",
            "bein sports 2": "bein sports 2 fr",
            "bein sports 2 fr": "bein sports 2 fr",
            "bein sports 2 france": "bein sports 2 fr",
            "bein sports 3": "bein sports 3 fr",
            "bein sports 3 fr": "bein sports 3 fr",
            "bein sports 3 france": "bein sports 3 fr",
            "bein sports max 4": "bein sports max 4 fr",
            "bein sports max 4 fr": "bein sports max 4 fr",
            "bein sports max 5": "bein sports max 5 fr",
            "bein sports max 5 fr": "bein sports max 5 fr",
            "bein sports max 6": "bein sports max 6 fr",
            "bein sports max 6 fr": "bein sports max 6 fr",
            "bein sports max 7": "bein sports max 7 fr",
            "bein sports max 7 fr": "bein sports max 7 fr",
            "bein sports max 8": "bein sports max 8 fr",
            "bein sports max 8 fr": "bein sports max 8 fr",
            "bein sports max 9": "bein sports max 9 fr",
            "bein sports max 9 fr": "bein sports max 9 fr",
            "bein sports max 10": "bein sports max 10 fr",
            "bein sports max 10 fr": "bein sports max 10 fr",

            # DAZN FR
            "dazn 1": "dazn 1 fr",
            "dazn 1 fr": "dazn 1 fr",
            "dazn 2": "dazn 2 fr",
            "dazn 2 fr": "dazn 2 fr",
            "dazn 3": "dazn 3 fr",
            "dazn 3 fr": "dazn 3 fr",
            "dazn fr": "dazn 1 fr",
            "dazn france": "dazn 1 fr",
            "rmc sport 1": "rmc sport 1",
            "rmc sport 2": "rmc sport 2",
        },

        "United Kingdom": {
            # Sky Sports UK
            "sky sports main event": "sky sports main event",
            "sky main event": "sky sports main event",
            "sky sports premier league": "sky sports premier league uk",
            "sky sports football": "sky sports football uk",
            "sky sports f1": "sky sports f1 uk",
            "sky sports f1 uk": "sky sports f1 uk",
            "sky sports cricket": "sky sports cricket uk",
            "sky sports action": "sky sports action uk",
            "sky sports arena": "sky sports arena uk",
            "sky sports arena uk": "sky sports arena uk",
            "sky sports golf": "sky sports golf uk",
            "sky sports tennis": "sky sports tennis uk",
            "sky sports news": "sky sports news uk",
            "sky sports mix": "sky sports mix uk",
            "sky sports nfl": "sky sports nfl",

            # TNT Sports UK
            "tnt sports 1": "tnt sports 1",
            "tnt sport 1": "tnt sports 1",
            "bt sport 1": "tnt sports 1",
            "tnt sports 2": "tnt sports 2",
            "tnt sport 2": "tnt sports 2",
            "bt sport 2": "tnt sports 2",
            "tnt sports 3": "tnt sports 3",
            "tnt sport 3": "tnt sports 3",
            "bt sport 3": "tnt sports 3",
            "tnt sports 4": "tnt sports 4",
            "tnt sport 4": "tnt sports 4",
            "bt sport 4": "tnt sports 4",
            "tnt sports ultimate": "tnt sports ultimate",
            "bbc one": "bbc one",
            "bbc two": "bbc two",
            "itv 1": "itv 1",
            "itv 4": "itv 4",
        },

        "World": {
            "euroleague tv": "euroleague tv",
            "nba tv": "nba tv",
        }
    }

    # Flat combined alias dictionary for global queries (country not specified)
    # Load foreign countries first, then Italy so Italy takes precedence on ambiguous global keys
    CHANNEL_ALIASES: Dict[str, str] = {}
    for _grp_name in ["World", "United Kingdom", "France", "Spain", "Germany", "Italy"]:
        if _grp_name in COUNTRY_ALIASES:
            CHANNEL_ALIASES.update(COUNTRY_ALIASES[_grp_name])

    # Display names formatted nicely with proper capitalization and country flags
    CANONICAL_DISPLAY_NAMES: Dict[str, str] = {
        # Italy
        "sky sport uno": "Sky Sport Uno",
        "sky sport calcio": "Sky Sport Calcio",
        "sky sport tennis": "Sky Sport Tennis",
        "sky sport arena": "Sky Sport Arena",
        "sky sport f1": "Sky Sport F1",
        "sky sport motogp": "Sky Sport MotoGP",
        "sky sport nba": "Sky Sport NBA",
        "sky sport 24": "Sky Sport 24",
        "sky sport golf": "Sky Sport Golf",
        "sky sport max": "Sky Sport Max",
        "sky sport mix": "Sky Sport Mix",
        "sky sport serie a": "Sky Sport Serie A",
        "sky sport": "Sky Sport",
        "dazn 1": "DAZN 1",
        "dazn 2": "DAZN 2",
        "eurosport 1": "Eurosport 1",
        "eurosport 2": "Eurosport 2",
        "rai sport": "Rai Sport",
        "sportitalia": "Sportitalia",
        "sportitalia plus": "Sportitalia Plus",
        "sportitalia solocalcio": "Sportitalia SoloCalcio",
        "supertennis": "SuperTennis",
        "tv8": "TV8",
        "cielo": "Cielo",
        "canale 5": "Canale 5",
        "italia 1": "Italia 1",
        "rai 1": "Rai 1",
        "rai 2": "Rai 2",
        "rai 3": "Rai 3",

        # Germany
        "magenta sport 1": "🇩🇪 Magenta Sport 1",
        "magenta sport 2": "🇩🇪 Magenta Sport 2",
        "magenta sport 3": "🇩🇪 Magenta Sport 3",
        "magenta sport 4": "🇩🇪 Magenta Sport 4",
        "magenta sport 5": "🇩🇪 Magenta Sport 5",
        "magenta sport 6": "🇩🇪 Magenta Sport 6",
        "magenta sport 7": "🇩🇪 Magenta Sport 7",
        "magenta sport 8": "🇩🇪 Magenta Sport 8",
        "magenta sport 9": "🇩🇪 Magenta Sport 9",
        "magenta sport 10": "🇩🇪 Magenta Sport 10",
        "sky sport bundesliga": "🇩🇪 Sky Sport Bundesliga",
        "sky sport bundesliga 1": "🇩🇪 Sky Sport Bundesliga 1",
        "sky sport bundesliga 2": "🇩🇪 Sky Sport Bundesliga 2",
        "sky sport bundesliga 3": "🇩🇪 Sky Sport Bundesliga 3",
        "sky sport bundesliga 4": "🇩🇪 Sky Sport Bundesliga 4",
        "sky sport bundesliga 5": "🇩🇪 Sky Sport Bundesliga 5",
        "sky sport top event": "🇩🇪 Sky Sport Top Event",
        "sky sport premier league de": "🇩🇪 Sky Sport Premier League",
        "sky sport 1 de": "🇩🇪 Sky Sport 1 DE",
        "sky sport 2 de": "🇩🇪 Sky Sport 2 DE",
        "sky sport 3 de": "🇩🇪 Sky Sport 3 DE",
        "sky sport 4 de": "🇩🇪 Sky Sport 4 DE",
        "sky sport 5 de": "🇩🇪 Sky Sport 5 DE",
        "sky sport 6 de": "🇩🇪 Sky Sport 6 DE",
        "sky sport 7 de": "🇩🇪 Sky Sport 7 DE",
        "sky sport 8 de": "🇩🇪 Sky Sport 8 DE",
        "sky sport 9 de": "🇩🇪 Sky Sport 9 DE",
        "sky sport 10 de": "🇩🇪 Sky Sport 10 DE",
        "sky sport 11 de": "🇩🇪 Sky Sport 11 DE",
        "sky sport f1 de": "🇩🇪 Sky Sport F1 DE",
        "sky sport tennis de": "🇩🇪 Sky Sport Tennis DE",
        "sky sport golf de": "🇩🇪 Sky Sport Golf DE",
        "sky sport mix de": "🇩🇪 Sky Sport Mix DE",
        "dazn 1 de": "🇩🇪 DAZN 1 DE",
        "dazn 2 de": "🇩🇪 DAZN 2 DE",
        "sport1 de": "🇩🇪 Sport1 DE",

        # Spain
        "movistar laliga": "🇪🇸 Movistar LaLiga",
        "movistar laliga 1": "🇪🇸 Movistar LaLiga 1",
        "movistar laliga 2": "🇪🇸 Movistar LaLiga 2",
        "movistar laliga 3": "🇪🇸 Movistar LaLiga 3",
        "movistar laliga 4": "🇪🇸 Movistar LaLiga 4",
        "movistar liga campeones": "🇪🇸 Movistar Liga de Campeones",
        "movistar liga campeones 1": "🇪🇸 Movistar Liga de Campeones 1",
        "movistar liga campeones 2": "🇪🇸 Movistar Liga de Campeones 2",
        "movistar liga campeones 3": "🇪🇸 Movistar Liga de Campeones 3",
        "movistar deportes": "🇪🇸 Movistar Deportes",
        "movistar deportes 1": "🇪🇸 Movistar Deportes 1",
        "movistar deportes 2": "🇪🇸 Movistar Deportes 2",
        "movistar golf": "🇪🇸 Movistar Golf",
        "movistar vamos": "🇪🇸 Movistar #Vamos",
        "dazn 1 es": "🇪🇸 DAZN 1 ES",
        "dazn 2 es": "🇪🇸 DAZN 2 ES",
        "dazn 3 es": "🇪🇸 DAZN 3 ES",
        "dazn 4 es": "🇪🇸 DAZN 4 ES",
        "dazn laliga": "🇪🇸 DAZN LaLiga",
        "dazn laliga 1": "🇪🇸 DAZN LaLiga 1",
        "dazn laliga 2": "🇪🇸 DAZN LaLiga 2",
        "dazn f1 es": "🇪🇸 DAZN F1 ES",
        "gol play": "🇪🇸 Gol Play",
        "teledeporte": "🇪🇸 Teledeporte",
        "bein sports la liga": "🇪🇸 beIN Sports LaLiga",

        # France
        "canal plus sport": "🇫🇷 Canal+ Sport",
        "canal plus foot": "🇫🇷 Canal+ Foot",
        "canal plus 360": "🇫🇷 Canal+ 360",
        "canal plus": "🇫🇷 Canal+",
        "canal plus live 1": "🇫🇷 Canal+ Live 1",
        "canal plus live 2": "🇫🇷 Canal+ Live 2",
        "bein sports 1 fr": "🇫🇷 beIN Sports 1",
        "bein sports 2 fr": "🇫🇷 beIN Sports 2",
        "bein sports 3 fr": "🇫🇷 beIN Sports 3",
        "bein sports max 4 fr": "🇫🇷 beIN Sports Max 4",
        "bein sports max 5 fr": "🇫🇷 beIN Sports Max 5",
        "bein sports max 6 fr": "🇫🇷 beIN Sports Max 6",
        "dazn 1 fr": "🇫🇷 DAZN 1 FR",
        "dazn 2 fr": "🇫🇷 DAZN 2 FR",
        "dazn 3 fr": "🇫🇷 DAZN 3 FR",
        "rmc sport 1": "🇫🇷 RMC Sport 1",
        "rmc sport 2": "🇫🇷 RMC Sport 2",

        # United Kingdom
        "sky sports main event": "🇬🇧 Sky Sports Main Event",
        "sky sports premier league uk": "🇬🇧 Sky Sports Premier League",
        "sky sports football uk": "🇬🇧 Sky Sports Football",
        "sky sports f1 uk": "🇬🇧 Sky Sports F1",
        "sky sports cricket uk": "🇬🇧 Sky Sports Cricket",
        "sky sports action uk": "🇬🇧 Sky Sports Action",
        "sky sports arena uk": "🇬🇧 Sky Sports Arena",
        "sky sports golf uk": "🇬🇧 Sky Sports Golf",
        "sky sports tennis uk": "🇬🇧 Sky Sports Tennis",
        "sky sports news uk": "🇬🇧 Sky Sports News",
        "sky sports mix uk": "🇬🇧 Sky Sports Mix",
        "sky sports nfl": "🇬🇧 Sky Sports NFL",
        "tnt sports 1": "🇬🇧 TNT Sports 1",
        "tnt sports 2": "🇬🇧 TNT Sports 2",
        "tnt sports 3": "🇬🇧 TNT Sports 3",
        "tnt sports 4": "🇬🇧 TNT Sports 4",
        "tnt sports ultimate": "🇬🇧 TNT Sports Ultimate",
        "bbc one": "🇬🇧 BBC One",
        "bbc two": "🇬🇧 BBC Two",
        "itv 1": "🇬🇧 ITV 1",
        "itv 4": "🇬🇧 ITV 4",

        # World
        "euroleague tv": "🌍 EuroLeague TV",
        "nba tv": "🌍 NBA TV",
    }

    def __init__(self):
        self._channels_by_canonical: Dict[str, List[Dict[str, Any]]] = {}
        self._last_sync_time: float = 0.0
        self._sync_lock = asyncio.Lock()
        self._addon_sig: Optional[str] = None
        self._sig_time: float = 0.0

    @staticmethod
    def cleanup_channel_name(name: str) -> str:
        """Cleans and normalizes channel name string for dictionary matching."""
        if not name:
            return ""
        s = name.lower()
        # Remove flag emojis
        s = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", s)
        # Remove server tags like .c, .s, .b, .p
        s = re.sub(r"\s*\.[a-z0-9]{1,3}\b", "", s)
        # Remove bracketed tags like [live during events only]
        s = re.sub(r"\[[^\]]*\]", "", s)
        # Remove parentheses tags
        s = re.sub(r"\([^)]*\)", "", s)
        # Remove quality markers and codecs
        s = re.sub(r"\b(fhd|uhd|4k|sd|hd\+?|hevc|raw|h\.?265)\b", "", s)
        # Normalize punctuation preserving # and +
        s = re.sub(r"[^a-z0-9#+]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    def get_canonical_key(self, channel_name: str, country: Optional[str] = None) -> Optional[str]:
        """
        Maps a given channel name to a canonical key using country silos and strict numeric matching.
        Prevents greedy partial match collisions across numbered channels.
        """
        cleaned = self.cleanup_channel_name(channel_name)
        if not cleaned:
            return None

        # Check trailing country word/code (e.g. 'dazn ca', 'dazn de', 'bein sports 1 france')
        trailing_word_match = re.search(r"\b([a-z]{2,12})$", cleaned)
        if trailing_word_match and country is None:
            trailing_code = trailing_word_match.group(1)
            if trailing_code in self.UNSUPPORTED_COUNTRY_SUFFIXES:
                # Explicit non-supported foreign broadcast (e.g. Canada, USA, Brazil) -> Do NOT fallback to Italy
                return None
            if trailing_code in self.COUNTRY_SUFFIX_MAP:
                country = self.COUNTRY_SUFFIX_MAP[trailing_code]

        # Build candidate dictionaries: prioritize country silo if specified or detected
        candidate_dicts: List[Dict[str, str]] = []
        if country and country in self.COUNTRY_ALIASES:
            # When country is specified or detected, ONLY search that country's silo (never contaminate with Italy)
            candidate_dicts.append(self.COUNTRY_ALIASES[country])
        else:
            # Fallback for country-agnostic queries: Italy first, then global aliases
            candidate_dicts.append(self.COUNTRY_ALIASES.get("Italy", {}))
            candidate_dicts.append(self.CHANNEL_ALIASES)

        for aliases_map in candidate_dicts:
            # 1. Exact match
            if cleaned in aliases_map:
                return aliases_map[cleaned]

            # 2. Match without symbols
            cleaned_no_sym = re.sub(r"[#+]", " ", cleaned).strip()
            cleaned_no_sym = re.sub(r"\s+", " ", cleaned_no_sym)
            if cleaned_no_sym in aliases_map:
                return aliases_map[cleaned_no_sym]

            # 3. Match with common noise words stripped (e.g., 'hd', 'uhd', 'fhd', 'tv', 'channel', 'canale', 'live')
            cleaned_no_noise = re.sub(r"\b(hd|uhd|fhd|sd|tv|channel|canale|live)\b", " ", cleaned)
            cleaned_no_noise = re.sub(r"\s+", " ", cleaned_no_noise).strip()
            if cleaned_no_noise and cleaned_no_noise in aliases_map:
                return aliases_map[cleaned_no_noise]

            # 4. Strict Number Match
            # Only match aliases that share the EXACT same set of numbers as cleaned.
            nums_in_cleaned = set(re.findall(r"\b\d+\b", cleaned))
            if nums_in_cleaned:
                for alias, canonical in aliases_map.items():
                    nums_in_alias = set(re.findall(r"\b\d+\b", alias))
                    if nums_in_cleaned != nums_in_alias:
                        continue
                    # Must be a word-bounded substring (e.g. 'sky sport 1' in 'sky sport 1 bar')
                    # Never allow reverse matching (cleaned in alias) to prevent false matches
                    pattern = r"\b" + re.escape(alias) + r"\b"
                    if re.search(pattern, cleaned):
                        return canonical

        return None

    async def get_vavoo_signature(self, client_ip: Optional[str] = None) -> Optional[str]:
        """Obtains session authorization signature via Vavoo ping endpoint."""
        now = time.time()
        if self._addon_sig and (now - self._sig_time) < 1800:
            return self._addon_sig

        payload = {
            "token": "",
            "reason": "app-focus",
            "locale": "de",
            "theme": "dark",
            "metadata": {
                "device": {"type": "phone", "uniqueId": uuid.uuid4().hex[:16]},
                "os": {"name": "android", "version": "14", "abis": ["arm64-v8a"], "host": "android"},
                "app": {"platform": "android"},
                "version": {"package": "net.vypn.app", "binary": "1.4.1", "js": "1.4.1"},
            },
            "appFocusTime": 0,
            "playerActive": False,
            "playDuration": 0,
            "devMode": False,
            "hasAddon": True,
            "castConnected": False,
            "package": "net.vypn.app",
            "version": "1.4.1",
        }

        headers = {
            "user-agent": "okhttp/4.11.0",
            "accept": "application/json",
            "content-type": "application/json; charset=utf-8",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(self.PING_URL, headers=headers, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    sig = data.get("addonSig")
                    if sig:
                        self._addon_sig = sig
                        self._sig_time = now
                        logger.info("Successfully acquired new Vavoo session signature.")
                        return sig
        except Exception as e:
            logger.warning("Failed to obtain Vavoo signature: %s", e)
        return None

    async def sync_channels(self, force: bool = False) -> Dict[str, List[Dict[str, Any]]]:
        """
        Synchronizes sports channels across all supported country groups
        (Italy, Spain, Germany, France, United Kingdom) from the live Vavoo catalog.
        Caches in RAM for 6 hours unless forced.
        """
        if not ENABLE_TVVOO:
            return {}

        now = time.time()
        if not force and self._channels_by_canonical and (now - self._last_sync_time) < TVVOO_CACHE_INTERVAL:
            return self._channels_by_canonical

        async with self._sync_lock:
            # Double-check inside lock
            if not force and self._channels_by_canonical and (now - self._last_sync_time) < TVVOO_CACHE_INTERVAL:
                return self._channels_by_canonical

            sig = await self.get_vavoo_signature()
            if not sig:
                logger.warning("Cannot sync Vavoo channels without a valid session signature.")
                return self._channels_by_canonical

            cat_headers = {
                "user-agent": "okhttp/4.11.0",
                "accept": "application/json",
                "content-type": "application/json; charset=utf-8",
                "mediahubmx-signature": sig,
            }

            all_raw_items: List[Dict[str, Any]] = []

            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    for group in self.TVVOO_COUNTRY_GROUPS:
                        cursor: Any = 0
                        while True:
                            cat_body = {
                                "language": "de",
                                "region": "AT",
                                "catalogId": "iptv",
                                "id": "iptv",
                                "adult": False,
                                "search": "",
                                "sort": "name",
                                "filter": {"group": group},
                                "cursor": cursor,
                                "clientVersion": "3.1.0",
                            }
                            r_cat = await client.post(self.CATALOG_URL, headers=cat_headers, json=cat_body)
                            if r_cat.status_code != 200:
                                break
                            data = r_cat.json()
                            items = data.get("items", [])
                            if not items:
                                break
                            for it in items:
                                it["_country_group"] = group
                            all_raw_items.extend(items)
                            cursor = data.get("nextCursor")
                            if not cursor:
                                break
            except Exception as e:
                logger.error("Error fetching Vavoo multi-country catalog: %s", e)
                return self._channels_by_canonical

            # Group and map channels to canonical names respecting country silos
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for it in all_raw_items:
                raw_name = it.get("name", "")
                url = it.get("url") or it.get("play") or ""
                country_group = it.get("_country_group", "")
                if not url:
                    continue

                canonical = self.get_canonical_key(raw_name, country=country_group)
                if not canonical:
                    canonical = self.get_canonical_key(raw_name)
                if not canonical:
                    continue

                # Determine server/mirror tag (.c, .s, or .b)
                raw_lower = raw_name.lower()
                tag = "c" if ".c" in raw_lower else ("s" if ".s" in raw_lower else ("b" if ".b" in raw_lower else "live"))
                display_name = self.CANONICAL_DISPLAY_NAMES.get(canonical, canonical.title())

                channel_entry = {
                    "canonical": canonical,
                    "display_name": display_name,
                    "url": url,
                    "raw_name": raw_name,
                    "tag": tag,
                    "country": country_group,
                }

                # Store under canonical key
                if canonical not in grouped:
                    grouped[canonical] = []
                grouped[canonical].append(channel_entry)

                # Also store under country-scoped key (e.g. "it:sky sport uno", "de:magenta sport 3")
                scoped_key = f"{country_group.lower()[:2]}:{canonical}"
                if scoped_key not in grouped:
                    grouped[scoped_key] = []
                grouped[scoped_key].append(channel_entry)

            # Sort entries for each canonical: tag .c first, then .s, then .b
            tag_order = {"c": 0, "s": 1, "b": 2, "live": 3}
            for k in grouped:
                grouped[k].sort(key=lambda x: tag_order.get(x.get("tag", "live"), 4))

            if grouped:
                self._channels_by_canonical = grouped
                self._last_sync_time = time.time()
                logger.info(
                    "TvVoo sync complete. Indexed %d canonical sports channels across 5 countries (%d total stream URLs).",
                    len(grouped),
                    sum(len(v) for v in grouped.values()),
                )

            return self._channels_by_canonical

    async def ensure_synced(self):
        """Ensures that channel mappings are populated in RAM."""
        if not self._channels_by_canonical:
            await self.sync_channels()

    def get_channel_streams(self, channel_name: str, country: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Finds all available TvVoo stream targets for a requested TV channel.
        Optionally scopes to a specific country silo (e.g., 'Italy', 'Germany').
        Returns a list of dicts with 'canonical', 'url', 'display_name', 'tag', 'country'.
        """
        canonical = self.get_canonical_key(channel_name, country=country)
        if not canonical:
            return []

        # If country is provided, check scoped key first
        if country:
            scoped_key = f"{country.lower()[:2]}:{canonical}"
            if scoped_key in self._channels_by_canonical:
                return list(self._channels_by_canonical[scoped_key])

        return list(self._channels_by_canonical.get(canonical, []))

    def has_channel(self, channel_name: str, country: Optional[str] = None) -> bool:
        """Returns True if the requested channel exists and has at least one valid stream."""
        canonical = self.get_canonical_key(channel_name, country=country)
        if not canonical:
            return False
        if country:
            scoped_key = f"{country.lower()[:2]}:{canonical}"
            if scoped_key in self._channels_by_canonical:
                return len(self._channels_by_canonical[scoped_key]) > 0
        return len(self._channels_by_canonical.get(canonical, [])) > 0

    def enrich_matches_with_tvvoo(self, matches: List[Dict[str, Any]]) -> int:
        """
        Attaches TvVoo high-definition streams to matches that contain recognized sports channels
        (from DaddyLive, Streamed, or external listings) across Italy, Spain, Germany, France, UK, World.
        """
        if not self._channels_by_canonical or not matches:
            return 0

        enriched_count = 0
        for m in matches:
            sources = m.get("sources")
            if not isinstance(sources, list):
                continue

            existing_urls = {s.get("url") for s in sources if isinstance(s, dict) and s.get("url")}
            existing_ids = {str(s.get("id")) for s in sources if isinstance(s, dict)}

            new_tvvoo_streams: List[Dict[str, Any]] = []

            for s in list(sources):
                if not isinstance(s, dict):
                    continue
                # If this source is from DaddyLive or another external listing, check its channel name
                s_name = s.get("name") or ""
                if not s_name or s.get("source") == "tvvoo":
                    continue

                streams = self.get_channel_streams(s_name)
                for st in streams:
                    u = st.get("url")
                    c_id = st.get("canonical")
                    if u and u not in existing_urls and c_id not in existing_ids:
                        new_tvvoo_streams.append({
                            "source": "tvvoo",
                            "id": c_id,
                            "name": st["display_name"],
                            "url": u,
                            "tag": st.get("tag", "c"),
                            "country": st.get("country", ""),
                        })
                        existing_urls.add(u)
                        existing_ids.add(c_id)

            if new_tvvoo_streams:
                # Prepend TvVoo streams at index 0 of match sources
                for tv_s in reversed(new_tvvoo_streams):
                    sources.insert(0, tv_s)
                enriched_count += 1

        if enriched_count > 0:
            logger.info("TvVoo: agganciati flussi FHD diretti a %d match con emittenti internazionali.", enriched_count)
        return enriched_count


tvvoo_service = TvVooService()
