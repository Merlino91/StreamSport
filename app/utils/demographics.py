"""
Centralized demographics extraction module for StreamSport.
Extracts gender (is_women) and age tier (is_youth) from competition/league metadata
without inspecting athlete or team names, preventing false positives from initials (e.g. D. Medvedev, F. Cobolli).
"""
import re
from typing import Tuple

# Universal multilingual women's keywords and leagues
# Covers English (Flashscore Global/UK), Italian (Diretta.it), and major international sports
_WOMEN_KEYWORDS = re.compile(
    r"\b("
    r"women(?:['’]s)?|woman|ladies|lady|"
    r"femminile|femm|donne|"
    r"femenil|femenino|feminina|feminino|liga\s*f|"
    r"frauen|féminine|feminine|dames|damen|"
    r"wta|wnba|wsl|uwcl|nwsl|lpga|bjk\s*cup|billie\s*jean|fed\s*cup"
    r")\b",
    re.IGNORECASE,
)

# Universal multilingual youth / junior keywords and tiers
_YOUTH_KEYWORDS = re.compile(
    r"\b("
    r"u-?1[5-9]|u-?2[01]|under\s*1[5-9]|under\s*2[01]|"
    r"youth|primavera|junior|juniores|allievi|giovanili|giovanissimi|"
    r"cadete|juvenil|sub-?1[5-9]|sub-?2[01]|"
    r"college|ncaa"
    r")\b",
    re.IGNORECASE,
)


def extract_demographics(competition_text: str) -> Tuple[bool, bool]:
    """
    Extracts (is_women, is_youth) strictly from tournament / league / competition metadata.
    Defaults to (False, False) when no explicit female or youth keyword is declared.
    """
    c = (competition_text or "").strip()
    if not c or c.lower() in ("unknown", "other", "live sports", "sports"):
        return False, False

    is_women = bool(_WOMEN_KEYWORDS.search(c))
    is_youth = bool(_YOUTH_KEYWORDS.search(c))

    return is_women, is_youth
