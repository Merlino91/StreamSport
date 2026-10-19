"""
Freeshot Italian TV Channels Service.

Manages the mapping and stream resolution for Italian linear TV channels hosted
on the Freeshot / WideIPTV infrastructure.

Rules:
- Maps canonical Italian channel names (Sky Sport, DAZN, Eurosport, SuperTennis, Rai, Mediaset)
  to their tested Freeshot embed codes.
- Streams are dispatched through EasyProxy with host=freeshot and d={embed_code}.
- When displayed in Stremio/Nuvio, Freeshot sources are positioned immediately below TvVoo sources.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("streamsport.freeshot_service")

# Complete verified mapping of Italian channels on Freeshot (from freeshot.live/live-tv/italia)
# Key: canonical channel key (matching TvVoo canonical keys)
# Value: dict with embed code, display name, and category
FREESHOT_ITALIAN_CHANNELS: Dict[str, Dict[str, str]] = {
    # DAZN
    "dazn 1": {"code": "ZonaDAZN", "name": "Zona DAZN"},
    # Sky Sport
    "sky sport uno": {"code": "SkySportUnoItalia", "name": "Sky Sport Uno"},
    "sky sport calcio": {"code": "SkySportCalcioIT", "name": "Sky Sport Calcio"},
    "sky sport tennis": {"code": "SkySportTennisIT", "name": "Sky Sport Tennis"},
    "sky sport f1": {"code": "SkySportF1Italia", "name": "Sky Sport F1"},
    "sky sport motogp": {"code": "SkySportMotoGPIT", "name": "Sky Sport MotoGP"},
    "sky sport arena": {"code": "SkySportArenaIT", "name": "Sky Sport Arena"},
    "sky sport nba": {"code": "SkySportNBAIT", "name": "Sky Sport NBA"},
    "sky sport golf": {"code": "SkySportGolfIT", "name": "Sky Sport Golf"},
    "sky sport max": {"code": "SkySportMaxIT", "name": "Sky Sport Max"},
    "sky sport 24": {"code": "SkySport24IT", "name": "Sky Sport 24"},
    "sky sport legend": {"code": "SkySportLegendIT", "name": "Sky Sport Legend"},
    "sky sport mix": {"code": "SkySportMixIT", "name": "Sky Sport Mix"},
    # Eurosport
    "eurosport 1": {"code": "Eurosport1IT", "name": "Eurosport 1"},
    "eurosport 2": {"code": "Eurosport2IT", "name": "Eurosport 2"},
    # Tennis & Sport Nazionali
    "supertennis": {"code": "SuperTennisIT", "name": "SuperTennis"},
    "solocalcio": {"code": "SoloCalcio", "name": "SoloCalcio"},
    "sportitalia solocalcio": {"code": "SoloCalcio", "name": "SoloCalcio"},
    "sportitalia": {"code": "Sportitalia", "name": "Sportitalia"},
    "bike channel": {"code": "BikeIT", "name": "Bike Channel"},
    # Club Tematici Serie A
    "inter tv": {"code": "InterTV", "name": "Inter TV"},
    "milan tv": {"code": "MilanTV", "name": "Milan TV"},
    # Rai DTT
    "rai sport": {"code": "RaiSport", "name": "Rai Sport"},
    "rai 1": {"code": "Rai1IT", "name": "Rai 1"},
    "rai 2": {"code": "Rai2IT", "name": "Rai 2"},
    "rai 3": {"code": "Rai3IT", "name": "Rai 3"},
    "rai 4": {"code": "Rai4IT", "name": "Rai 4"},
    "rai news 24": {"code": "RaiNews24", "name": "Rai News 24"},
    # Mediaset & FTA Nazionali
    "canale 5": {"code": "Canale-5", "name": "Canale 5"},
    "italia 2": {"code": "ITALIA2TV", "name": "Italia 2"},
    "mediaset extra": {"code": "MediasetExtra", "name": "Mediaset Extra"},
    "la7": {"code": "La7", "name": "La7"},
    "la7d": {"code": "LA7d", "name": "La7d"},
}

# Lookup by Sky / Tivùsat official channel IDs
SKY_TO_FREESHOT_KEY: Dict[str, str] = {
    "9094": "sky sport 24",
    "9097": "sky sport uno",
    "9113": "sky sport calcio",
    "11237": "sky sport tennis",
    "9093": "sky sport arena",
    "9116": "sky sport nba",
    "9103": "sky sport max",
    "9096": "sky sport f1",
    "9102": "sky sport motogp",
    "10254": "sky sport golf",
    "11910": "sky sport legend",
    "11909": "sky sport mix",
    "6000": "supertennis",
    "11402": "dazn 1",
    "807": "rai sport",
    "9513": "milan tv",
    "9893": "inter tv",
    "899": "rai 1",
    "898": "rai 2",
    "897": "rai 3",
}

TIVUSAT_TO_FREESHOT_KEY: Dict[str, str] = {
    "42": "rai 1",
    "43": "rai 2",
    "44": "rai 3",
    "88": "rai sport",
    "145": "canale 5",
    "295": "solocalcio",
}


class FreeshotService:
    """Service providing Freeshot stream mapping and EasyProxy link generation."""

    def get_channel_info(
        self,
        canonical_key: Optional[str] = None,
        sky_id: Optional[str] = None,
        tivusat_id: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        """Resolves a channel to its Freeshot embed info."""
        target_key = None
        if sky_id and str(sky_id) in SKY_TO_FREESHOT_KEY:
            target_key = SKY_TO_FREESHOT_KEY[str(sky_id)]
        elif tivusat_id and str(tivusat_id) in TIVUSAT_TO_FREESHOT_KEY:
            target_key = TIVUSAT_TO_FREESHOT_KEY[str(tivusat_id)]
        elif canonical_key:
            target_key = canonical_key.lower().strip()

        if not target_key:
            return None

        # Direct match or alias
        if target_key in FREESHOT_ITALIAN_CHANNELS:
            return FREESHOT_ITALIAN_CHANNELS[target_key]

        # Substring/fuzzy match
        for k, info in FREESHOT_ITALIAN_CHANNELS.items():
            if k in target_key or target_key in k:
                return info

        return None

    def has_channel(
        self,
        canonical_key: Optional[str] = None,
        sky_id: Optional[str] = None,
        tivusat_id: Optional[str] = None,
    ) -> bool:
        """Returns True if the channel is available on Freeshot."""
        return self.get_channel_info(canonical_key=canonical_key, sky_id=sky_id, tivusat_id=tivusat_id) is not None

    def get_stream_item(self, channel_code: str, display_name: str) -> Dict[str, Any]:
        """Returns a source dict for match sources."""
        return {
            "source": "freeshot",
            "id": channel_code,
            "name": display_name,
            "code": channel_code,
            "country": "Italy",
        }


freeshot_service = FreeshotService()
