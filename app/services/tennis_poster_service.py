from __future__ import annotations
import asyncio
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple
import weakref
import httpx

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None
    ImageDraw = None
    ImageFont = None

from app.config import POSTERS_DIR, PROJECT_DIR
from app.services.db_service import db_service

logger = logging.getLogger("streamsport.tennis_poster")

ATHLETES_DIR = POSTERS_DIR / "athletes"
FLAGS_DIR = POSTERS_DIR / "flags"
ASSETS_DIR = POSTERS_DIR / "assets"
BACKGROUNDS_DIR = POSTERS_DIR / "backgrounds"

# Static directory (baked into Docker image, never masked by volume mounts)
STATIC_DIR = PROJECT_DIR / "app" / "static" / "posters"
STATIC_BACKGROUNDS_DIR = STATIC_DIR / "backgrounds"
STATIC_ASSETS_DIR = STATIC_DIR / "assets"

CANVAS_WIDTH = 1225
CANVAS_HEIGHT = 700
TENNIS_POSTER_VERSION = 4

ESPN_COUNTRIES_URL = "https://sports.core.api.espn.com/v2/sports/tennis/leagues/atp/countries?limit=300"
ESPN_FLAG_CDN_TMPL = "https://a.espncdn.com/i/teamlogos/countries/500/{code}.png"
ESPN_COUNTRIES_TTL = 86400  # 24h

# Normalized country aliases (lowercase, unaccented) -> ESPN 3-letter code
COUNTRY_ALIASES: Dict[str, str] = {
    "italia": "ita", "italy": "ita",
    "cina": "chn", "china": "chn",
    "stati uniti": "usa", "united states": "usa", "usa": "usa",
    "spagna": "esp", "spain": "esp",
    "francia": "fra", "france": "fra",
    "germania": "ger", "germany": "ger",
    "gran bretagna": "gbr", "great britain": "gbr", "regno unito": "gbr", "united kingdom": "gbr", "uk": "gbr",
    "inghilterra": "eng", "england": "eng",
    "australia": "aus",
    "canada": "can",
    "argentina": "arg",
    "giappone": "jpn", "japan": "jpn",
    "olanda": "ned", "paesi bassi": "ned", "netherlands": "ned", "holland": "ned",
    "belgio": "bel", "belgium": "bel",
    "svizzera": "sui", "switzerland": "sui",
    "serbia": "srb",
    "croazia": "cro", "croatia": "cro",
    "repubblica ceca": "cze", "cechia": "cze", "czech republic": "cze", "czechia": "cze",
    "polonia": "pol", "poland": "pol",
    "brasile": "bra", "brazil": "bra",
    "svezia": "swe", "sweden": "swe",
    "austria": "aut",
    "kazakistan": "kaz", "kazakhstan": "kaz",
    "ucraina": "ukr", "ukraine": "ukr",
    "slovacchia": "svk", "slovakia": "svk",
    "slovenia": "slo",
    "cile": "chi", "chile": "chi",
    "colombia": "col",
    "messico": "mex", "mexico": "mex",
    "corea del sud": "kor", "south korea": "kor", "korea": "kor", "korea republic": "kor", "corea": "kor",
    "corea del nord": "prk", "north korea": "prk",
    "norvegia": "nor", "norway": "nor",
    "danimarca": "den", "denmark": "den",
    "finlandia": "fin", "finland": "fin",
    "romania": "rou",
    "ungheria": "hun", "hungary": "hun",
    "grecia": "gre", "greece": "gre",
    "portogallo": "por", "portugal": "por",
    "india": "ind",
    "israele": "isr", "israel": "isr",
    "taiwan": "tpo", "taipei cinese": "tpo", "chinese taipei": "tpo",
    "hong kong": "hkg",
    "thailandia": "tha", "thailand": "tha",
    "indonesia": "ina",
    "paraguay": "par",
    "sudafrica": "rsa", "south africa": "rsa",
    "georgia": "geo",
    "lettonia": "lat", "latvia": "lat",
    "lituania": "ltu", "lithuania": "ltu",
    "estonia": "est",
    "filippine": "phi", "philippines": "phi",
    "emirati arabi uniti": "uae", "united arab emirates": "uae", "uae": "uae",
    "tagikistan": "tjk", "tajikistan": "tjk",
    "brunei": "bru", "brunei darussalam": "bru",
    "yemen": "yem",
    "bhutan": "bhu",
    "laos": "lao",
    "maldive": "mdv", "maldives": "mdv",
    "mongolia": "mgl",
    "nepal": "nep",
    "bangladesh": "ban",
    "bahrein": "bhr", "bahrain": "bhr",
    "macao": "mac", "macau": "mac",
    "guam": "gum",
    "cambogia": "cam", "cambodia": "cam",
    "papua nuova guinea": "png", "papua new guinea": "png",
    "barbados": "brb",
    "figi": "fij", "fiji": "fij",
    "nuova zelanda": "nzl", "new zealand": "nzl",
    "russia": "rus",
    "bielorussia": "blr", "belarus": "blr",
    "turchia": "tur", "turkey": "tur", "turkiye": "tur",
    "arabia saudita": "ksa", "saudi arabia": "ksa",
    "giordania": "jor", "jordan": "jor",
    "libano": "lib", "lebanon": "lib",
    "siria": "syr", "syria": "syr",
    "uzbekistan": "uzb",
    "turkmenistan": "tkm",
    "kirghizistan": "kgz", "kyrgyzstan": "kgz",
    "pakistan": "pak",
    "vietnam": "vie",
    "singapore": "sin",
    "malesia": "mas", "malaysia": "mas",
    "marocco": "mar", "morocco": "mar",
    "tunisia": "tun",
    "egitto": "egy", "egypt": "egy",
    "algeria": "alg",
    "uruguay": "uru",
    "peru": "per",
    "ecuador": "ecu",
    "bolivia": "bol",
    "venezuela": "ven",
    "costa d avorio": "civ", "cote d ivoire": "civ", "ivory coast": "civ",
    "lussemburgo": "lux", "luxembourg": "lux",
    "bosnia ed erzegovina": "bih", "bosnia and herzegovina": "bih", "bosnia herzegovina": "bih",
    "macedonia del nord": "mkd", "north macedonia": "mkd", "macedonia": "mkd",
    "porto rico": "pur", "puerto rico": "pur",
    "repubblica dominicana": "dom", "dominican republic": "dom",
    "moldavia": "mda", "moldova": "mda",
    "irlanda": "irl", "ireland": "irl",
    "cipro": "cyp", "cyprus": "cyp",
    "malta": "mlt",
    "monaco": "mon",
    "armenia": "arm",
    "azerbaigian": "aze", "azerbaijan": "aze",
    "qatar": "qat",
    "oman": "oma",
    "kuwait": "kuw",
    "iran": "irn",
    "iraq": "irq",
    "palestina": "ple", "palestine": "ple",
    "myanmar": "mya",
    "sri lanka": "sri",
    "bulgaria": "bul",
    "el salvador": "esa",
    "costa rica": "crc",
    "samoa": "wsm",
}

# Preferred clean Italian names for fallback cards (when an entity has no flag and feed used English)
COUNTRY_IT_DISPLAY: Dict[str, str] = {
    "ita": "Italia", "chn": "Cina", "usa": "Stati Uniti", "esp": "Spagna",
    "fra": "Francia", "ger": "Germania", "gbr": "Gran Bretagna", "eng": "Inghilterra",
    "jpn": "Giappone", "ned": "Paesi Bassi", "bel": "Belgio", "sui": "Svizzera",
    "cro": "Croazia", "cze": "Repubblica Ceca", "pol": "Polonia", "bra": "Brasile",
    "swe": "Svezia", "kaz": "Kazakistan", "ukr": "Ucraina", "svk": "Slovacchia",
    "chi": "Cile", "mex": "Messico", "kor": "Corea del Sud", "prk": "Corea del Nord",
    "nor": "Norvegia", "den": "Danimarca", "fin": "Finlandia", "hun": "Ungheria",
    "gre": "Grecia", "por": "Portogallo", "isr": "Israele", "tha": "Thailandia",
    "rsa": "Sudafrica", "lat": "Lettonia", "ltu": "Lituania", "phi": "Filippine",
    "uae": "Emirati Arabi Uniti", "tjk": "Tagikistan", "mdv": "Maldive",
    "bhr": "Bahrein", "mac": "Macao", "cam": "Cambogia", "png": "Papua Nuova Guinea",
    "fij": "Figi", "nzl": "Nuova Zelanda", "blr": "Bielorussia", "tur": "Turchia",
    "ksa": "Arabia Saudita", "jor": "Giordania", "lib": "Libano", "syr": "Siria",
    "kgz": "Kirghizistan", "mas": "Malesia", "mar": "Marocco", "egy": "Egitto",
    "civ": "Costa d'Avorio", "lux": "Lussemburgo", "bih": "Bosnia ed Erzegovina",
    "mkd": "Macedonia del Nord", "pur": "Porto Rico", "dom": "Repubblica Dominicana",
    "mda": "Moldavia", "irl": "Irlanda", "cyp": "Cipro", "aze": "Azerbaigian",
    "ple": "Palestina",
}

SPECIAL_TEAM_ENTITIES: Dict[str, Tuple[str, str]] = {
    "pacific oceania": ("PAC", "Pacific Oceania"),
    "team europe": ("EUR", "Team Europe"),
    "europa": ("EUR", "Team Europe"),
    "europe": ("EUR", "Team Europe"),
    "team world": ("WLD", "Team World"),
    "resto del mondo": ("WLD", "Team World"),
    "world": ("WLD", "Team World"),
}

KNOWN_COUNTRIES: Set[str] = set(COUNTRY_ALIASES.keys()) | set(SPECIAL_TEAM_ENTITIES.keys())

CIRCUIT_NAMES = {"atp", "wta", "itf", "challenger", "davis cup", "bjk cup", "united cup", "laver cup"}

STAGE_REGEX = re.compile(
    r"((?:couples?\s+)?1/[248]\s*final[ei]?(?:\s*\d+)?|quarter[- ]?finals?(?:\s*\d+)?|semi[- ]?finals?(?:\s*\d+)?|\bsemifinal[ei]\b|\bquarti(?:\s+di\s+finale)?\b|\bfinal\b|\bfinals\b|\bfinale\b|\bfinali\b|round of \d+|round \d+|r\d{1,2}\b|singles|doubles|qualification|qualifying|day \d+|giornata \d+|session \d+|sessione \d+|warmup|warm[- ]up|\batp\s*(?:&|and|\+)\s*wta\b|\bwta\s*(?:&|and|\+)\s*atp\b)",
    re.I
)

