from __future__ import annotations
import asyncio
import hashlib
import io
import logging
import os
from pathlib import Path
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None
    ImageDraw = None
    ImageFont = None
    ImageFilter = None

from app.config import PROJECT_DIR
from app.services.db_service import db_service

logger = logging.getLogger("streamsport.tennis_poster")

DATA_DIR = PROJECT_DIR / "data"
POSTERS_DIR = DATA_DIR / "posters"
ATHLETES_DIR = POSTERS_DIR / "athletes"
ASSETS_DIR = POSTERS_DIR / "assets"
BACKGROUNDS_DIR = POSTERS_DIR / "backgrounds"

# Static directory (baked into Docker image, never masked by volume mounts)
STATIC_DIR = PROJECT_DIR / "app" / "static" / "posters"
STATIC_BACKGROUNDS_DIR = STATIC_DIR / "backgrounds"
STATIC_ASSETS_DIR = STATIC_DIR / "assets"

CANVAS_WIDTH = 1225
CANVAS_HEIGHT = 700

KNOWN_COUNTRIES = {
    "italy", "italia", "china", "cina", "usa", "united states", "spain", "spagna",
    "france", "francia", "germany", "germania", "great britain", "gran bretagna", "uk",
    "australia", "canada", "argentina", "japan", "giappone", "netherlands", "olanda",
    "belgium", "belgio", "switzerland", "svizzera", "serbia", "croatia", "croazia",
    "czech republic", "repubblica ceca", "poland", "polonia", "brazil", "brasile",
    "sweden", "svezia", "austria", "kazakhstan", "kazakistan", "ukraine", "ucraina",
    "slovakia", "slovacchia", "chile", "cile", "colombia", "mexico", "messico",
    "korea", "south korea", "corea del sud", "norway", "norvegia", "denmark", "danimarca",
    "finland", "finlandia", "romania", "hungary", "ungheria", "greece", "grecia",
    "portugal", "portogallo", "india", "israel", "taiwan", "hong kong", "thailand",
    "indonesia", "paraguay", "south africa", "georgia", "latvia", "lettonia", "philippines"
}

CIRCUIT_NAMES = {"atp", "wta", "itf", "challenger", "davis cup", "bjk cup", "united cup", "laver cup"}

STAGE_REGEX = re.compile(
    r"((?:couples?\s+)?1/[248]\s*final(?:\s*\d+)?|quarter[- ]?finals?(?:\s*\d+)?|semi[- ]?finals?(?:\s*\d+)?|\bfinal\b|\bfinals\b|round of \d+|round \d+|r\d{1,2}\b|singles|doubles|qualification|qualifying|day \d+|session \d+|warmup|warm[- ]up|\batp\s*(?:&|and|\+)\s*wta\b|\bwta\s*(?:&|and|\+)\s*atp\b)",
    re.I
)

