"""
Provider-specific text and match dictionary normalizers for StreamSport.
Prepares raw provider candidates for scoring without altering original streaming sources.
"""
from __future__ import annotations
import re
import html
import unicodedata
from typing import Any, Dict, Optional, Tuple

from app.services.team_matching import clean_team_name, extract_team_name

# Emojis regex: flags (regional indicator symbols), miscellaneous symbols, transport, dingbats
_EMOJI_REGEX = re.compile(
    r"[\U0001F1E6-\U0001F1FF\U000E0020-\U000E007F\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\u2600-\u26FF\u2700-\u27BF]"
)

# Trailing note in parentheses, e.g. "(England - Championship)" or "(🥊 Boxing WBA)"
_PARENTHESIS_NOTE_REGEX = re.compile(r"\s*\(([^)]+)\)\s*$")

# Channel or quality suffix separated by pipe, e.g. "Match Title | Sky Sport 1"
_PIPE_SUFFIX_REGEX = re.compile(r"\s*\|.*$")

# Match separators
_VS_SPLIT_REGEX = re.compile(r"\s+(?:vs\.?|v\.?|–|—|-)\s+", re.IGNORECASE)


# Common non-ASCII European characters that don't decompose under NFKD
_CHAR_REPLACEMENTS = {
    "ø": "o", "Ø": "O",
    "æ": "ae", "Æ": "AE",
    "œ": "oe", "Œ": "OE",
    "ı": "i", "İ": "I",
    "ß": "ss",
    "ð": "d", "Ð": "D",
    "þ": "th", "Þ": "TH",
    "ł": "l", "Ł": "L",
    "đ": "d", "Đ": "D",
}

def normalize_ascii_text(text: str) -> str:
    """Strips accents and unprintable characters while preserving letters and digits."""
    if not text:
        return ""
    # Map special latin characters first
    for char, rep in _CHAR_REPLACEMENTS.items():
        text = text.replace(char, rep)
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("utf-8")
    return re.sub(r"\s+", " ", ascii_text).strip()