TOURNAMENT_STAGE_WORDS = {
    "atp", "wta", "itf", "challenger", "tour", "open", "finals", "final",
    "finale", "finali", "semi-final", "semifinale", "semifinali", "quarter-final",
    "quarti", "round", "day", "giornata", "session", "sessione", "singles",
    "doubles", "qualifying", "qualification", "warmup", "r16", "r32", "r64",
}


class TennisPosterService:
    """
    Generates dynamic broadcast-grade 16:9 landscape posters for tennis events.
    Features:
    - Official minimal brand backgrounds with tournament crests/logos
    - Strict sport-filtered athlete queries (rejects hockey players, goalkeepers, generic jerseys)
    - Rate-limiting (2.5s) for TheSportsDB API with in-memory session negative cache
    - Automatic detection and dedicated rendering for Country vs Country ties, Bracket Stages, and Doubles (4 players)
    - Automatic cleanup of concluded event cutouts & posters to keep storage lean
    """

    def __init__(self):
        self.posters_dir = POSTERS_DIR
        try:
            POSTERS_DIR.mkdir(parents=True, exist_ok=True)
            ATHLETES_DIR.mkdir(parents=True, exist_ok=True)
            FLAGS_DIR.mkdir(parents=True, exist_ok=True)
            self.flags_dir = FLAGS_DIR
        except Exception as e:
            logger.warning("Primary POSTERS_DIR %s unavailable (%s). Switching to temp fallback.", POSTERS_DIR, e)
            self.posters_dir = Path(tempfile.gettempdir()) / "streamsport_data" / "posters"
            self.posters_dir.mkdir(parents=True, exist_ok=True)
            (self.posters_dir / "athletes").mkdir(parents=True, exist_ok=True)
            self.flags_dir = self.posters_dir / "flags"
            self.flags_dir.mkdir(parents=True, exist_ok=True)

        self._font_path = self._find_font()
        self._enrichment_task: Optional[asyncio.Task] = None
        self._is_enriching: bool = False
        self._unknown_male_cache: Optional[Image.Image] = None
        self._unknown_female_cache: Optional[Image.Image] = None
        self._poster_locks: weakref.WeakValueDictionary = weakref.WeakValueDictionary()

        # ESPN Countries & Flags caches (24h index + persistent local flag PNGs)
        self._espn_countries_by_abbr: Dict[str, Dict[str, str]] = {}
        self._espn_countries_by_name: Dict[str, str] = {}
        self._espn_countries_loaded_at: float = 0.0
        self._countries_lock: Optional[asyncio.Lock] = None
        self._missing_flags: Set[str] = set()
        self._load_espn_countries_from_disk()

        # In-memory negative cache only (session-based). Clean any legacy .missing files from disk.
        self._missing_athletes: Set[str] = set()
        try:
            for p in ATHLETES_DIR.glob("*.missing"):
                try:
                    p.unlink()
                except Exception:
                    pass
        except Exception:
            pass

        # Purge legacy posters to free space and invalidate stale client caches
        try:
            for old_file in self.posters_dir.glob("tennis_*.jpg"):
                if not old_file.name.endswith(f"_v{TENNIS_POSTER_VERSION}.jpg"):
                    try:
                        old_file.unlink()
                    except Exception:
                        pass
        except Exception:
            pass

    def _find_font(self) -> Optional[str]:
        """Locates a clean, bold sans-serif font across Windows and Linux."""
        candidates = [
            # Windows
            os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "segoeuib.ttf"),
            os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arialbd.ttf"),
            os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf"),
            # Linux / Docker
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return None

    def _get_font(self, size: int) -> ImageFont.ImageFont:
        if self._font_path:
            try:
                return ImageFont.truetype(self._font_path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    @staticmethod
    def clean_player_name(raw: str) -> str:
        """Strips flag emojis, seeds, and bracketed notes."""
        s = raw or ""
        s = re.sub(r"[\U0001F1E6-\U0001F1FF]", "", s)
        s = re.sub(r"\[[^\]]*\]", "", s)
        s = re.sub(r"\([^)]*\)", "", s)
        s = re.sub(r"^\d+\s+", "", s)  # e.g. '1 Sinner' -> 'Sinner'
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    @staticmethod
    def parse_team_players(team_str: str) -> List[str]:
        """Splits doubles partners (e.g. 'Simone Bolelli / Andrea Vavassori' or 'Bolelli & Vavassori')."""
        if not team_str:
            return []
        cleaned = re.sub(r"\([^)]*\)", "", team_str).strip()
        if "/" in cleaned:
            parts = cleaned.split("/")
        elif " & " in cleaned:
            parts = cleaned.split(" & ")
        else:
            parts = [cleaned]
        res = [TennisPosterService.clean_player_name(p) for p in parts if p.strip()]
        # Discard circuit/tour tokens (ATP, WTA, ITF, Challenger) that are never individual athletes
        valid_players = [p for p in res if p and p.lower() not in CIRCUIT_NAMES]
        return valid_players[:2]

    def parse_players(self, title: str) -> Tuple[str, str]:
        """Extracts clean player 1 and player 2 names from event title."""
        t = title or ""
        t = re.sub(r"\([^)]*\)", "", t)
        t = re.sub(r"^[^:]+:\s*", "", t)

        if " vs " in t.lower():
            parts = re.split(r"\s+vs\s+", t, flags=re.I)
            p1 = self.clean_player_name(parts[0])
            p2 = self.clean_player_name(parts[1])
            return p1, p2
        elif " - " in t:
            parts = t.split(" - ", 1)
            p0_lower = parts[0].strip().lower()
            p1_lower = parts[1].strip().lower()
            has_stage = any(
                re.search(rf"\b{re.escape(w)}\b", p0_lower) or re.search(rf"\b{re.escape(w)}\b", p1_lower)
                for w in TOURNAMENT_STAGE_WORDS
            )
            if has_stage:
                return self.clean_player_name(t), ""
            p1 = self.clean_player_name(parts[0])
            p2 = self.clean_player_name(parts[1])
            return p1, p2

        return self.clean_player_name(t), ""

    @classmethod
    def _extract_single_player_surname(cls, player_name: str) -> str:
        """
        Extracts the full surname from various tennis naming formats:
        - 'Bolelli S.' or 'Bolelli S' -> 'BOLELLI'
        - 'S. Bolelli' -> 'BOLELLI'
        - 'Simone Bolelli' -> 'BOLELLI'
        - 'Bolelli' -> 'BOLELLI'
        - 'De Minaur A.' -> 'DE MINAUR'
        - 'Alex De Minaur' -> 'DE MINAUR'
        - 'Van De Zandschulp B.' -> 'VAN DE ZANDSCHULP'
        - 'Etcheverry T. M.' or 'Etcheverry T.M.' -> 'ETCHEVERRY'
        """
        p = (player_name or "").strip()
        if not p:
            return ""
        parts = p.split()
        if len(parts) == 1:
            return parts[0].upper()

        # 1. Trailing initials: 'Bolelli S.', 'Etcheverry T. M.', 'Etcheverry T.M.'
        trailing_initials = 0
        for tok in reversed(parts):
            tok_clean = tok.replace(".", "")
            if len(tok_clean) <= 2 and (len(tok) <= 2 or "." in tok):
                trailing_initials += 1
            else:
                break
        if 0 < trailing_initials < len(parts):
            surname_parts = parts[:-trailing_initials]
            return " ".join(surname_parts).upper()

        # 2. Leading initials: 'S. Bolelli', 'T. M. Etcheverry', 'T.M. Etcheverry'
        leading_initials = 0
        for tok in parts:
            tok_clean = tok.replace(".", "")
            if len(tok_clean) <= 2 and (len(tok) <= 2 or "." in tok):
                leading_initials += 1
            else:
                break
        if 0 < leading_initials < len(parts):
            surname_parts = parts[leading_initials:]
            return " ".join(surname_parts).upper()

        # 3. Multi-word surname prefixes (De Minaur, Van De Zandschulp, Del Potro)
        if len(parts) > 2 and parts[-2].lower() in ("de", "van", "del", "di", "da", "von", "le", "la"):
            return f"{parts[-2]} {parts[-1]}".upper()
        return parts[-1].upper()

    @classmethod
    def format_short_display_name(cls, full_name: str) -> str:
        """
        Formats singles as 'J. SINNER' / 'J. PAOLINI'.
        Formats doubles (2 vs 2) with full surnames as 'BOLELLI / VAVASSORI', 'ERRANI / PAOLINI', 'FRANTZEN / HAASE'.
        """
        name = full_name.strip()
        if "/" in name or " & " in name:
            delims = "/" if "/" in name else " & "
            partners = [p.strip() for p in name.split(delims) if p.strip()]
            surnames = [cls._extract_single_player_surname(p) for p in partners if p]
            return " / ".join(surnames[:2])
        # If already formatted as 'Paolini J.' or 'Sinner J.'
        m = re.match(r"^(.+?)\s+([A-Za-z])[\.\-]?$", name)
        if m:
            last = m.group(1).upper()
            initial = m.group(2).upper()
            return f"{initial}. {last}"
        m2 = re.match(r"^([A-Za-z])[\.\-]?\s+(.+)$", name)
        if m2:
            initial = m2.group(1).upper()
            last = m2.group(2).upper()
            return f"{initial}. {last}"
        parts = [p for p in name.split() if p]
        if not parts:
            return name.upper()
        if len(parts) == 1:
            return parts[0].upper()
        initial = parts[0][0].upper()
        last = " ".join(parts[1:]).upper()
        return f"{initial}. {last}"

    @classmethod
    def _is_player_match(cls, query: str, candidate_name: str) -> bool:
        """
        Validates that an API candidate matches the searched player query:
        - Exact match or surname containment (with accent normalization).
        - Prevents false-positive surname collision (e.g. 'Test Fake Male' vs 'Alastair Male', 'Janice Tjen' vs 'Jonas Luetjen').
        - Supports abbreviations & initials ('Bolelli S.' vs 'Simone Bolelli', 'Herbert P.H.' vs 'Pierre-Hugues Herbert', 'Harrison C.' vs 'Christian Harrison').
        - Supports compound names & reversed tokens ('Garfia Carlos Alcaraz' vs 'Carlos Alcaraz', 'Alex De Minaur' vs 'De Minaur Alex').
        """
        import unicodedata
        def _unaccent(s: str) -> str:
            nfkd = unicodedata.normalize("NFKD", s or "")
            return "".join(ch for ch in nfkd if not unicodedata.combining(ch))

        q = _unaccent(re.sub(r"[\(\)\[\]]", "", query or "")).strip().lower()
        c = _unaccent(candidate_name or "").strip().lower()
        if not q or not c:
            return False
        if q == c:
            return True

        q_tokens = [t for t in re.split(r"[\s\.\-]+", q) if t]
        c_tokens = [t for t in re.split(r"[\s\.\-]+", c) if t]
        if not q_tokens or not c_tokens:
            return False

        q_initials = [t[0] for t in q_tokens if len(t) == 1]
        q_words = [t for t in q_tokens if len(t) > 1]

        c_words = [t for t in c_tokens if len(t) > 1]

        if not q_words or not c_words:
            return False

        particles = {"de", "la", "del", "da", "di", "van", "von", "der"}
        q_sig_words = [w for w in q_words if w not in particles]
        c_sig_words = [w for w in c_words if w not in particles]

        # 1. Exact set match (e.g. 'Alex De Minaur' vs 'De Minaur Alex')
        if set(q_sig_words) == set(c_sig_words):
            return True

        # 2. Subset containment for compound names (e.g. 'Carlos Alcaraz' in 'Garfia Carlos Alcaraz')
        if len(c_sig_words) >= 2 and set(c_sig_words).issubset(set(q_sig_words)):
            return True
        if len(q_sig_words) >= 2 and set(q_sig_words).issubset(set(c_sig_words)):
            return True

        # 3. Surname check:
        # Trailing initials e.g. 'Herbert P.H.' -> surname is first word
        # Standard order e.g. 'Pierre-Hugues Herbert' -> surname is last word
        q_sur = q_words[0] if (q_tokens and len(q_tokens[-1]) == 1) else q_words[-1]
        c_sur = c_words[0] if (c_tokens and len(c_tokens[-1]) == 1) else c_words[-1]

        sur_ok = (q_sur in c_words) or (c_sur in q_words)
        if not sur_ok:
            return False

        # 4. Initials check:
        if q_initials:
            c_given_initials = [t[0] for t in c_tokens if t != c_sur]
            if q_initials[0] not in c_given_initials:
                return False
        elif len(q_words) > 1:
            q_given = [t for t in q_words if t != q_sur]
            c_given = [t for t in c_words if t != c_sur]
            if q_given and c_given:
                q_given_inits = {t[0] for t in q_given}
                c_given_inits = {t[0] for t in c_given}
                if not q_given_inits.intersection(c_given_inits):
                    return False

        return True

    def extract_tournament_and_stage(self, title: str, genre: str) -> Tuple[str, str]:
        """Extracts tournament name (e.g. 'WTA 500 SINGAPORE') and stage (e.g. 'QUARTER FINALS')."""
        t = title or ""
        tourn = ""
        stage = ""

        # Extract tournament from parentheses
        brackets = re.findall(r"\(([^)]+)\)", t)
        for b in brackets:
            b_clean = b.strip()
            if any(w in b_clean.lower() for w in ["wta", "atp", "cup", "open", "masters", "slam", "championship"]):
                # Clean up singles/doubles note
                if b_clean.lower() in ("atp - singles", "wta - singles", "atp - doubles", "wta - doubles"):
                    tourn = b_clean.split("-")[0].strip().upper() + " TOUR"
                else:
                    tourn = b_clean.upper()
                break

        if not tourn:
            if genre and genre.upper() not in ["ALL", "ALTRO"]:
                tourn = genre.upper()
            else:
                tourn = "ATP TOUR"

        # Extract stage pattern
        m = STAGE_REGEX.search(t)
        if m:
            raw_stage = m.group(1).upper()
            raw_stage = re.sub(r"1/4\s*FINAL[EI]?", "QUARTER FINAL", raw_stage)
            raw_stage = re.sub(r"1/2\s*FINAL[EI]?", "SEMI FINAL", raw_stage)
            raw_stage = re.sub(r"\bSEMIFINAL[EI]\b", "SEMI FINAL", raw_stage)
            raw_stage = re.sub(r"\bQUARTI(?:\s+DI\s+FINALE)?\b", "QUARTER FINAL", raw_stage)
            raw_stage = re.sub(r"\bFINAL[EI]\b", "FINAL", raw_stage)
            raw_stage = re.sub(r"\bGIORNATA\b", "DAY", raw_stage)
            raw_stage = re.sub(r"\bSESSIONE\b", "SESSION", raw_stage)
            raw_stage = re.sub(r"COUPLES?\s*", "DOUBLES ", raw_stage).strip()
            if any(k in raw_stage.lower() for k in ("atp & wta", "atp and wta", "wta & atp", "wta and atp")):
                raw_stage = "LIVE BROADCAST"
                if tourn == "ATP TOUR":
                    tourn = "ATP & WTA TOURS"
            stage = raw_stage

        return tourn, stage

    def is_stage_placeholder(self, title: str) -> Optional[Dict[str, str]]:
        """
        Returns stage/session info if title is a tournament bracket stage announcement
        or a multi-court / tournament session coverage feed without player vs player.
        """
        t = (title or "").strip()
        t_low = t.lower()
        has_vs = bool(re.search(r"\s+(?:vs\.?|v\.?)\s+", t_low))
        if has_vs:
            return None

        # 1. Bracket stage announcement (e.g. 'Quarter Finals', 'Semifinals', 'Final', 'Day 1')
        m = STAGE_REGEX.search(title or "")
        if m:
            tourn, stage = self.extract_tournament_and_stage(title, "")
            return {"tournament": tourn, "stage": stage or m.group(1).upper()}

        # 2. Multi-tournament, circuit feeds or broadcast sessions
        has_circuit = bool(re.search(r"\b(atp|wta|challenger|itf|masters|slam|open|davis|bjk)\b", t_low))
        has_multicourt_signal = (
            any(k in t_low for k in ("&", "+", "early rounds", "session", "sessione", "diretta", "multicourt", "multi-court", "broadcast"))
            or bool(re.match(r"^(?:atp|wta|itf|challenger)\s*(?:\d{3,4})?\s+[\w\s]+$", t, re.I))
            or t_low in ("atp & wta", "atp and wta", "wta & atp", "wta and atp", "atp + wta", "wta + atp", "atp", "wta")
        )

        if has_circuit and has_multicourt_signal:
            has_atp = "atp" in t_low
            has_wta = "wta" in t_low
            if has_atp and has_wta:
                tourn = "ATP & WTA TOURS"
                stage = "COPERTURA MULTI-CAMPO"
            elif has_wta:
                tourn = re.sub(r"\s*\(tennis\)$", "", t, flags=re.I).strip().upper()
                if "&" in tourn:
                    tourn = "WTA TOUR"
                stage = "SESSIONE DIRETTA"
            elif has_atp:
                tourn = re.sub(r"\s*\(tennis\)$", "", t, flags=re.I).strip().upper()
                if "&" in tourn:
                    tourn = "ATP TOUR"
                stage = "SESSIONE DIRETTA"
            else:
                tourn = "CIRCUITO TENNIS"
                stage = "LIVE BROADCAST"

            if "early rounds" in t_low:
                stage = "EARLY ROUNDS"

            return {"tournament": tourn, "stage": stage}

        return None

    @staticmethod
    def _norm_country_key(text: str) -> str:
        """Normalizes a country or team name into a plain lowercase ASCII lookup key."""
        s = re.sub(r"[\(\)\[\]]", " ", text or "")
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
        s = re.sub(r"[^a-zA-Z0-9]+", " ", s).strip().lower()
        return s

    def _seed_espn_countries(self, items: List[Dict[str, Any]], loaded_at: Optional[float] = None):
        """Populates in-memory country abbreviation and name lookup tables from ESPN items."""
        by_abbr: Dict[str, Dict[str, str]] = {}
        by_name: Dict[str, str] = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            abbr = (it.get("abbreviation") or "").strip().lower()
            name = (it.get("name") or "").strip()
            slug = (it.get("slug") or "").strip().replace("-", " ")
            flag = (it.get("flag") or {}).get("href") or ""
            if not abbr:
                continue
            entry = {
                "abbr": abbr.upper(),
                "name": name or abbr.upper(),
                "flag_url": flag or ESPN_FLAG_CDN_TMPL.format(code=abbr),
            }
            if abbr not in by_abbr or (flag and not by_abbr[abbr].get("flag_url")):
                by_abbr[abbr] = entry
            if name:
                k = self._norm_country_key(name)
                if k:
                    by_name[k] = abbr
            if slug:
                k_slug = self._norm_country_key(slug)
                if k_slug:
                    by_name[k_slug] = abbr
        if by_abbr:
            self._espn_countries_by_abbr = by_abbr
            self._espn_countries_by_name = by_name
            self._espn_countries_loaded_at = loaded_at if loaded_at is not None else time.time()

    def _load_espn_countries_from_disk(self):
        """Loads persisted ESPN country index if available in flags_dir."""
        cache_file = self.flags_dir / "_espn_countries.json"
        if not cache_file.exists():
            return
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            items = payload.get("items") or []
            ts = float(payload.get("fetched_at") or 0.0)
            if items:
                self._seed_espn_countries(items, loaded_at=ts)
        except Exception as e:
            logger.debug("Failed reading ESPN countries disk cache %s: %s", cache_file, e)

    async def _ensure_espn_country_index(self):
        """Refreshes the 200+ ESPN tennis country index once per 24h (memory + disk cached)."""
        now = time.time()
        if self._espn_countries_by_abbr and (now - self._espn_countries_loaded_at < ESPN_COUNTRIES_TTL):
            return
        if self._countries_lock is None:
            self._countries_lock = asyncio.Lock()
        async with self._countries_lock:
            now = time.time()
            if self._espn_countries_by_abbr and (now - self._espn_countries_loaded_at < ESPN_COUNTRIES_TTL):
                return
            try:
                async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "curl/8.4.0"}) as client:
                    res = await client.get(ESPN_COUNTRIES_URL)
                if res.status_code == 200:
                    data = res.json()
                    items = data.get("items") or []
                    if items:
                        self._seed_espn_countries(items, loaded_at=now)
                        try:
                            self.flags_dir.mkdir(parents=True, exist_ok=True)
                            (self.flags_dir / "_espn_countries.json").write_text(
                                json.dumps({"fetched_at": now, "items": items}),
                                encoding="utf-8",
                            )
                        except Exception as e:
                            logger.debug("Could not persist ESPN countries cache: %s", e)
                        return
            except Exception as e:
                logger.debug("ESPN countries index fetch failed (using offline aliases): %s", e)
            # Mark attempted even on transient offline failure so every poster doesn't retry immediately
            if not self._espn_countries_loaded_at:
                self._espn_countries_loaded_at = now - ESPN_COUNTRIES_TTL + 300

    def _build_country_result(self, abbr: str, raw_display: str) -> Dict[str, Any]:
        """Builds a normalized country resolution record from a resolved ESPN 3-letter code."""
        code_low = abbr.lower()
        espn_entry = self._espn_countries_by_abbr.get(code_low, {})
        flag_url = espn_entry.get("flag_url") or ESPN_FLAG_CDN_TMPL.format(code=code_low)
        it_pref = COUNTRY_IT_DISPLAY.get(code_low)
        # Prefer Italian name if input was English and an Italian form exists; keep clean IT input as-is
        raw_clean = self.clean_player_name(raw_display).strip()
        raw_key = self._norm_country_key(raw_clean)
        display = raw_clean
        if it_pref and raw_key == self._norm_country_key(espn_entry.get("name", "")):
            display = it_pref
        elif not display:
            display = it_pref or espn_entry.get("name") or abbr.upper()
        return {
            "abbr": abbr.upper(),
            "display": display,
            "flag_url": flag_url,
            "is_team": True,
            "is_special": False,
        }

    def resolve_country(self, raw_name: str, alt_name: str = "") -> Dict[str, Any]:
        """
        Resolves a competitor string (IT, EN, or doubled EN+IT legacy strings) into
        an ESPN flag code and clean Italianized fallback label.
        """
        cleaned_raw = self.clean_player_name(raw_name).strip()
        cleaned_alt = self.clean_player_name(alt_name).strip()

        for orig in (cleaned_raw, cleaned_alt):
            k = self._norm_country_key(orig)
            if not k:
                continue
            if k in SPECIAL_TEAM_ENTITIES:
                code, label = SPECIAL_TEAM_ENTITIES[k]
                return {"abbr": code, "display": label, "flag_url": None, "is_team": True, "is_special": True}

            abbr = COUNTRY_ALIASES.get(k) or self._espn_countries_by_name.get(k)
            if not abbr and k in self._espn_countries_by_abbr:
                abbr = k
            if abbr:
                # For Italian display preference when alt_name matched first, keep cleaned_raw if non-empty
                disp_seed = cleaned_raw or orig
                return self._build_country_result(abbr, disp_seed)

        # Recover legacy corrupted strings like "United Arab Emirates Emirati arabi uniti" or "Tajikistan Tagikistan"
        k_raw = self._norm_country_key(cleaned_raw)
        if k_raw:
            known_lookup: List[Tuple[str, str]] = list(COUNTRY_ALIASES.items()) + list(self._espn_countries_by_name.items())
            known_lookup.sort(key=lambda kv: -len(kv[0]))
            for alias_k, abbr in known_lookup:
                if len(alias_k) < 4:
                    continue
                if k_raw.startswith(alias_k + " "):
                    rest = k_raw[len(alias_k):].strip()
                    rest_abbr = COUNTRY_ALIASES.get(rest) or self._espn_countries_by_name.get(rest)
                    if rest_abbr == abbr or rest == alias_k:
                        # Rest is the localized Italian duplicate -> prefer IT display
                        disp = COUNTRY_IT_DISPLAY.get(abbr.lower()) or rest.title()
                        return self._build_country_result(abbr, disp)
                elif k_raw.endswith(" " + alias_k):
                    prefix = k_raw[: -len(alias_k)].strip()
                    prefix_abbr = COUNTRY_ALIASES.get(prefix) or self._espn_countries_by_name.get(prefix)
                    if prefix_abbr == abbr or prefix == alias_k:
                        disp = COUNTRY_IT_DISPLAY.get(abbr.lower()) or alias_k.title()
                        return self._build_country_result(abbr, disp)

        fallback_disp = cleaned_raw or cleaned_alt or "TBD"
        abbr_guess = re.sub(r"[^A-Za-z]", "", fallback_disp).upper()[:3] or "TBD"
        return {"abbr": abbr_guess, "display": fallback_disp, "flag_url": None, "is_team": False, "is_special": False}

    def is_country_match(
        self,
        title: str,
        p1: str,
        p2: str,
        genre: str,
        match: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Returns True ONLY when both competitors resolve as nations or national-team entities.
        Prevents individual singles/doubles rubbers inside Davis/BJK/United Cup (e.g. Sinner vs Alcaraz)
        from being misclassified as nation-flag cards, while ensuring true country ties never search athletes.
        """
        if not p1 or not p2:
            return False
        alt_h = (match.get("alt_home") or "") if match else ""
        alt_a = (match.get("alt_away") or "") if match else ""
        c1 = self.resolve_country(p1, alt_name=alt_h)
        c2 = self.resolve_country(p2, alt_name=alt_a)
        return bool(c1.get("is_team") and c2.get("is_team"))

    @staticmethod
    def _crop_flag_image(img: Image.Image) -> Image.Image:
        """Trims transparent padding around ESPN flag PNGs and removes 3px outer anti-aliased edges."""
        rgba = img.convert("RGBA")
        bbox = rgba.getbbox()
        if bbox:
            left, top, right, bottom = bbox
            if (right - left) > 24 and (bottom - top) > 24:
                rgba = rgba.crop((left + 3, top + 3, right - 3, bottom - 3))
            else:
                rgba = rgba.crop(bbox)
        return rgba

    async def _fetch_country_flag(self, info: Dict[str, Any]) -> Optional[Image.Image]:
        """Fetches, trims, and caches an ESPN country flag PNG cleanly on disk."""
        if not info.get("is_team") or info.get("is_special"):
            return None
        code = (info.get("abbr") or "").strip().lower()
        flag_url = info.get("flag_url")
        if not code or not flag_url or code in self._missing_flags:
            return None

        local_path = self.flags_dir / f"{code}.png"
        if local_path.exists():
            try:
                with Image.open(local_path) as loaded:
                    return loaded.convert("RGBA")
            except Exception as e:
                logger.debug("Corrupted local flag cache %s (%s), re-fetching", local_path, e)
                try:
                    local_path.unlink(missing_ok=True)
                except Exception:
                    pass

        content_bytes: Optional[bytes] = None
        try:
            async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "curl/8.4.0"}) as client:
                res = await client.get(flag_url)
            if res.status_code == 200 and len(res.content) > 200:
                content_bytes = res.content
        except Exception as e:
            logger.debug("Direct flag download failed for %s: %s", flag_url, e)

        if content_bytes is None:
            try:
                from app.services.doh_client import doh_client
                proxied, _ = await doh_client.proxy_image(flag_url)
                if proxied and len(proxied) > 200:
                    content_bytes = proxied
            except Exception as e:
                logger.debug("DoH flag proxy failed for %s: %s", flag_url, e)

        if content_bytes:
            try:
                with io.BytesIO(content_bytes) as bio:
                    with Image.open(bio) as opened:
                        if opened.width < 40 or opened.height < 40:
                            return None
                        cropped = self._crop_flag_image(opened)
                try:
                    self.flags_dir.mkdir(parents=True, exist_ok=True)
                    tmp_path = local_path.with_suffix(".tmp.png")
                    cropped.save(tmp_path, "PNG")
                    tmp_path.replace(local_path)
                except Exception as e:
                    logger.debug("Could not write flag cache %s: %s", local_path, e)
                return cropped
            except Exception as e:
                logger.debug("Failed decoding flag %s: %s", flag_url, e)

        self._missing_flags.add(code)
        return None

    async def _fetch_match_flags(
        self,
        p1_name: str,
        p2_name: str,
        match: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Resolves both country records and fetches/caches their ESPN flag PNGs concurrently."""
        alt_h = (match.get("alt_home") or "") if match else ""
        alt_a = (match.get("alt_away") or "") if match else ""
        info1 = self.resolve_country(p1_name, alt_name=alt_h)
        info2 = self.resolve_country(p2_name, alt_name=alt_a)
        f1, f2 = await asyncio.gather(
            self._fetch_country_flag(info1),
            self._fetch_country_flag(info2),
        )
        return {"c1": f1, "c2": f2, "info1": info1, "info2": info2}

    @staticmethod
    def is_female_competition(title: str, genre: str = "", match: Optional[Dict[str, Any]] = None) -> bool:
        """Determines if a match belongs to a women's tournament or circuit."""
        if match and match.get("is_women"):
            return True
        comp = (match.get("competition") or "") if match else ""
        canon = (match.get("canonical_competition") or "") if match else ""
        combined = f"{genre} {title} {comp} {canon}".lower()
        if any(w in combined for w in ("wta", "bjk", "billie jean", "femminile", "women", "donne", "ladies")):
            return True
        from app.utils.demographics import extract_demographics
        is_w, _ = extract_demographics(combined)
        return is_w

    def _get_unknown_cutout(self, is_female: bool = False) -> Optional[Image.Image]:
        """Loads the official locked/unknown athlete bust silhouette asset (male or female)."""
        if is_female:
            if self._unknown_female_cache is not None:
                return self._unknown_female_cache
            for p in (
                STATIC_ASSETS_DIR / "Unknow_female.png",
                ASSETS_DIR / "Unknow_female.png",
            ):
                if p.exists():
                    try:
                        with Image.open(p) as loaded:
                            self._unknown_female_cache = loaded.convert("RGBA")
                        return self._unknown_female_cache
                    except Exception as e:
                        logger.warning("Failed loading unknown female cutout from %s: %s", p, e)
        else:
            if self._unknown_male_cache is not None:
                return self._unknown_male_cache
            for p in (
                STATIC_ASSETS_DIR / "Unknow_male.png",
                ASSETS_DIR / "Unknow_male.png",
            ):
                if p.exists():
                    try:
                        with Image.open(p) as loaded:
                            self._unknown_male_cache = loaded.convert("RGBA")
                        return self._unknown_male_cache
                    except Exception as e:
                        logger.warning("Failed loading unknown male cutout from %s: %s", p, e)

        # Fallback if specific gender asset is missing
        for p in (
            STATIC_ASSETS_DIR / "Unknown.png",
            ASSETS_DIR / "Unknown.png",
        ):
            if p.exists():
                try:
                    with Image.open(p) as loaded:
                        return loaded.convert("RGBA")
                except Exception:
                    pass
        return None

    async def _fetch_and_validate_image(self, url: str) -> Optional[Image.Image]:
        """
        Actively validates that a cached image URL is live (HTTP 200, Content-Type image/*)
        and loads it into a PIL RGBA Image in volatile RAM without saving to disk.
        Returns None if expired, 404/403, broken, or not an image (triggering auto-invalidation).
        """
        if not url or not url.startswith("http"):
            return None

        def _fetch_sync():
            try:
                from curl_cffi import requests as curl_requests
                if "atptour.com" in url:
                    headers = {
                        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": "https://www.atptour.com/en/players",
                    }
                    imp = "safari15_5"
                else:
                    headers = {
                        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
                    }
                    imp = "chrome120"
                with curl_requests.Session(impersonate=imp) as s:
                    s.headers.update(headers)
                    res = s.get(url, timeout=8.0)
                    if res.status_code == 200:
                        # Reject ATP default empty grey silhouette (exactly 4729 bytes)
                        if len(res.content) == 4729:
                            logger.info("Active validation: URL rifiutato (silhouette grigia ATP da 4729 byte): %s", url)
                            return None
                        ct = (res.headers.get("content-type") or "").lower()
                        if "image" in ct or res.content.startswith(b"\x89PNG") or res.content.startswith(b"\xff\xd8"):
                            return res.content
                    else:
                        logger.info("Active validation HTTP %d per URL: %s", res.status_code, url)
            except Exception as e:
                logger.info("Active validation fetch error per %s: %s", url, e)
            return None

        content = await asyncio.to_thread(_fetch_sync)
        if content:
            try:
                with io.BytesIO(content) as bio:
                    with Image.open(bio) as loaded:
                        img = loaded.convert("RGBA")
                if img.width >= 50 and img.height >= 50:
                    return img
            except Exception as e:
                logger.debug("Failed opening validated image from %s: %s", url, e)
        return None

    async def _search_espn(self, search_query: str) -> Optional[Tuple[Image.Image, str]]:
        """Level 1: Searches ESPN Search API for tennis athlete cutout."""
        def _get_query_candidates(name: str) -> List[str]:
            clean_name = self.clean_player_name(name)
            cands = [clean_name]
            surname = self._extract_single_player_surname(clean_name)
            if surname and len(surname) >= 3 and surname.lower() != clean_name.lower():
                cands.append(surname.capitalize())
            parts = clean_name.split()
            if len(parts) >= 3:
                cands.append(" ".join(parts[1:]))
                cands.append(" ".join(parts[:2]))
                cands.append(f"{parts[0]} {parts[-1]}")
            cleaned_parts = [p for p in parts if not p.isdigit() and len(p) > 1 and not p.endswith(".")]
            if 1 < len(cleaned_parts) < len(parts):
                cands.append(" ".join(cleaned_parts))
            return list(dict.fromkeys(cands))

        candidates = _get_query_candidates(search_query)
        headers = {"User-Agent": "curl/8.4.0", "Accept": "*/*"}
        async with httpx.AsyncClient(timeout=6.0, headers=headers) as client:
            for q in candidates:
                try:
                    espn_url = f"https://site.web.api.espn.com/apis/search/v2?query={urllib.parse.quote(q)}"
                    res = await client.get(espn_url)
                    if res.status_code == 200:
                        data = res.json()
                        for r in data.get("results", []):
                            if r.get("type") == "player":
                                for item in r.get("contents", []):
                                    sport = str(item.get("sport") or "").lower()
                                    desc = str(item.get("description") or "").lower()
                                    img_url = (item.get("image") or {}).get("default") or ""
                                    is_tennis = (sport == "tennis") or ("tennis" in desc) or ("/tennis/" in img_url.lower())
                                    if not is_tennis:
                                        continue
                                    if any(ph in img_url.lower() for ph in ["default-player-logo", "silhouette", "placeholder"]):
                                        continue
                                    if img_url.startswith("http"):
                                        img_res = await client.get(img_url)
                                        if img_res.status_code == 200:
                                            with io.BytesIO(img_res.content) as bio:
                                                with Image.open(bio) as loaded:
                                                    img = loaded.convert("RGBA")
                                            return img, img_url
                except Exception as e:
                    logger.debug("ESPN athlete search error for '%s' (candidate '%s'): %s", search_query, q, e)
        return None

    async def _search_atp(self, search_query: str) -> Optional[Tuple[Image.Image, str]]:
        """Level 2 (Maschile): Searches ATP Tour official directory via curl_cffi for gladiator/headshot cutout."""
        def _search_sync():
            try:
                import time
                import unicodedata
                from curl_cffi import requests as curl_requests

                clean_full = self.clean_player_name(search_query)
                surname = self._extract_single_player_surname(clean_full)

                def _unaccent(s: str) -> str:
                    nfkd = unicodedata.normalize("NFKD", s or "")
                    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))

                cleaned_name = re.sub(r"[^\w\s\-]", "", clean_full).strip()
                cleaned_name = _unaccent(cleaned_name)
                norm_surname = _unaccent(surname) if surname else ""

                is_initial_name = bool(re.search(r"\b[A-Za-z]$", cleaned_name))

                # User requirement:
                # 1. Full name first (Nome e Cognome)
                # 2. Surname second (Solo il Cognome as fallback)
                search_terms: List[str] = []
                if not is_initial_name and len(cleaned_name.split()) >= 2:
                    search_terms.append(cleaned_name)
                if norm_surname and len(norm_surname) >= 3 and norm_surname.upper() != cleaned_name.upper():
                    search_terms.append(norm_surname)

                headers = {
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.atptour.com/en/players",
                    "X-Requested-With": "XMLHttpRequest",
                }

                q_toks = [t for t in re.split(r"[\s\.\-]+", clean_full.lower()) if t]
                q_inits = [t[0] for t in q_toks if len(t) == 1]

                def _score_item(it: dict) -> int:
                    fn = (it.get("FirstName") or "").strip()
                    ln = (it.get("LastName") or "").strip()
                    full = f"{fn} {ln}".lower()
                    active = it.get("Active") == "A"
                    score = 0
                    if active:
                        score += 50
                    if full == clean_full.lower():
                        score += 100
                    elif norm_surname and _unaccent(ln).lower() == norm_surname.lower():
                        score += 60
                    if q_inits and fn:
                        if fn[0].lower() in q_inits:
                            score += 30
                    return score

                with curl_requests.Session(impersonate="safari15_5") as session:
                    session.headers.update(headers)
                    found_player = None

                    for i, term in enumerate(search_terms):
                        if i > 0:
                            time.sleep(0.25)
                        encoded = urllib.parse.quote(term.strip().lower())
                        api_url = f"https://www.atptour.com/en/-/www/players/find/byname/{encoded}/en"
                        try:
                            res = session.get(api_url, timeout=8.0)
                            if res.status_code != 200:
                                logger.info("ATP API HTTP %d per termine '%s' (url: %s)", res.status_code, term, api_url)
                                continue
                            if not res.content or not res.content.startswith(b"["):
                                logger.info("ATP API risposta non-JSON per '%s': %d byte", term, len(res.content) if res.content else 0)
                                continue
                            data = res.json()
                            if not isinstance(data, list) or not data:
                                continue

                            sorted_items = sorted(data, key=_score_item, reverse=True)
                            for item in sorted_items:
                                fn = (item.get("FirstName") or "").strip()
                                ln = (item.get("LastName") or "").strip()
                                cand_full = f"{fn} {ln}".strip()
                                if not self._is_player_match(clean_full, cand_full):
                                    continue
                                found_player = item
                                break

                            if found_player:
                                break
                        except Exception as e:
                            logger.info("ATP search errore connessione per '%s' (term '%s'): %s", clean_full, term, e)

                    if not found_player:
                        return None

                    atp_id = (found_player.get("PlayerId") or "").strip().lower()
                    if not atp_id:
                        return None

                    # Preference 1: Gladiator headshot. Preference 2: Classic headshot
                    candidates = [
                        f"https://www.atptour.com/-/media/alias/player-gladiator-headshot/{atp_id}",
                        f"https://www.atptour.com/-/media/alias/player-headshot/{atp_id}",
                    ]
                    img_headers = {
                        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": "https://www.atptour.com/en/players",
                    }
                    for c_url in candidates:
                        try:
                            time.sleep(0.2)
                            r_img = session.get(c_url, headers=img_headers, timeout=8.0)
                            if r_img.status_code == 200:
                                # Reject ATP default empty grey silhouette (exactly 4729 bytes)
                                if len(r_img.content) == 4729:
                                    logger.info("ATP candidate rifiutato (silhouette grigia 4729b): %s", c_url)
                                    continue
                                ct = (r_img.headers.get("content-type") or "").lower()
                                if "image" in ct or r_img.content.startswith(b"\x89PNG") or r_img.content.startswith(b"\xff\xd8"):
                                    with io.BytesIO(r_img.content) as bio:
                                        with Image.open(bio) as loaded:
                                            img = loaded.convert("RGBA")
                                    if img.width >= 100 and img.height >= 100:
                                        return img, c_url
                            else:
                                logger.info("ATP candidate HTTP %d per: %s", r_img.status_code, c_url)
                        except Exception as e:
                            logger.info("ATP candidate errore per %s: %s", c_url, e)
            except Exception as e:
                logger.warning("ATP search error for '%s': %s", search_query, e)
            return None

        return await asyncio.to_thread(_search_sync)

    async def _search_wta(self, search_query: str) -> Optional[Tuple[Image.Image, str]]:
        """Level 2 (Femminile): Searches WTA Tour official directory for torso cutout on photoresources CloudFront."""
        headers = {"User-Agent": "curl/8.4.0", "Accept": "*/*"}
        async with httpx.AsyncClient(timeout=6.0, headers=headers) as client:
            try:
                clean_full = self.clean_player_name(search_query)
                surname = self._extract_single_player_surname(clean_full)

                content = []
                for term in [clean_full, surname]:
                    if not term or len(term) < 3:
                        continue
                    encoded = urllib.parse.quote(term.strip())
                    api_url = f"https://api.wtatennis.com/tennis/players?name={encoded}"
                    res = await client.get(api_url)
                    if res.status_code == 200:
                        content = res.json().get("content") or []
                        if content:
                            break

                if not content:
                    return None

                def _score_wta(item: dict) -> int:
                    full = (item.get("fullName") or "").lower()
                    last = (item.get("lastName") or "").lower()
                    q = clean_full.lower()
                    sur = (surname or "").lower()
                    if full == q: return 100
                    if last == sur and sur: return 80
                    if q in full: return 50
                    return 10

                sorted_players = sorted(content, key=_score_wta, reverse=True)
                for player in sorted_players[:3]:
                    cand_name = player.get("fullName", "")
                    if not self._is_player_match(clean_full, cand_name):
                        continue
                    pid = player.get("id")
                    if not pid:
                        continue
                    profile_url = f"https://www.wtatennis.com/players/{pid}/x"
                    r_prof = await client.get(profile_url, follow_redirects=True)
                    if r_prof.status_code != 200:
                        continue

                    # STRICT: Accept ONLY official transparent -Torso_ cutouts!
                    # Completely reject generic PNGs, site banners, social share cards (Home-Share), or UI logos.
                    m = re.search(r'(https://photoresources\.wtatennis\.com/photo-resources/[^"\'\s>]+-Torso_[^"\'\s>]+)', r_prof.text)
                    if m:
                        raw_url = m.group(1).split(',')[0].strip()
                        if any(bad in raw_url.lower() for bad in ["home-share", "share", "logo", "banner", "tile", "placeholder"]):
                            continue
                        clean_url = re.sub(r'\?.*$', '', raw_url) + "?height=600"
                        r_img = await client.get(clean_url)
                        if r_img.status_code == 200:
                            ct = (r_img.headers.get("content-type") or "").lower()
                            if "image" in ct or r_img.content.startswith(b"\x89PNG"):
                                with io.BytesIO(r_img.content) as bio:
                                    with Image.open(bio) as loaded:
                                        img = loaded.convert("RGBA")
                                if img.width >= 100 and img.height >= 100:
                                    return img, clean_url
            except Exception as e:
                logger.debug("WTA search error for '%s': %s", search_query, e)
        return None

    async def get_player_cutout(self, player_name: str, is_female: bool = False) -> Optional[Image.Image]:
        """
        Retrieves transparent headshot/cutout PNG for an individual tennis player.
        1. Checks database URL cache (tennis_athletes_cache). Actively validates URL (status 200, image/*).
           If broken, expired, or holding a blacklisted asset, auto-invalidates and purges immediately.
        2. Level 1: ESPN Search API (both ATP & WTA via fast JSON API).
        3. Level 2: Official circuits strictly partitioned by gender (ZERO cross-fallback):
           - ATP Tour for male events
           - WTA Tour for female events
        4. Keeps athlete images strictly in volatile memory (RAM), saving only URLs to SQLite.
        5. Level 3 fallback: None (caller falls back to neutral silhouette Unknow_male/Unknow_female).
        """
        search_query = (player_name or "").strip()
        if not search_query or len(search_query) < 3:
            return None

        slug = re.sub(r"[^a-zA-Z0-9]+", "_", search_query.lower()).strip("_")

        # 1. In-memory session negative cache
        if slug in self._missing_athletes:
            return None

        # 2. Check SQLite URL cache with Active Validation
        cached = db_service.get_tennis_athlete(slug)
        if cached and cached.get("image_url"):
            cached_url = cached["image_url"].lower()
            prov = (cached.get("provider") or "").lower()
            # Strict integrity check:
            # - WTA entries MUST be transparent '-torso_' cutouts
            # - ATP entries MUST be from atptour.com
            # - ESPN entries MUST be from espncdn.com
            is_valid_prov = (
                (prov == "wta" and "-torso_" in cached_url)
                or (prov == "atp" and "atptour.com" in cached_url)
                or (prov == "espn" and "espncdn.com" in cached_url)
            )
            is_blacklisted = any(bad in cached_url for bad in ["home-share", "share", "logo", "banner", "tile", "placeholder", "finals"])

            if not is_valid_prov or is_blacklisted:
                db_service.delete_tennis_athlete(slug)
            else:
                valid_img = await self._fetch_and_validate_image(cached["image_url"])
                # Athlete cutout must NOT be a landscape banner/tile
                if valid_img is not None and valid_img.width <= int(valid_img.height * 1.5):
                    logger.info("Tennis cutout da cache SQLite [%s]: '%s' -> %s", prov.upper(), search_query, cached["image_url"])
                    return valid_img
                # URL is broken, expired or landscape banner: Auto-Invalidation!
                logger.info("Tennis athlete cached URL invalid for '%s' (%s). Auto-invalidation triggered.", slug, cached["image_url"])
                db_service.delete_tennis_athlete(slug)

        # 3. Live Waterfall Search
        # Level 1: ESPN (Fast JSON API for both ATP and WTA)
        espn_res = await self._search_espn(search_query)
        if espn_res:
            img, img_url = espn_res
            gender_str = "female" if is_female else "male"
            logger.info("Tennis cutout trovato [ESPN]: '%s' -> %s", search_query, img_url)
            db_service.save_tennis_athlete(slug, "espn", img_url, gender_str)
            return img

        # Level 2: Official Circuits (Strict gender partition - ZERO cross-fallback!)
        if is_female:
            wta_res = await self._search_wta(search_query)
            if wta_res:
                img, img_url = wta_res
                logger.info("Tennis cutout trovato [WTA Tour]: '%s' -> %s", search_query, img_url)
                db_service.save_tennis_athlete(slug, "wta", img_url, "female")
                return img
        else:
            logger.info("Ricerca ATP Tour per '%s' (fallback da ESPN)...", search_query)
            atp_res = await self._search_atp(search_query)
            if atp_res:
                img, img_url = atp_res
                logger.info("Tennis cutout trovato [ATP Tour]: '%s' -> %s", search_query, img_url)
                db_service.save_tennis_athlete(slug, "atp", img_url, "male")
                return img

        # Level 3: Not found on official circuit -> Negative cache and return None (caller uses silhouette)
        logger.warning("Nessun cutout ufficiale trovato per '%s' (%s) -> Silhouette d'emergenza", search_query, "WTA" if is_female else "ATP")
        self._missing_athletes.add(slug)
        return None

    def get_theme_palette(self, genre: str, title: str, match: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Selects circuit background template and tournament title."""
        t_lower = (title or "").lower()
        g_lower = (genre or "").lower()
        comp_lower = ((match.get("competition") or match.get("_competition") or "") if match else "").lower()
        canon_lower = ((match.get("canonical_competition") or match.get("_canonical_competition") or "") if match else "").lower()
        combined = f"{g_lower} {t_lower} {comp_lower} {canon_lower}"

        # 1. Grand Slams (evaluated on title & genre)
        if "wimbledon" in combined:
            return {"bg_file": "bg_wimbledon.jpg", "badge_text": "WIMBLEDON"}
        if "roland" in combined or "french" in combined:
            return {"bg_file": "bg_roland_garros.jpg", "badge_text": "ROLAND GARROS"}
        if "australian" in combined:
            return {"bg_file": "bg_australian_open.jpg", "badge_text": "AUSTRALIAN OPEN"}
        if "us open" in combined:
            return {"bg_file": "bg_us_open.jpg", "badge_text": "US OPEN"}
        if "slam" in combined:
            return {"bg_file": "bg_us_open.jpg", "badge_text": "GRAND SLAM"}

        # 2. Team Cups (inspect title & real competition only; omit macro slug 'tennis_davis_bjk' which contains 'bjk' for all cups)
        event_text = f"{t_lower} {comp_lower}"
        if re.search(r"\bunited\s*cup\b", event_text):
            return {"bg_file": "bg_united_cup.jpg", "badge_text": "UNITED CUP"}
        if "laver" in event_text:
            return {"bg_file": "bg_laver_cup.jpg", "badge_text": "LAVER CUP"}
        if any(w in event_text for w in ("bjk", "billie", "king cup")):
            return {"bg_file": "bg_bjk_cup.jpg", "badge_text": "BJK CUP"}
        if "hopman" in event_text:
            return {"bg_file": "bg_davis_cup.jpg", "badge_text": "HOPMAN CUP"}
        if "davis" in event_text:
            return {"bg_file": "bg_davis_cup.jpg", "badge_text": "DAVIS CUP"}

        if re.search(r"\bunited\s*cup\b", g_lower):
            return {"bg_file": "bg_united_cup.jpg", "badge_text": "UNITED CUP"}
        if "laver" in g_lower:
            return {"bg_file": "bg_laver_cup.jpg", "badge_text": "LAVER CUP"}
        if "hopman" in g_lower:
            return {"bg_file": "bg_davis_cup.jpg", "badge_text": "HOPMAN CUP"}
        if "davis" in g_lower:
            return {"bg_file": "bg_davis_cup.jpg", "badge_text": "DAVIS CUP"}
        if any(w in g_lower for w in ("bjk", "billie", "king cup")):
            return {"bg_file": "bg_bjk_cup.jpg", "badge_text": "BJK CUP"}

        # 3. Explicit circuit identity in event TITLE (highest priority over any channel/genre noise)
        has_atp_title = bool(re.search(r"\batp\b", t_lower))
        from app.utils.demographics import extract_demographics
        has_wta_title = bool(re.search(r"\bwta\b", t_lower)) or extract_demographics(t_lower)[0]

        if has_atp_title and has_wta_title:
            return {"bg_file": "bg_atp.jpg", "badge_text": "ATP & WTA TOURS"}
        if has_atp_title:
            return {"bg_file": "bg_atp.jpg", "badge_text": "ATP TOUR"}
        if has_wta_title:
            return {"bg_file": "bg_wta.jpg", "badge_text": "WTA TOUR"}

        # 4. Fallback to classified genre if title does not declare the circuit
        has_atp_genre = "atp" in g_lower
        has_wta_genre = "wta" in g_lower or "femminile" in g_lower
        if has_atp_genre and has_wta_genre:
            return {"bg_file": "bg_atp.jpg", "badge_text": "ATP & WTA TOURS"}
        if has_atp_genre:
            return {"bg_file": "bg_atp.jpg", "badge_text": "ATP TOUR"}
        if has_wta_genre:
            return {"bg_file": "bg_wta.jpg", "badge_text": "WTA TOUR"}

        # 5. Challenger Tour & Altri
        if "challenger" in combined or "itf" in combined:
            return {"bg_file": "bg_challenger.jpg", "badge_text": "CHALLENGER TOUR"}

        # 6. Default ATP Tour
        return {"bg_file": "bg_atp.jpg", "badge_text": "ATP TOUR"}

    def _render_cinematic_arena(self, theme: Dict[str, Any], tourn_title: str) -> Image.Image:
        """Loads pre-rendered brand background template."""
        W, H = CANVAS_WIDTH, CANVAS_HEIGHT
        bg_name = theme.get("bg_file", "bg_atp.jpg")

        # Check static directory first (baked into Docker image), then data directory
        bg_path = STATIC_BACKGROUNDS_DIR / bg_name
        if not bg_path.exists():
            bg_path = BACKGROUNDS_DIR / bg_name

        if bg_path.exists():
            with Image.open(bg_path) as loaded:
                canvas = loaded.convert("RGBA")
        else:
            logger.warning(
                "Background template not found: '%s' (searched '%s' and '%s'). Using fallback.",
                bg_name, STATIC_BACKGROUNDS_DIR, BACKGROUNDS_DIR
            )
            canvas = Image.new("RGBA", (W, H), (8, 14, 28, 255))

        return canvas

    def _get_poster_lock(self, key: str) -> asyncio.Lock:
        loop_id = id(asyncio.get_running_loop())
        lock_key = (loop_id, key)
        lock = self._poster_locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            self._poster_locks[lock_key] = lock
        return lock

    async def _fetch_match_cutouts(
        self, p1_name: str, p2_name: str, is_female: bool = False
    ) -> Dict[str, Any]:
        """Asynchronously fetches player cutout images for singles or doubles matches."""
        team1 = self.parse_team_players(p1_name)
        team2 = self.parse_team_players(p2_name)
        is_doubles = len(team1) > 1 or len(team2) > 1

        if not is_doubles:
            p1_task = self.get_player_cutout(p1_name, is_female=is_female)
            p2_task = self.get_player_cutout(p2_name, is_female=is_female) if p2_name else asyncio.sleep(0, result=None)
            p1_img, p2_img = await asyncio.gather(p1_task, p2_task)
            src1 = "CUTOUT" if p1_img is not None else "SILHOUETTE_FALLBACK"
            src2 = "CUTOUT" if p2_img is not None else "SILHOUETTE_FALLBACK"
            logger.info("Tennis match compositing: '%s' [%s] vs '%s' [%s]", p1_name, src1, p2_name, src2)
            return {"is_doubles": False, "p1_img": p1_img, "p2_img": p2_img}

        p1_a_name = team1[0] if len(team1) > 0 else ""
        p1_b_name = team1[1] if len(team1) > 1 else ""
        p2_a_name = team2[0] if len(team2) > 0 else ""
        p2_b_name = team2[1] if len(team2) > 1 else ""

        tasks = [
            self.get_player_cutout(p1_a_name, is_female=is_female) if p1_a_name else asyncio.sleep(0, result=None),
            self.get_player_cutout(p1_b_name, is_female=is_female) if p1_b_name else asyncio.sleep(0, result=None),
            self.get_player_cutout(p2_a_name, is_female=is_female) if p2_a_name else asyncio.sleep(0, result=None),
            self.get_player_cutout(p2_b_name, is_female=is_female) if p2_b_name else asyncio.sleep(0, result=None),
        ]
        t1_a_img, t1_b_img, t2_a_img, t2_b_img = await asyncio.gather(*tasks)

        logger.info(
            "Tennis doubles match compositing: Team 1 [%s: %s, %s: %s] vs Team 2 [%s: %s, %s: %s]",
            p1_a_name, "CUTOUT" if t1_a_img is not None else "SILHOUETTE_FALLBACK",
            p1_b_name, "CUTOUT" if t1_b_img is not None else "SILHOUETTE_FALLBACK",
            p2_a_name, "CUTOUT" if t2_a_img is not None else "SILHOUETTE_FALLBACK",
            p2_b_name, "CUTOUT" if t2_b_img is not None else "SILHOUETTE_FALLBACK",
        )
        return {
            "is_doubles": True,
            "p1_b_name": p1_b_name,
            "p2_b_name": p2_b_name,
            "t1_a_img": t1_a_img,
            "t1_b_img": t1_b_img,
            "t2_a_img": t2_a_img,
            "t2_b_img": t2_b_img,
        }

    def _render_standard_match_sync(
        self,
        canvas: Image.Image,
        p1_name: str,
        p2_name: str,
        cutouts: Dict[str, Any],
        is_female: bool = False,
    ) -> Image.Image:
        """Synchronous CPU-bound player cutout compositing and lower-third banner rendering."""
        W, H = CANVAS_WIDTH, CANVAS_HEIGHT
        banner_h = 75
        banner_top = H - banner_h
        y_bottom = banner_top  # Player image ends exactly where the banner begins
        cx = W // 2

        unknown_cutout = self._get_unknown_cutout(is_female=is_female)

        if not cutouts.get("is_doubles"):
            target_h = 510
            p1_img = cutouts.get("p1_img")
            p2_img = cutouts.get("p2_img")

            # Player 1 (Left)
            img1 = p1_img or unknown_cutout
            if img1:
                w1 = int(img1.width * (target_h / img1.height))
                p1_resized = img1.resize((w1, target_h), Image.Resampling.LANCZOS)
                canvas.paste(p1_resized, (170 - w1 // 4, y_bottom - target_h), p1_resized)

            # Player 2 (Right)
            if p2_name:
                img2 = p2_img or unknown_cutout
                if img2:
                    w2 = int(img2.width * (target_h / img2.height))
                    p2_resized = img2.resize((w2, target_h), Image.Resampling.LANCZOS)
                    canvas.paste(p2_resized, (W - 170 - (w2 * 3) // 4, y_bottom - target_h), p2_resized)
        else:
            # Doubles layout: 2 players per side with depth of field and balanced sizing
            h_front = 400
            h_back = 350

            def _paste_athlete(img: Image.Image, target_cx: int, target_h: int):
                scale = target_h / img.height
                wb = int(img.width * scale)
                resized = img.resize((wb, target_h), Image.Resampling.LANCZOS)
                bbox = img.getbbox()
                if bbox:
                    athlete_center_x = ((bbox[0] + bbox[2]) / 2.0) * scale
                else:
                    athlete_center_x = wb / 2.0
                paste_x = int(target_cx - athlete_center_x)
                paste_y = y_bottom - target_h
                canvas.paste(resized, (paste_x, paste_y), resized)

            cx_left_outer = 170
            cx_left_inner = 390
            cx_right_inner = W - 390
            cx_right_outer = W - 170

            p1_b_name = cutouts.get("p1_b_name", "")
            p2_b_name = cutouts.get("p2_b_name", "")

            # Left Team - Player 1B (Back, outer left)
            img1_b = cutouts.get("t1_b_img") or unknown_cutout
            if p1_b_name and img1_b:
                _paste_athlete(img1_b, cx_left_outer, h_back)

            # Left Team - Player 1A (Front, inner left, layered over 1B)
            img1_a = cutouts.get("t1_a_img") or unknown_cutout
            if img1_a:
                _paste_athlete(img1_a, cx_left_inner, h_front)

            # Right Team - Player 2B (Back, outer right)
            img2_b = cutouts.get("t2_b_img") or unknown_cutout
            if p2_b_name and img2_b:
                _paste_athlete(img2_b, cx_right_outer, h_back)

            # Right Team - Player 2A (Front, inner right, layered over 2B)
            img2_a = cutouts.get("t2_a_img") or unknown_cutout
            if img2_a:
                _paste_athlete(img2_a, cx_right_inner, h_front)

        # Continuous Edge-to-Edge Broadcast Lower-Third Banner
        banner = Image.new("RGBA", (W, banner_h), (0, 0, 0, 0))
        b_draw = ImageDraw.Draw(banner)

        # Deep obsidian glass gradient background
        for y in range(banner_h):
            alpha = int(235 + 20 * (y / banner_h))  # 235 to 255
            r = int(10 + 4 * (y / banner_h))
            g = int(14 + 4 * (y / banner_h))
            b = int(24 + 6 * (y / banner_h))
            b_draw.line([(0, y), (W, y)], fill=(r, g, b, alpha))

        # Top metallic highlight lines
        b_draw.line([(0, 0), (W, 0)], fill=(255, 255, 255, 120), width=1)
        b_draw.line([(0, 1), (W, 1)], fill=(255, 255, 255, 40), width=1)

        # Subtle center divider accent (where VS axis meets the lower third)
        b_draw.line([(cx, 10), (cx, banner_h - 10)], fill=(255, 255, 255, 50), width=1)
        b_draw.ellipse([(cx - 3, banner_h // 2 - 3), (cx + 3, banner_h // 2 + 3)], fill=(255, 255, 255, 120))

        # Typography
        font_name = self._get_font(28)

        # Left Player / Team (Centered in left half)
        disp_p1 = self.format_short_display_name(p1_name)
        cx_left = cx // 2
        b_draw.text((cx_left, banner_h // 2), disp_p1, font=font_name, fill=(255, 255, 255), anchor="mm")

        # Right Player / Team (Centered in right half)
        if p2_name:
            disp_p2 = self.format_short_display_name(p2_name)
            cx_right = cx + (W - cx) // 2
            b_draw.text((cx_right, banner_h // 2), disp_p2, font=font_name, fill=(255, 255, 255), anchor="mm")

        # Composite banner onto canvas
        canvas.paste(banner, (0, banner_top), banner)

        return canvas

    def _render_stage_card(self, canvas: Image.Image, stage_name: str, tourn_name: str) -> Image.Image:
        """Renders an official bracket stage card (e.g. 'QUARTER FINALS') when players are not yet fixed."""
        W, H = CANVAS_WIDTH, CANVAS_HEIGHT
        cx, cy = W // 2, H // 2 + 30
        draw = ImageDraw.Draw(canvas)

        # Large Central Glass Plate
        card_w, card_h = 750, 280
        draw.rounded_rectangle(
            [(cx - card_w // 2, cy - card_h // 2), (cx + card_w // 2, cy + card_h // 2)],
            radius=18,
            fill=(10, 18, 35, 220),
            outline=(255, 255, 255, 90),
            width=2,
        )

        # Stage Pill
        font_stage = self._get_font(38)
        draw.text((cx, cy - 40), stage_name.upper(), font=font_stage, fill=(255, 215, 0), anchor="mm")

        # Subtitle
        font_sub = self._get_font(26)
        draw.text((cx, cy + 30), tourn_name.upper(), font=font_sub, fill=(255, 255, 255, 230), anchor="mm")

        # Live Broadcast Note
        font_note = self._get_font(18)
        draw.text((cx, cy + 85), "STREAMING LIVE MATCH", font=font_note, fill=(147, 197, 253, 200), anchor="mm")

        return canvas

    def _fit_rounded_flag(self, flag_im: Image.Image, target_w: int, target_h: int, radius: int = 18) -> Image.Image:
        """Aspect-fill crops a flag image and applies rounded corners."""
        fw, fh = flag_im.size
        scale = max(target_w / fw, target_h / fh)
        nw, nh = int(round(fw * scale)), int(round(fh * scale))
        resized = flag_im.resize((nw, nh), Image.Resampling.LANCZOS)
        left = (nw - target_w) // 2
        top = (nh - target_h) // 2
        cropped = resized.crop((left, top, left + target_w, top + target_h))

        mask = Image.new("L", (target_w, target_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, target_w - 1, target_h - 1], radius=radius, fill=255)

        out = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
        out.paste(cropped, (0, 0), mask)
        return out

    def _fit_wrapped_country_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        max_w: int,
    ) -> Tuple[List[str], ImageFont.ImageFont]:
        """Chooses the largest crisp font (with optional 2-line wrap) fitting max_w for fallback cards."""
        upper_text = (text or "TBD").upper().strip()
        words = upper_text.split()
        for size in (44, 38, 34, 30, 26, 22):
            font = self._get_font(size)
            bbox = draw.textbbox((0, 0), upper_text, font=font)
            if (bbox[2] - bbox[0]) <= max_w:
                return [upper_text], font
            if len(words) >= 2:
                # Try balanced 2-line split
                best_split: Optional[Tuple[str, str]] = None
                best_diff = 10**9
                for idx in range(1, len(words)):
                    l1 = " ".join(words[:idx])
                    l2 = " ".join(words[idx:])
                    w1 = draw.textbbox((0, 0), l1, font=font)[2]
                    w2 = draw.textbbox((0, 0), l2, font=font)[2]
                    if w1 <= max_w and w2 <= max_w and abs(w1 - w2) < best_diff:
                        best_diff = abs(w1 - w2)
                        best_split = (l1, l2)
                if best_split:
                    return list(best_split), font
        return [upper_text], self._get_font(20)

    def _render_country_match(
        self,
        canvas: Image.Image,
        c1_name: str,
        c2_name: str,
        tourn_name: str,
        flags: Optional[Dict[str, Any]] = None,
    ) -> Image.Image:
        """
        Renders National Team tie posters (Davis Cup, Billie Jean King Cup, United Cup).
        Draws clean standalone rounded flags directly on the canvas; only draws a dark card
        with the clean Italianized nation name when a flag is unavailable.
        """
        W, H = CANVAS_WIDTH, CANVAS_HEIGHT
        cy = int(H * 0.55)
        box_w, box_h = 364, 236
        radius = 18
        draw = ImageDraw.Draw(canvas)

        info1 = (flags or {}).get("info1") or self.resolve_country(c1_name)
        info2 = (flags or {}).get("info2") or self.resolve_country(c2_name)
        sides = [
            (300, (flags or {}).get("c1"), info1["display"]),
            (W - 300, (flags or {}).get("c2"), info2["display"]),
        ]

        for cx, flag_im, display_name in sides:
            fx = cx - box_w // 2
            fy = cy - box_h // 2
            if flag_im is not None:
                rounded = self._fit_rounded_flag(flag_im, box_w, box_h, radius=radius)
                canvas.paste(rounded, (fx, fy), rounded)
                # Single subtle rim directly on the flag edge (no double outer card)
                draw.rounded_rectangle(
                    [(fx, fy), (fx + box_w - 1, fy + box_h - 1)],
                    radius=radius,
                    outline=(255, 255, 255, 115),
                    width=2,
                )
            else:
                # Fallback ONLY: dark card with clean, large Italianized country/team name
                draw.rounded_rectangle(
                    [(fx, fy), (fx + box_w - 1, fy + box_h - 1)],
                    radius=radius,
                    fill=(10, 18, 35, 225),
                    outline=(255, 255, 255, 110),
                    width=2,
                )
                lines, font_fallback = self._fit_wrapped_country_text(draw, display_name, box_w - 40)
                if len(lines) == 1:
                    draw.text((cx, cy), lines[0], font=font_fallback, fill=(255, 255, 255), anchor="mm")
                else:
                    line_gap = int(font_fallback.size * 1.28) if hasattr(font_fallback, "size") else 40
                    top_y = cy - line_gap // 2
                    for i, line in enumerate(lines[:2]):
                        draw.text((cx, top_y + i * line_gap), line, font=font_fallback, fill=(255, 255, 255), anchor="mm")

        return canvas

    def _compose_and_save_poster_sync(
        self,
        target_path: Path,
        fallback_path: Path,
        filename: str,
        title: str,
        genre: str,
        stage_info: Optional[Dict[str, str]],
        p1_name: str,
        p2_name: str,
        is_country: bool,
        is_fem: bool,
        cutouts: Optional[Dict[str, Any]],
        match: Optional[Dict[str, Any]] = None,
        flags: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Executes CPU-bound Pillow rendering and JPEG saving cleanly inside a worker thread."""
        theme = self.get_theme_palette(genre, title, match=match)
        tourn_name, _ = self.extract_tournament_and_stage(title, genre)

        canvas = self._render_cinematic_arena(theme, tourn_name)
        if stage_info:
            canvas = self._render_stage_card(canvas, stage_info["stage"], stage_info["tournament"])
        elif is_country:
            canvas = self._render_country_match(canvas, p1_name, p2_name, tourn_name, flags=flags)
        elif not p2_name:
            # Failsafe: if there is no second competitor, never render an asymmetric single-sided match
            canvas = self._render_stage_card(canvas, "LIVE BROADCAST", tourn_name)
        else:
            canvas = self._render_standard_match_sync(
                canvas, p1_name, p2_name, cutouts or {}, is_female=is_fem
            )

        final_rgb = canvas.convert("RGB")
        saved = False
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            final_rgb.save(target_path, "JPEG", quality=90, optimize=True)
            saved = True
        except Exception as e:
            logger.warning("Failed saving tennis poster to primary %s: %s (attempting temp fallback)", target_path, e)
            try:
                fallback_path.parent.mkdir(parents=True, exist_ok=True)
                final_rgb.save(fallback_path, "JPEG", quality=90, optimize=True)
                self.posters_dir = fallback_path.parent
                saved = True
            except Exception as e2:
                logger.error("Failed saving tennis poster to fallback %s: %s", fallback_path, e2)

        if saved:
            logger.info("Generated tennis poster: %s (%s)", filename, title)
            return f"/posters/{filename}"
        return None

    async def generate_poster(
        self, match_id: str, title: str, genre: str, match: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Creates and saves a 16:9 landscape poster for a tennis match.
        Returns the relative web path (e.g. '/posters/tennis_xyz.jpg').
        """
        if not HAS_PIL:
            return None

        clean_key = hashlib.md5(match_id.encode("utf-8")).hexdigest()[:12]
        filename = f"tennis_{clean_key}_v{TENNIS_POSTER_VERSION}.jpg"
        target_path = self.posters_dir / filename
        fallback_path = Path(tempfile.gettempdir()) / "streamsport_data" / "posters" / filename

        if target_path.exists() or fallback_path.exists():
            return f"/posters/{filename}"

        lock = self._get_poster_lock(filename)
        async with lock:
            if target_path.exists() or fallback_path.exists():
                return f"/posters/{filename}"

            stage_info = self.is_stage_placeholder(title)
            p1_name = ""
            p2_name = ""
            is_country = False
            is_fem = False
            cutouts: Optional[Dict[str, Any]] = None
            flags: Optional[Dict[str, Any]] = None

            if not stage_info:
                p1_name, p2_name = self.parse_players(title)
                if not p1_name:
                    return None
                await self._ensure_espn_country_index()
                is_fem = self.is_female_competition(title, genre, match=match)
                is_country = self.is_country_match(title, p1_name, p2_name, genre, match=match)
                if is_country:
                    flags = await self._fetch_match_flags(p1_name, p2_name, match=match)
                elif p2_name:
                    cutouts = await self._fetch_match_cutouts(p1_name, p2_name, is_female=is_fem)

            return await asyncio.to_thread(
                self._compose_and_save_poster_sync,
                target_path,
                fallback_path,
                filename,
                title,
                genre,
                stage_info,
                p1_name,
                p2_name,
                is_country,
                is_fem,
                cutouts,
                match,
                flags,
            )

    def start_background_enrichment(self, matches: List[Dict[str, Any]]):
        """Asynchronously triggers poster generation for tennis matches lacking artwork."""
        if not HAS_PIL:
            logger.warning("Pillow (PIL) is not installed. Dynamic tennis poster generation is disabled.")
            return

        tennis_matches = [
            m for m in matches
            if (m.get("category") == "tennis" or m.get("_catalog") == "tennis")
            and (
                not m.get("poster")
                or "nostream" in str(m.get("poster"))
                or (str(m.get("poster")).startswith("/posters/tennis_") and not str(m.get("poster")).endswith(f"_v{TENNIS_POSTER_VERSION}.jpg"))
            )
            and not any(k in str(m.get("poster", "")).lower() for k in ("guidatv.sky.it", "sky.it", "apid.sky.it", "tivulaguida", "thesportsdb"))
            and m.get("title")
        ]

        if not tennis_matches:
            return

        if self._is_enriching and self._enrichment_task and not self._enrichment_task.done():
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()

        self._is_enriching = True
        self._enrichment_task = loop.create_task(self._process_matches_queue(tennis_matches))

    async def _process_matches_queue(self, tennis_matches: List[Dict[str, Any]]):
        logger.info("Starting background tennis poster generation for %d matches...", len(tennis_matches))
        sem = asyncio.Semaphore(2)

        async def _enrich_one(m: Dict[str, Any]):
            async with sem:
                m_id = str(m.get("id", ""))
                title = m.get("title", "")
                genre = m.get("_genre")
                if not genre:
                    from app.services.genre_classifier import genre_classifier
                    _, genre = genre_classifier.classify(m)
                try:
                    rel_poster = await self.generate_poster(m_id, title, genre, match=m)
                    if rel_poster:
                        m["poster"] = rel_poster
                        await db_service.save_matches_async([m])
                        from app.services.banner_service import banner_service
                        banner_service.invalidate_match(m_id)
                except Exception as e:
                    logger.debug("Failed generating tennis poster for '%s': %s", title, e)

        tasks = [_enrich_one(m) for m in tennis_matches]
        await asyncio.gather(*tasks, return_exceptions=True)
        self._is_enriching = False
        logger.info("Tennis poster background generation complete.")

    def cleanup_finished_events(self, active_matches: List[Dict[str, Any]]):
        """
        Purges athlete cutouts and generated match posters for events that have concluded.
        Guarantees that disk storage remains lean and that players missing cutouts
        will be re-queried during their next tournaments if official photos become available.
        """
        if not HAS_PIL:
            return

        active_slugs: Set[str] = set()
        active_poster_filenames: Set[str] = set()

        for m in active_matches:
            cat = (m.get("category") or m.get("_catalog") or "").lower()
            if "tennis" not in cat:
                continue

            m_id = str(m.get("id", ""))
            if m_id:
                clean_key = hashlib.md5(m_id.encode("utf-8")).hexdigest()[:12]
                active_poster_filenames.add(f"tennis_{clean_key}_v{TENNIS_POSTER_VERSION}.jpg")

            title = m.get("title", "")
            p1_name, p2_name = self.parse_players(title)
            for p_str in (p1_name, p2_name):
                for single_p in self.parse_team_players(p_str):
                    slug = re.sub(r"[^a-zA-Z0-9]+", "_", single_p.lower()).strip("_")
                    if slug:
                        active_slugs.add(slug)

        # 1. Clean ATHLETES_DIR: keep Unknown.png and currently active athletes
        purged_athletes = 0
        for f in ATHLETES_DIR.glob("*"):
            if f.is_file():
                if f.suffix.lower() == ".missing":
                    try:
                        f.unlink()
                        purged_athletes += 1
                    except Exception:
                        pass
                elif f.suffix.lower() == ".png":
                    if f.name.lower() in (
                        "unknown.png",
                        "tennis_player_unknow.png",
                        "tennis_player_unknown.png",
                        "unknow_male.png",
                        "unknow_female.png",
                        "unknown_male.png",
                        "unknown_female.png",
                    ):
                        continue
                    if f.stem not in active_slugs:
                        try:
                            f.unlink()
                            purged_athletes += 1
                        except Exception:
                            pass

        # 2. Clean POSTERS_DIR: remove stale tennis posters not in active catalog
        purged_posters = 0
        for f in POSTERS_DIR.glob(f"tennis_*_v{TENNIS_POSTER_VERSION}.jpg"):
            if f.name not in active_poster_filenames:
                try:
                    f.unlink()
                    purged_posters += 1
                except Exception:
                    pass

        # Clear in-memory missing athletes so future tournaments will re-query
        self._missing_athletes.clear()

        if purged_athletes > 0 or purged_posters > 0:
            logger.info(
                "Tennis Cache Cleanup: eliminati %d ritagli atleti e %d locandine di match conclusi.",
                purged_athletes, purged_posters
            )


tennis_poster_service = TennisPosterService()