TOURNAMENT_STAGE_WORDS = {
    "atp", "wta", "itf", "challenger", "tour", "open", "finals", "final",
    "semi-final", "quarter-final", "round", "day", "session", "singles",
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
        POSTERS_DIR.mkdir(parents=True, exist_ok=True)
        ATHLETES_DIR.mkdir(parents=True, exist_ok=True)
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)
        self._font_path = self._find_font()
        self._enrichment_task: Optional[asyncio.Task] = None
        self._is_enriching: bool = False
        self._tsdb_lock = asyncio.Lock()
        self._last_tsdb_call: float = 0.0
        self._unknown_cutout_cache: Optional[Image.Image] = None

        # Pre-render missing background templates
        self._ensure_background_templates()

        # In-memory negative cache only (session-based). Clean any legacy .missing files from disk.
        self._missing_athletes: Set[str] = set()
        for p in ATHLETES_DIR.glob("*.missing"):
            try:
                p.unlink()
            except Exception:
                pass

        # Purge legacy v1/v2 posters to free space and invalidate stale client caches
        for old_file in POSTERS_DIR.glob("tennis_*.jpg"):
            if not old_file.name.endswith("_v3.jpg"):
                try:
                    old_file.unlink()
                except Exception:
                    pass

    def _ensure_background_templates(self):
        """Pre-renders clean, minimal 16:9 backgrounds with official tournament logos centered at top."""
        if not HAS_PIL:
            return

        BACKGROUNDS_DIR.mkdir(parents=True, exist_ok=True)

        def _make_white_logo(img: Image.Image) -> Image.Image:
            r, g, b, a = img.split()
            white = Image.new("RGBA", img.size, (255, 255, 255, 255))
            white.putalpha(a)
            return white

        def _stamp_preset(bg_filename: str, logo_filename: str, target_h: int, top_y: int,
                          force_white: bool, out_filename: str):
            out_path = BACKGROUNDS_DIR / out_filename
            if out_path.exists():
                return
            bg_src = BACKGROUNDS_DIR / bg_filename
            logo_src = ASSETS_DIR / logo_filename
            if not bg_src.exists() or not logo_src.exists():
                return
            try:
                bg = Image.open(bg_src).convert("RGBA")
                logo = Image.open(logo_src).convert("RGBA")
                if force_white:
                    logo = _make_white_logo(logo)
                aspect = logo.width / logo.height
                target_w = int(target_h * aspect)
                resized = logo.resize((target_w, target_h), Image.Resampling.LANCZOS)
                pad = 24
                padded = Image.new("RGBA", (target_w + pad * 2, target_h + pad * 2), (0, 0, 0, 0))
                padded.paste(resized, (pad, pad), resized)
                r, g, b, a = padded.split()
                shadow = Image.new("RGBA", padded.size, (0, 0, 0, 220))
                shadow.putalpha(a)
                if ImageFilter:
                    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
                final_logo = Image.new("RGBA", padded.size, (0, 0, 0, 0))
                final_logo.paste(shadow, (0, 0), shadow)
                final_logo.paste(padded, (0, 0), padded)
                W, H = bg.size
                paste_x = (W - final_logo.width) // 2
                bg.paste(final_logo, (paste_x, top_y), final_logo)
                bg.convert("RGB").save(out_path, "JPEG", quality=95)
            except Exception as e:
                logger.debug("Failed building background %s: %s", out_filename, e)

        configs = [
            ("bg_atp2.jpg", "atp.png", 135, 45, True, "bg_atp.jpg"),
            ("bg_wta2.jpg", "wta2.png", 105, 48, True, "bg_wta.jpg"),
            ("bg_australian_open2.jpg", "australian_open.png", 85, 52, False, "bg_australian_open.jpg"),
            ("bg_roland_garros2.jpg", "roland_garros.png", 105, 45, False, "bg_roland_garros.jpg"),
            ("bg_wimbledon2.jpg", "wimbledon.png", 105, 45, False, "bg_wimbledon.jpg"),
            ("bg_us_open2.jpg", "us_open.png", 90, 50, False, "bg_us_open.jpg"),
            ("bg_davis_cup2.jpg", "davis_cup.png", 80, 50, False, "bg_davis_cup.jpg"),
            ("bg_bjk_cup2.jpg", "bjk_cup.png", 80, 52, True, "bg_bjk_cup.jpg"),
            ("bg_laver_cup2.jpg", "laver_cup.png", 100, 48, True, "bg_laver_cup.jpg"),
            ("bg_united_cup2.jpg", "united_cup.png", 105, 45, False, "bg_united_cup.jpg"),
            ("bg_challenger2.jpg", "challenger.png", 75, 52, False, "bg_challenger.jpg"),
        ]

        for bg_f, l_f, th, ty, fw, out_f in configs:
            _stamp_preset(bg_f, l_f, th, ty, fw, out_f)

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

    @staticmethod
    def format_short_display_name(full_name: str) -> str:
        """Formats 'Jannik Sinner' into 'J. SINNER' or doubles 'Garland / Hsieh' into 'GARLAND / HSIEH'."""
        name = full_name.strip()
        if "/" in name:
            sub = [p.strip().split()[-1].upper() for p in name.split("/") if p.strip()]
            return " / ".join(sub[:2])
        parts = [p for p in name.split() if p]
        if not parts:
            return name.upper()
        if len(parts) == 1:
            return parts[0].upper()
        initial = parts[0][0].upper()
        last = " ".join(parts[1:]).upper()
        return f"{initial}. {last}"

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
            raw_stage = re.sub(r"1/4\s*FINAL", "QUARTER FINAL", raw_stage)
            raw_stage = re.sub(r"1/2\s*FINAL", "SEMI FINAL", raw_stage)
            raw_stage = re.sub(r"COUPLES?\s*", "DOUBLES ", raw_stage).strip()
            if any(k in raw_stage.lower() for k in ("atp & wta", "atp and wta", "wta & atp", "wta and atp")):
                raw_stage = "LIVE BROADCAST"
                if tourn == "ATP TOUR":
                    tourn = "ATP & WTA TOURS"
            stage = raw_stage

        return tourn, stage

    def is_stage_placeholder(self, title: str) -> Optional[Dict[str, str]]:
        """Returns stage info if title is a tournament bracket stage announcement without player vs player."""
        has_vs = " vs " in (title or "").lower()
        t_clean = (title or "").strip().lower()

        # Direct broadcast title match for generic circuit feeds
        if t_clean in ("atp & wta", "atp and wta", "wta & atp", "wta and atp", "atp + wta", "wta + atp", "atp", "wta"):
            return {"tournament": "ATP & WTA TOURS", "stage": "LIVE BROADCAST"}

        m = STAGE_REGEX.search(title or "")
        if m and not has_vs:
            tourn, stage = self.extract_tournament_and_stage(title, "")
            return {"tournament": tourn, "stage": stage or m.group(1).upper()}
        return None

    def is_country_match(self, title: str, p1: str, p2: str, genre: str) -> bool:
        """Identifies national team matches (Davis Cup, Billie Jean King Cup, United Cup)."""
        combined = f"{title} {genre}".lower()
        if any(w in combined for w in ["billie jean king", "bjk cup", "davis cup", "united cup"]):
            return True
        c1 = p1.lower().strip()
        c2 = p2.lower().strip()
        if c1 in KNOWN_COUNTRIES and c2 in KNOWN_COUNTRIES:
            return True
        return False

    def _get_unknown_cutout(self) -> Optional[Image.Image]:
        """Loads the official locked/unknown athlete bust silhouette asset."""
        if self._unknown_cutout_cache is not None:
            return self._unknown_cutout_cache
        for p in (STATIC_ASSETS_DIR / "Unknown.png", ASSETS_DIR / "Unknown.png"):
            if p.exists():
                try:
                    self._unknown_cutout_cache = Image.open(p).convert("RGBA")
                    return self._unknown_cutout_cache
                except Exception as e:
                    logger.warning("Failed loading Unknown.png from %s: %s", p, e)
        return None

    async def get_player_cutout(self, player_name: str) -> Optional[Image.Image]:
        """
        Retrieves transparent headshot/cutout PNG for an individual player.
        Checks local cache first, then ESPN Search API, then TheSportsDB cutout fallback.
        Strictly enforces sport == 'tennis' and cutout only (rejects hockey, soccer, jersey renders).
        Does not persist .missing markers on disk, ensuring future tournament re-queries.
        """
        search_query = (player_name or "").strip()
        if not search_query or len(search_query) < 3:
            return None

        slug = re.sub(r"[^a-zA-Z0-9]+", "_", search_query.lower()).strip("_")
        cached_file = ATHLETES_DIR / f"{slug}.png"

        if cached_file.exists():
            try:
                return Image.open(cached_file).convert("RGBA")
            except Exception:
                pass

        if slug in self._missing_athletes:
            return None

        headers = {
            "User-Agent": "curl/8.4.0",
            "Accept": "*/*",
        }

        async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
            # 1. ESPN Search API for Tennis Player Headshot
            try:
                espn_url = f"https://site.web.api.espn.com/apis/search/v2?query={urllib.parse.quote(search_query)}"
                res = await client.get(espn_url)
                if res.status_code == 200:
                    data = res.json()
                    for r in data.get("results", []):
                        if r.get("type") == "player":
                            for item in r.get("contents", []):
                                sport = str(item.get("sport") or "").lower()
                                desc = str(item.get("description") or "").lower()
                                img_url = (item.get("image") or {}).get("default") or ""

                                # STRICT SPORT VALIDATION: Must be Tennis!
                                is_tennis = (sport == "tennis") or ("tennis" in desc) or ("/tennis/" in img_url.lower())
                                if not is_tennis:
                                    continue

                                # Reject generic placeholder logos
                                if any(ph in img_url.lower() for ph in ["default-player-logo", "silhouette", "placeholder"]):
                                    continue

                                if img_url.startswith("http"):
                                    img_res = await client.get(img_url)
                                    if img_res.status_code == 200:
                                        img = Image.open(io.BytesIO(img_res.content)).convert("RGBA")
                                        img.save(cached_file, "PNG")
                                        return img
            except Exception as e:
                logger.debug("ESPN athlete search error for '%s': %s", search_query, e)

            # 2. TheSportsDB Cutout Fallback (Rate Limited to 2.5s)
            async with self._tsdb_lock:
                now = time.monotonic()
                elapsed = now - self._last_tsdb_call
                if elapsed < 2.5:
                    await asyncio.sleep(2.5 - elapsed)
                self._last_tsdb_call = time.monotonic()

                try:
                    tsdb_url = f"https://www.thesportsdb.com/api/v1/json/3/searchplayers.php?p={urllib.parse.quote(search_query)}"
                    res = await client.get(tsdb_url)
                    if res.status_code == 200:
                        data = res.json()
                        players = data.get("player") or []
                        for p in players:
                            sport = str(p.get("strSport") or "").lower()
                            # STRICT SPORT VALIDATION: Must be Tennis!
                            if sport != "tennis":
                                continue

                            # ONLY ACCEPT transparent cutout PNG. Never strThumb or strRender!
                            cutout_url = p.get("strCutout")
                            if cutout_url and cutout_url.startswith("http") and cutout_url.lower().endswith(".png"):
                                img_res = await client.get(cutout_url)
                                if img_res.status_code == 200:
                                    img = Image.open(io.BytesIO(img_res.content)).convert("RGBA")
                                    img.save(cached_file, "PNG")
                                    return img
                except Exception as e:
                    logger.debug("TheSportsDB athlete search error for '%s': %s", search_query, e)

        # In-memory negative cache only (no .missing files created on disk)
        self._missing_athletes.add(slug)
        return None

    def get_theme_palette(self, genre: str, title: str) -> Dict[str, Any]:
        """Selects circuit background template and tournament title."""
        t_lower = (title or "").lower()
        g_lower = (genre or "").lower()
        combined = f"{g_lower} {t_lower}"

        # 1. Grand Slams
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

        # 2. WTA Tour
        if "wta" in combined or "femminile" in combined:
            return {"bg_file": "bg_wta.jpg", "badge_text": "WTA TOUR"}

        # 3. Team Cups (United Cup, Laver Cup, Davis Cup, Billie Jean King Cup)
        if "united" in combined:
            return {"bg_file": "bg_united_cup.jpg", "badge_text": "UNITED CUP"}
        if "laver" in combined:
            return {"bg_file": "bg_laver_cup.jpg", "badge_text": "LAVER CUP"}
        if any(w in t_lower for w in ("bjk", "billie", "king cup")):
            return {"bg_file": "bg_bjk_cup.jpg", "badge_text": "DAVIS CUP"}
        if "davis" in t_lower:
            return {"bg_file": "bg_davis_cup.jpg", "badge_text": "DAVIS CUP"}
        if any(w in g_lower for w in ("davis", "king cup", "bjk")):
            return {"bg_file": "bg_davis_cup.jpg", "badge_text": "DAVIS CUP"}

        # 4. Challenger Tour & Altri
        if "challenger" in combined or "itf" in combined:
            return {"bg_file": "bg_challenger.jpg", "badge_text": "CHALLENGER TOUR"}

        # 5. Default ATP Tour
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
            canvas = Image.open(bg_path).convert("RGBA")
        else:
            logger.warning(
                "Background template not found: '%s' (searched '%s' and '%s'). Using fallback.",
                bg_name, STATIC_BACKGROUNDS_DIR, BACKGROUNDS_DIR
            )
            canvas = Image.new("RGBA", (W, H), (8, 14, 28, 255))

        return canvas

    async def _render_standard_match(self, canvas: Image.Image, p1_name: str, p2_name: str) -> Image.Image:
        """Renders player cutouts (supporting both Singles and Doubles) and broadcast lower-third banner."""
        W, H = CANVAS_WIDTH, CANVAS_HEIGHT
        banner_h = 75
        banner_top = H - banner_h
        y_bottom = banner_top  # Player image ends exactly where the banner begins
        cx = W // 2

        unknown_cutout = self._get_unknown_cutout()

        team1 = self.parse_team_players(p1_name)
        team2 = self.parse_team_players(p2_name)
        is_doubles = len(team1) > 1 or len(team2) > 1

        if not is_doubles:
            target_h = 510
            p1_task = self.get_player_cutout(p1_name)
            p2_task = self.get_player_cutout(p2_name) if p2_name else asyncio.sleep(0, result=None)
            p1_img, p2_img = await asyncio.gather(p1_task, p2_task)

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
            # Doubles layout: 2 players per side with depth of field
            p1_a_name = team1[0] if len(team1) > 0 else ""
            p1_b_name = team1[1] if len(team1) > 1 else ""
            p2_a_name = team2[0] if len(team2) > 0 else ""
            p2_b_name = team2[1] if len(team2) > 1 else ""

            tasks = [
                self.get_player_cutout(p1_a_name) if p1_a_name else asyncio.sleep(0, result=None),
                self.get_player_cutout(p1_b_name) if p1_b_name else asyncio.sleep(0, result=None),
                self.get_player_cutout(p2_a_name) if p2_a_name else asyncio.sleep(0, result=None),
                self.get_player_cutout(p2_b_name) if p2_b_name else asyncio.sleep(0, result=None),
            ]
            t1_a_img, t1_b_img, t2_a_img, t2_b_img = await asyncio.gather(*tasks)

            h_front = 500
            h_back = 440

            # Left Team - Player 1B (Back, shifted towards outside)
            img1_b = t1_b_img or unknown_cutout
            if p1_b_name and img1_b:
                wb1 = int(img1_b.width * (h_back / img1_b.height))
                b1_resized = img1_b.resize((wb1, h_back), Image.Resampling.LANCZOS)
                canvas.paste(b1_resized, (60, y_bottom - h_back), b1_resized)

            # Left Team - Player 1A (Front, shifted towards center, pasted over 1B)
            img1_a = t1_a_img or unknown_cutout
            if img1_a:
                wa1 = int(img1_a.width * (h_front / img1_a.height))
                a1_resized = img1_a.resize((wa1, h_front), Image.Resampling.LANCZOS)
                canvas.paste(a1_resized, (220, y_bottom - h_front), a1_resized)

            # Right Team - Player 2B (Back, shifted towards outside)
            img2_b = t2_b_img or unknown_cutout
            if p2_b_name and img2_b:
                wb2 = int(img2_b.width * (h_back / img2_b.height))
                b2_resized = img2_b.resize((wb2, h_back), Image.Resampling.LANCZOS)
                canvas.paste(b2_resized, (W - 60 - wb2, y_bottom - h_back), b2_resized)

            # Right Team - Player 2A (Front, shifted towards center, pasted over 2B)
            img2_a = t2_a_img or unknown_cutout
            if p2_a_name and img2_a:
                wa2 = int(img2_a.width * (h_front / img2_a.height))
                a2_resized = img2_a.resize((wa2, h_front), Image.Resampling.LANCZOS)
                canvas.paste(a2_resized, (W - 220 - wa2, y_bottom - h_front), a2_resized)

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

    def _render_country_match(self, canvas: Image.Image, c1_name: str, c2_name: str, tourn_name: str) -> Image.Image:
        """Renders National Team tie poster (e.g. 'ITALY vs CHINA') for Davis Cup and Billie Jean King Cup."""
        W, H = CANVAS_WIDTH, CANVAS_HEIGHT
        cx, cy = W // 2, int(H * 0.55)
        draw = ImageDraw.Draw(canvas)

        code_map = {
            "italy": "ITA", "italia": "ITA", "china": "CHN", "cina": "CHN", "usa": "USA",
            "united states": "USA", "spain": "ESP", "spagna": "ESP", "france": "FRA", "francia": "FRA",
            "germany": "GER", "germania": "GER", "great britain": "GBR", "gran bretagna": "GBR", "uk": "GBR",
            "australia": "AUS", "canada": "CAN", "argentina": "ARG", "japan": "JPN", "giappone": "JPN",
            "belgium": "BEL", "belgio": "BEL", "ukraine": "UKR", "ucraina": "UKR", "czech republic": "CZE",
            "repubblica ceca": "CZE", "poland": "POL", "polonia": "POL", "brazil": "BRA", "brasile": "BRA",
            "switzerland": "SUI", "svizzera": "SUI", "serbia": "SRB", "croatia": "CRO", "croazia": "CRO",
            "netherlands": "NED", "olanda": "NED", "austria": "AUT", "sweden": "SWE", "svezia": "SWE",
            "kazakhstan": "KAZ", "kazakistan": "KAZ", "slovakia": "SVK", "chile": "CHI", "colombia": "COL"
        }

        c1_clean = self.clean_player_name(c1_name)
        c2_clean = self.clean_player_name(c2_name)
        code1 = code_map.get(c1_clean.lower(), c1_clean[:3].upper())
        code2 = code_map.get(c2_clean.lower(), c2_clean[:3].upper())

        # Country 1 (Left Shield)
        draw.rounded_rectangle([(120, cy - 110), (480, cy + 100)], radius=18, fill=(10, 18, 35, 225), outline=(255, 255, 255, 100), width=2)
        font_code = self._get_font(58)
        draw.text((300, cy - 35), code1, font=font_code, fill=(255, 255, 255), anchor="mm")
        font_c = self._get_font(24)
        draw.text((300, cy + 28), c1_clean.upper(), font=font_c, fill=(255, 215, 0), anchor="mm")
        font_nat = self._get_font(15)
        draw.text((300, cy + 65), "NATIONAL TEAM", font=font_nat, fill=(147, 197, 253, 190), anchor="mm")

        # Country 2 (Right Shield)
        draw.rounded_rectangle([(W - 480, cy - 110), (W - 120, cy + 100)], radius=18, fill=(10, 18, 35, 225), outline=(255, 255, 255, 100), width=2)
        draw.text((W - 300, cy - 35), code2, font=font_code, fill=(255, 255, 255), anchor="mm")
        draw.text((W - 300, cy + 28), c2_clean.upper(), font=font_c, fill=(255, 215, 0), anchor="mm")
        draw.text((W - 300, cy + 65), "NATIONAL TEAM", font=font_nat, fill=(147, 197, 253, 190), anchor="mm")

        return canvas

    async def generate_poster(self, match_id: str, title: str, genre: str) -> Optional[str]:
        """
        Creates and saves an 888x500 landscape poster for a tennis match.
        Returns the relative web path (e.g. '/posters/tennis_xyz.jpg').
        """
        if not HAS_PIL:
            return None

        clean_key = hashlib.md5(match_id.encode("utf-8")).hexdigest()[:12]
        filename = f"tennis_{clean_key}_v3.jpg"
        target_path = POSTERS_DIR / filename

        if target_path.exists():
            return f"/posters/{filename}"

        theme = self.get_theme_palette(genre, title)
        tourn_name, stage_name = self.extract_tournament_and_stage(title, genre)

        # Base Arena
        canvas = self._render_cinematic_arena(theme, tourn_name)

        # 1. Check if title is a Bracket Stage card (e.g. '(Couples) 1/4 Final 1 (WTA 500 Singapore)')
        stage_info = self.is_stage_placeholder(title)
        if stage_info:
            canvas = self._render_stage_card(canvas, stage_info["stage"], stage_info["tournament"])
        else:
            p1_name, p2_name = self.parse_players(title)
            if not p1_name:
                return None

            # 2. Check if Country match (Davis Cup / BJK Cup / 'Italy vs China')
            if self.is_country_match(title, p1_name, p2_name, genre):
                canvas = self._render_country_match(canvas, p1_name, p2_name, tourn_name)
            else:
                # 3. Standard Player vs Player Match
                canvas = await self._render_standard_match(canvas, p1_name, p2_name)

        # Save to disk as high-quality JPEG
        final_rgb = canvas.convert("RGB")
        final_rgb.save(target_path, "JPEG", quality=90, optimize=True)
        logger.info("Generated tennis poster: %s (%s)", filename, title)

        return f"/posters/{filename}"

    def start_background_enrichment(self, matches: List[Dict[str, Any]]):
        """Asynchronously triggers poster generation for tennis matches lacking artwork."""
        if not HAS_PIL:
            logger.warning("Pillow (PIL) is not installed. Dynamic tennis poster generation is disabled.")
            return

        tennis_matches = [
            m for m in matches
            if (m.get("category") == "tennis" or m.get("_catalog") == "tennis")
            and (not m.get("poster") or not str(m.get("poster")).endswith("_v3.jpg"))
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
                genre = m.get("_genre") or "ATP"
                try:
                    rel_poster = await self.generate_poster(m_id, title, genre)
                    if rel_poster:
                        m["poster"] = rel_poster
                        db_service.save_matches([m])
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
                active_poster_filenames.add(f"tennis_{clean_key}_v3.jpg")

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
                    if f.name.lower() == "unknown.png":
                        continue
                    if f.stem not in active_slugs:
                        try:
                            f.unlink()
                            purged_athletes += 1
                        except Exception:
                            pass

        # 2. Clean POSTERS_DIR: remove stale tennis posters not in active catalog
        purged_posters = 0
        for f in POSTERS_DIR.glob("tennis_*_v3.jpg"):
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