def clean_daddylive_event(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cleans DaddyLive raw match:
    - Strips flag and sport emojis
    - Extracts trailing notes in parentheses into league_note
    - Extracts clean home and away strings
    - Standardizes teams object into {"home": {"name": ...}, "away": {"name": ...}}
    """
    m = dict(raw)
    raw_title = html.unescape(m.get("title") or "")
    if "raw_title" not in m:
        m["raw_title"] = raw_title

    # 1. Strip pipe suffix if any
    t = _PIPE_SUFFIX_REGEX.sub("", raw_title).strip()

    # 2. Extract trailing parenthesis note (e.g. league or sport cue)
    note_match = _PARENTHESIS_NOTE_REGEX.search(t)
    league_note = ""
    if note_match:
        league_note = note_match.group(1).strip()
        t = _PARENTHESIS_NOTE_REGEX.sub("", t).strip()

    # 3. Strip emojis
    t = _EMOJI_REGEX.sub("", t).strip()
    t = normalize_ascii_text(t)

    # 4. Extract home and away
    home_str = ""
    away_str = ""
    teams = m.get("teams")
    if isinstance(teams, dict) and teams.get("home") and teams.get("away"):
        home_str = extract_team_name(teams.get("home"))
        away_str = extract_team_name(teams.get("away"))
        home_str = _EMOJI_REGEX.sub("", home_str).strip()
        away_str = _EMOJI_REGEX.sub("", away_str).strip()
        home_str = normalize_ascii_text(home_str)
        away_str = normalize_ascii_text(away_str)

    if not home_str or not away_str:
        if _VS_SPLIT_REGEX.search(t):
            parts = _VS_SPLIT_REGEX.split(t, maxsplit=1)
            home_str = parts[0].strip()
            away_str = parts[1].strip()

    m["home"] = home_str
    m["away"] = away_str
    m["clean_home"] = clean_team_name(home_str)
    m["clean_away"] = clean_team_name(away_str)
    m["clean_title"] = f"{home_str} vs {away_str}" if (home_str and away_str) else t
    if league_note:
        m["league_note"] = league_note

    # Detect demographic cues written in parenthesis note if not already set
    if "is_women" not in m and bool(re.search(r"\b(women|femminile|ladies|dames|damen|w)\b", league_note, re.I)):
        m["is_women"] = True
    if "is_youth" not in m and bool(re.search(r"\b(u19|u20|u21|u23|youth|primavera)\b", league_note, re.I)):
        m["is_youth"] = True

    m["teams"] = {
        "home": {"name": home_str},
        "away": {"name": away_str}
    }
    return m


def clean_spon_event(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cleans Sportzonline (SPON) match:
    - Fixes flat-string teams into nested {"home": {"name": ...}}
    - Strips sport prefix from title
    - Populates top-level home and away
    """
    m = dict(raw)
    raw_title = m.get("title") or ""
    if "raw_title" not in m:
        m["raw_title"] = raw_title

    # Remove category prefix (e.g. "Football: " or "Tennis: ")
    t = raw_title
    if ":" in t:
        p_prefix, p_rest = t.split(":", 1)
        if any(w in p_prefix.lower() for w in ("football", "calcio", "tennis", "basket", "hockey", "motogp", "f1")):
            t = p_rest.strip()

    t = normalize_ascii_text(t)

    teams = m.get("teams") or {}
    h_val = teams.get("home") if isinstance(teams, dict) else ""
    a_val = teams.get("away") if isinstance(teams, dict) else ""
    h = h_val.get("name", "") if isinstance(h_val, dict) else str(h_val or "")
    a = a_val.get("name", "") if isinstance(a_val, dict) else str(a_val or "")

    h = normalize_ascii_text(h)
    a = normalize_ascii_text(a)

    if (not h or not a) and _VS_SPLIT_REGEX.search(t):
        parts = _VS_SPLIT_REGEX.split(t, maxsplit=1)
        h = parts[0].strip()
        a = parts[1].strip()

    m["home"] = h
    m["away"] = a
    m["clean_home"] = clean_team_name(h)
    m["clean_away"] = clean_team_name(a)
    m["clean_title"] = f"{h} vs {a}" if (h and a) else t
    m["teams"] = {
        "home": {"name": h},
        "away": {"name": a}
    }
    return m


def clean_fctv33_event(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cleans FCTV33 match:
    - Normalizes teams and title
    - Sets top-level home and away
    """
    m = dict(raw)
    raw_title = m.get("title") or ""
    if "raw_title" not in m:
        m["raw_title"] = raw_title

    teams = m.get("teams") or {}
    h = extract_team_name(teams.get("home")) if isinstance(teams, dict) else ""
    a = extract_team_name(teams.get("away")) if isinstance(teams, dict) else ""

    h = normalize_ascii_text(h)
    a = normalize_ascii_text(a)

    if (not h or not a) and _VS_SPLIT_REGEX.search(raw_title):
        parts = _VS_SPLIT_REGEX.split(raw_title, maxsplit=1)
        h = normalize_ascii_text(parts[0].strip())
        a = normalize_ascii_text(parts[1].strip())

    m["home"] = h
    m["away"] = a
    m["clean_home"] = clean_team_name(h)
    m["clean_away"] = clean_team_name(a)
    m["clean_title"] = f"{h} vs {a}" if (h and a) else normalize_ascii_text(raw_title)
    m["teams"] = {
        "home": {"name": h},
        "away": {"name": a}
    }
    return m


def clean_mandrakodi_event(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cleans Mandrakodi event:
    - Strips [ITA] and other bracket prefixes
    - Cleans home and away
    """
    m = dict(raw)
    raw_title = m.get("title") or ""
    if "raw_title" not in m:
        m["raw_title"] = raw_title

    t = re.sub(r"\[[^\]]+\]", "", raw_title).strip()
    t = normalize_ascii_text(t)

    teams = m.get("teams") or {}
    h = extract_team_name(teams.get("home")) if isinstance(teams, dict) else ""
    a = extract_team_name(teams.get("away")) if isinstance(teams, dict) else ""

    h = normalize_ascii_text(h)
    a = normalize_ascii_text(a)

    if (not h or not a) and _VS_SPLIT_REGEX.search(t):
        parts = _VS_SPLIT_REGEX.split(t, maxsplit=1)
        h = parts[0].strip()
        a = parts[1].strip()

    m["home"] = h
    m["away"] = a
    m["clean_home"] = clean_team_name(h)
    m["clean_away"] = clean_team_name(a)
    m["clean_title"] = f"{h} vs {a}" if (h and a) else t
    m["teams"] = {
        "home": {"name": h},
        "away": {"name": a}
    }
    return m


def clean_streamed_event(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cleans Streamed API match:
    - Handles raw teams (whether dict or list)
    - Sets top-level home and away
    """
    m = dict(raw)
    raw_title = m.get("title") or ""
    if "raw_title" not in m:
        m["raw_title"] = raw_title

    t = normalize_ascii_text(raw_title)

    h, a = "", ""
    teams = m.get("teams")
    if isinstance(teams, dict):
        h = extract_team_name(teams.get("home"))
        a = extract_team_name(teams.get("away"))
    elif isinstance(teams, list) and len(teams) >= 2:
        h = extract_team_name(teams[0])
        a = extract_team_name(teams[1])

    h = normalize_ascii_text(h)
    a = normalize_ascii_text(a)

    if (not h or not a) and _VS_SPLIT_REGEX.search(t):
        parts = _VS_SPLIT_REGEX.split(t, maxsplit=1)
        h = parts[0].strip()
        a = parts[1].strip()

    m["home"] = h
    m["away"] = a
    m["clean_home"] = clean_team_name(h)
    m["clean_away"] = clean_team_name(a)
    m["clean_title"] = f"{h} vs {a}" if (h and a) else t
    m["teams"] = {
        "home": {"name": h},
        "away": {"name": a}
    }
    return m
