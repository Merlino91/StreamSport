import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from app.config import TV_CHANNELS_FILE, CATALOG_TYPE

logger = logging.getLogger("streamsport.italian_resolver")

ITALIAN_CHANNELS_METAS = [
    {
        "id": "skysportcalcio",
        "dlhd_id": "867",
        "name": "Sky Sport Calcio",
        "genres": ["Serie A", "Serie B", "Champions League", "Europa e Conference League", "Premier League", "Bundesliga e Ligue 1", "Altri Campionati Europei"],
        "poster": "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/italy/sky-sport-calcio-it.png",
        "description": "🔴 Diretta TV 24/7 • Serie A, Premier League, Bundesliga, rubriche e approfondimenti di Sky Sport."
    },
    {
        "id": "dazn1",
        "dlhd_id": "875",
        "name": "DAZN 1",
        "genres": ["Serie A", "Serie B", "La Liga", "UFC", "MMA", "Boxe", "NFL"],
        "poster": "https://upload.wikimedia.org/wikipedia/commons/d/d6/Dazn-logo.png",
        "description": "🔴 Diretta TV 24/7 • Serie A Enilive, LaLiga EA Sports, Zona DAZN e sport da combattimento."
    },
    {
        "id": "skysportuno",
        "dlhd_id": "866",
        "name": "Sky Sport Uno",
        "genres": ["Serie A", "Champions League", "Europa e Conference League", "Premier League", "ATP", "WTA", "Grandi Slam"],
        "poster": "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/italy/sky-sport-uno-it.png",
        "description": "🔴 Diretta TV 24/7 • Grandi eventi, UEFA Champions League, Premier League e finali ATP."
    },
    {
        "id": "skysportf1",
        "dlhd_id": "871",
        "name": "Sky Sport F1",
        "genres": ["Formula 1"],
        "poster": "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/italy/sky-sport-f1-it.png",
        "description": "🔴 Diretta TV 24/7 • Tutto il mondiale di Formula 1, F2, F3, Porsche Supercup e dirette box."
    },
    {
        "id": "skysportmotogp",
        "dlhd_id": "872",
        "name": "Sky Sport MotoGP",
        "genres": ["MotoGP e Superbike"],
        "poster": "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/italy/sky-sport-motogp-it.png",
        "description": "🔴 Diretta TV 24/7 • MotoGP, Moto2, Moto3 e Superbike con telecronaca italiana."
    },
    {
        "id": "skysporttennis",
        "dlhd_id": "870",
        "name": "Sky Sport Tennis",
        "genres": ["ATP", "WTA", "Coppa Davis e BJK Cup", "Grandi Slam", "Challenger e Altri"],
        "poster": "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/italy/sky-sport-tennis-it.png",
        "description": "🔴 Diretta TV 24/7 • Tornei ATP Masters 1000, ATP 500, Wimbledon e Nitto ATP Finals."
    },
    {
        "id": "skysportnba",
        "dlhd_id": "873",
        "name": "Sky Sport NBA",
        "genres": ["NBA"],
        "poster": "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/italy/sky-sport-nba-it.png",
        "description": "🔴 Diretta TV 24/7 • Partite NBA in diretta con commento in italiano e programmi speciali."
    },
    {
        "id": "skysportarena",
        "dlhd_id": "869",
        "name": "Sky Sport Arena",
        "genres": ["Champions League", "Europa e Conference League", "Eurolega e Eurocup", "LBA Serie A", "NASCAR e IndyCar", "Rally e WRC", "Superlega e Serie A1", "Champions League Volley", "Rugby e AFL"],
        "poster": "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/italy/sky-sport-arena-it.png",
        "description": "🔴 Diretta TV 24/7 • Eurolega di basket, rugby, volley e sport internazionali."
    },
    {
        "id": "tv8",
        "dlhd_id": "852",
        "name": "TV8",
        "genres": ["Formula 1", "MotoGP e Superbike", "Europa e Conference League"],
        "poster": "https://cdn.jsdelivr.net/gh/Tundrak/IPTV-Italia/logos/tv8.png",
        "description": "🔴 Canale TV8 • Gare e qualifiche di F1 e MotoGP in chiaro, partite di Europa League."
    },
    {
        "id": "raisport",
        "dlhd_id": "882",
        "name": "Rai Sport + HD",
        "genres": ["Serie C", "Coppa Italia e Supercoppa", "Superlega e Serie A1", "Volley Femminile", "Ciclismo", "Biliardo, Padel e Altri"],
        "poster": "https://cdn.jsdelivr.net/gh/Tundrak/IPTV-Italia/logos/raisport+hd.png",
        "description": "🔴 Rai Sport • Serie C, Coppa Italia, atletica, ciclismo e campionati italiani."
    },
    {
        "id": "eurosport1",
        "dlhd_id": "876",
        "name": "Eurosport 1",
        "genres": ["ATP", "WTA", "Grandi Slam", "Ciclismo", "NASCAR e IndyCar", "Biliardo, Padel e Altri"],
        "poster": "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/spain/eurosport-1-es.png",
        "description": "🔴 Eurosport 1 • Australian Open, Roland Garros, Giochi Olimpici e sport invernali."
    },
    {
        "id": "eurosport2",
        "dlhd_id": "877",
        "name": "Eurosport 2",
        "genres": ["Eurolega e Eurocup", "Ciclismo", "Biliardo, Padel e Altri"],
        "poster": "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/spain/eurosport-1-es.png",
        "description": "🔴 Eurosport 2 • Eurolega, ciclismo, sport motoristici ed eventi internazionali."
    }
]

class ItalianResolver:
    """
    Provides Italian sports broadcasts (Sky Sport, DAZN, Eurosport, TV8, Rai)
    integrated directly with EasyProxy.
    """

    def __init__(self):
        self._channels_by_id = {c["id"]: c for c in ITALIAN_CHANNELS_METAS}

    def get_channel_metas_for_genre(self, target_genre: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Returns catalog meta items for Italian channels belonging to the given genre.
        """
        results: List[Dict[str, Any]] = []
        is_all = not target_genre or target_genre in ("Tutti gli Eventi", "all", "All", "All Sports")

        for ch in ITALIAN_CHANNELS_METAS:
            if not is_all and target_genre not in ch["genres"]:
                continue

            item = {
                "id": f"streamsport:channel:{ch['id']}",
                "type": CATALOG_TYPE,
                "name": f"📺 {ch['name']} (Live 24/7)",
                "genres": ch["genres"],
                "poster": ch["poster"],
                "posterShape": "square",
                "description": ch["description"],
                "releaseInfo": "Diretta 24/7",
            }
            results.append(item)

        return results

    def get_channel_meta_by_id(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """
        Returns full meta detail for a single channel ID.
        """
        clean_id = channel_id.replace("channel:", "").strip()
        ch = self._channels_by_id.get(clean_id)
        if not ch:
            return None

        return {
            "id": f"streamsport:channel:{ch['id']}",
            "type": CATALOG_TYPE,
            "name": f"📺 {ch['name']} (Live 24/7)",
            "genres": ch["genres"],
            "poster": ch["poster"],
            "background": ch["poster"],
            "posterShape": "square",
            "description": ch["description"],
            "releaseInfo": "Diretta 24/7",
        }

    def get_stream_for_channel(
        self,
        channel_id: str,
        ep_url: Optional[str],
        ep_pass: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns the stream playback entry for a 24/7 Italian channel.
        """
        clean_id = channel_id.replace("channel:", "").strip()
        ch = self._channels_by_id.get(clean_id)
        if not ch:
            return []

        dlhd_id = ch["dlhd_id"]
        ch_name = ch["name"]

        if ep_url:
            base_ep = ep_url.rstrip("/")
            proxy_url = f"{base_ep}/extractor/video.m3u8?host=dlhd&id={dlhd_id}&api_password={ep_pass or ''}"
            return [
                {
                    "name": f"[IT 🇮🇹] {ch_name}",
                    "title": f"📺 {ch_name} • Diretta TV 24/7\n🚀 EasyProxy Playback",
                    "url": proxy_url,
                    "behaviorHints": {
                        "notWebReady": False,
                    },
                }
            ]
        else:
            return [
                {
                    "name": f"[IT 🇮🇹] {ch_name}",
                    "title": f"📺 {ch_name} • Diretta TV 24/7\n⚠️ Configura EasyProxy nel pannello per avviare",
                    "url": "",
                    "behaviorHints": {
                        "notWebReady": True,
                    },
                }
            ]

    def get_italian_streams_for_genre(
        self,
        genre: str,
        ep_url: Optional[str] = None,
        ep_pass: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns suggested Italian streams for a match based on its genre.
        """
        streams: List[Dict[str, Any]] = []
        for ch in ITALIAN_CHANNELS_METAS:
            if genre in ch["genres"]:
                streams.extend(self.get_stream_for_channel(ch["id"], ep_url, ep_pass))

        return streams

italian_resolver = ItalianResolver()
