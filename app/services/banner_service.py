from __future__ import annotations
import asyncio
import datetime
import io
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, Optional, Set, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None
    ImageDraw = None
    ImageFont = None

from app.config import PROJECT_DIR
from app.services.doh_client import doh_client

logger = logging.getLogger("streamsport.banner")

CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720


class BannerService:
    """
    Renders dynamic 16:9 posters with status badges (time / LIVE / replay)
    and optional TvVoo bookmark ribbons.
    Falls back to a sleek dark background canvas for matches lacking official posters.
    Caches all generated JPEGs on disk for ultra-low latency and zero CPU re-renders.
    """

    def __init__(self):
        self.data_dir = PROJECT_DIR / "data"
        self.banners_dir = self.data_dir / "posters" / "banners"
        self.banners_dir.mkdir(parents=True, exist_ok=True)

        self._font_bold_path = self._find_font(bold=True)
        self._font_regular_path = self._find_font(bold=False)

    def _find_font(self, bold: bool = True) -> Optional[str]:
        """Locates a clean sans-serif font across Windows and Linux environments."""
        if bold:
            candidates = [
                # Windows
                os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "segoeuib.ttf"),
                os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arialbd.ttf"),
                # Linux / Docker
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            ]
        else:
            candidates = [
                # Windows
                os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "segoeui.ttf"),
                os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf"),
                # Linux / Docker
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return None

    def _get_font(self, size: int, bold: bool = True) -> ImageFont.ImageFont:
        path = self._font_bold_path if bold else self._font_regular_path
        if path:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    @staticmethod
    def _sanitize_id(raw_id: str) -> str:
        s = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw_id or "unknown")
        return s[:64]

    def get_cache_path(self, match_id: str, status: str, time_str: str, has_tvvoo: bool) -> Path:
        sanitized_id = self._sanitize_id(match_id)
        clean_time = re.sub(r"[^a-zA-Z0-9]+", "", time_str or "")
        tv_flag = "1" if has_tvvoo else "0"
        filename = f"{sanitized_id}_{status}_{clean_time}_{tv_flag}.jpg"
        return self.banners_dir / filename

    async def get_or_create_poster(
        self,
        match: Optional[Dict[str, Any]],
        match_id: str,
        status: str = "upcoming",
        time_str: str = "",
        has_tvvoo: bool = False,
    ) -> Optional[bytes]:
        """
        Retrieves cached banner poster bytes from disk if present,
        or generates, caches, and returns them asynchronously.
        """
        if not HAS_PIL:
            return None

        cache_file = self.get_cache_path(match_id, status, time_str, has_tvvoo)
        if cache_file.exists() and cache_file.stat().st_size > 1000:
            try:
                return cache_file.read_bytes()
            except Exception as e:
                logger.debug("Error reading banner cache %s: %s", cache_file, e)

        # Generate poster
        poster_bytes = await self._generate_poster(match, status, time_str, has_tvvoo)
        if poster_bytes:
            try:
                cache_file.write_bytes(poster_bytes)
            except Exception as e:
                logger.warning("Failed to save banner cache to %s: %s", cache_file, e)
            return poster_bytes

        return None

    async def _fetch_base_image(self, url: Optional[str]) -> Optional[Image.Image]:
        if not url:
            return None

        # Check local files first
        if url.startswith("/posters/") or url.startswith("posters/"):
            local_rel = url.lstrip("/")
            local_p = self.data_dir / local_rel
            if local_p.exists():
                try:
                    return Image.open(local_p).convert("RGB")
                except Exception:
                    pass

        # Remote image via DoH proxy
        if url.startswith("http://") or url.startswith("https://"):
            try:
                content, _ = await doh_client.proxy_image(url)
                if content:
                    return Image.open(io.BytesIO(content)).convert("RGB")
            except Exception as e:
                logger.debug("Failed to fetch base poster %s: %s", url, e)

        return None

    async def _generate_poster(
        self,
        match: Optional[Dict[str, Any]],
        status: str,
        time_str: str,
        has_tvvoo: bool,
    ) -> Optional[bytes]:
        try:
            m = match or {}
            base_url = m.get("poster")
            base_im = await self._fetch_base_image(base_url)

            if base_im:
                # Resize and center-crop to 1280x720
                im = self._fit_cover(base_im, CANVAS_WIDTH, CANVAS_HEIGHT)
                # Apply top shadow gradient so badges stand out with 100% contrast
                self._apply_top_gradient(im)
            else:
                # Sleek dark background canvas
                im = self._create_dark_canvas(m)

            draw = ImageDraw.Draw(im)

            # 1. Top-Left: Status Badge (LIVE / Upcoming Time / Replay)
            self._draw_status_badge(draw, status, time_str)

            # 2. Top-Right: TvVoo Bookmark Ribbon (Lightning bolt only)
            if has_tvvoo:
                self._draw_tvvoo_bookmark(draw)

            # Encode as progressive JPEG with quality=88
            out_buf = io.BytesIO()
            im.save(out_buf, format="JPEG", quality=88, optimize=True)
            return out_buf.getvalue()

        except Exception as e:
            logger.error("Error generating banner poster for match: %s", e, exc_info=True)
            return None

    def _create_dark_canvas(self, match: Dict[str, Any]) -> Image.Image:
        """Creates an elegant dark canvas with centered match title and competition subtitle."""
        im = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), (12, 14, 18))
        draw = ImageDraw.Draw(im)

        title = (match.get("title") or "Live Sports").strip()
        comp = (match.get("competition") or match.get("_competition") or "").strip().upper()

        # Clean title from redundant prefixes
        clean_title = re.sub(r"^[0-9:\s-]+(?:\s*:\s*)?", "", title)
        clean_title = re.sub(r"^[A-Za-z0-9\s-]+:\s*", "", clean_title)

        # Dynamic font sizing for long titles
        font_size = 52 if len(clean_title) < 40 else 42
        if len(clean_title) > 60:
            font_size = 36

        f_title = self._get_font(font_size, bold=True)
        f_comp = self._get_font(28, bold=False)

        # Center Title
        bbox = draw.textbbox((0, 0), clean_title, font=f_title)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((CANVAS_WIDTH - tw) // 2, (CANVAS_HEIGHT - th) // 2 - 20), clean_title, font=f_title, fill=(245, 247, 250))

        # Center Competition
        if comp:
            bbox_c = draw.textbbox((0, 0), comp, font=f_comp)
            cw = bbox_c[2] - bbox_c[0]
            draw.text(((CANVAS_WIDTH - cw) // 2, (CANVAS_HEIGHT - th) // 2 + th + 18), comp, font=f_comp, fill=(138, 148, 162))

        return im

    @staticmethod
    def _fit_cover(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
        """Resizes and center-crops an image to exact target dimensions maintaining aspect ratio."""
        orig_w, orig_h = img.size
        scale = max(target_w / orig_w, target_h / orig_h)
        new_w = int(round(orig_w * scale))
        new_h = int(round(orig_h * scale))
        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))

    @staticmethod
    def _apply_top_gradient(img: Image.Image):
        """Applies a smooth top vignette gradient to guarantee badge legibility."""
        grad_height = 190
        grad = Image.new("RGBA", (CANVAS_WIDTH, grad_height), (0, 0, 0, 0))
        g_draw = ImageDraw.Draw(grad)
        for y in range(grad_height):
            alpha = int(220 * (1.0 - (y / grad_height) ** 1.3))
            g_draw.line([(0, y), (CANVAS_WIDTH, y)], fill=(0, 0, 0, alpha))
        img.paste(grad, (0, 0), grad)

    def _draw_status_badge(self, draw: ImageDraw.ImageDraw, status: str, time_str: str):
        """Draws the top-left pill badge (LIVE, Upcoming Time, or Replay) with enlarged legibility."""
        bx, by = 36, 26
        bh = 54
        f_badge = self._get_font(32, bold=True)

        if status == "live":
            txt = "LIVE"
            t_bbox = draw.textbbox((0, 0), txt, font=f_badge)
            tw = t_bbox[2] - t_bbox[0]
            th = t_bbox[3] - t_bbox[1]
            bw = tw + 72
            # Vibrant crimson red pill
            draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=12, fill=(229, 9, 20))
            # Center white dot
            cx, cy = bx + 22, by + bh // 2
            draw.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), fill=(255, 255, 255))
            draw.text((bx + 38, by + (bh - th) // 2 - 2), txt, font=f_badge, fill=(255, 255, 255))

        elif status == "replay":
            txt = "REPLAY"
            t_bbox = draw.textbbox((0, 0), txt, font=f_badge)
            tw = t_bbox[2] - t_bbox[0]
            th = t_bbox[3] - t_bbox[1]
            bw = tw + 44
            # Slate pill
            draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=12, fill=(35, 42, 54), outline=(80, 92, 110), width=2)
            draw.text((bx + 22, by + (bh - th) // 2 - 2), txt, font=f_badge, fill=(240, 244, 248))

        else:
            # Upcoming
            raw_time = time_str.strip() if time_str else "OGGI"
            display_txt = raw_time
            t_bbox = draw.textbbox((0, 0), display_txt, font=f_badge)
            tw = t_bbox[2] - t_bbox[0]
            th = t_bbox[3] - t_bbox[1]
            bw = tw + 72
            # Dark glass translucent pill
            draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=12, fill=(18, 22, 30), outline=(90, 102, 120), width=2)
            # Clock circle icon
            cx, cy = bx + 24, by + bh // 2
            clock_r = 10
            draw.ellipse((cx - clock_r, cy - clock_r, cx + clock_r, cy + clock_r), outline=(220, 230, 242), width=3)
            draw.line((cx, cy, cx, cy - 6), fill=(220, 230, 242), width=3)
            draw.line((cx, cy, cx + 5, cy), fill=(220, 230, 242), width=3)
            draw.text((bx + 44, by + (bh - th) // 2 - 2), display_txt, font=f_badge, fill=(245, 247, 250))

    @staticmethod
    def _draw_tvvoo_bookmark(draw: ImageDraw.ImageDraw):
        """Draws the bold golden bolt bookmark ribbon pinned to the top-right corner."""
        rw, rh = 78, 106
        rx = CANVAS_WIDTH - 36 - rw
        ry = 0

        # Ribbon polygon with classic swallowtail V-notch
        ribbon_pts = [
            (rx, ry),
            (rx + rw, ry),
            (rx + rw, ry + rh),
            (rx + rw // 2, ry + rh - 20),
            (rx, ry + rh),
        ]
        draw.polygon(ribbon_pts, fill=(15, 18, 24), outline=(255, 204, 0), width=3)

        # Bold golden lightning bolt polygon
        bolt_pts = [
            (rx + 39, ry + 16),
            (rx + 56, ry + 16),
            (rx + 34, ry + 48),
            (rx + 48, ry + 48),
            (rx + 22, ry + 82),
            (rx + 36, ry + 52),
            (rx + 22, ry + 52),
        ]
        draw.polygon(bolt_pts, fill=(255, 204, 0))

    def purge_stale_banners(self, max_age_hours: int = 24) -> int:
        """Deletes generated banner files older than max_age_hours to preserve disk space."""
        cutoff_sec = time.time() - (max_age_hours * 3600)
        deleted = 0
        try:
            for p in self.banners_dir.glob("*.jpg"):
                if p.is_file() and p.stat().st_mtime < cutoff_sec:
                    p.unlink(missing_ok=True)
                    deleted += 1
            if deleted > 0:
                logger.info("Purged %d stale banner cache images older than %d hours.", deleted, max_age_hours)
        except Exception as e:
            logger.warning("Error during banner cache purge: %s", e)
        return deleted


banner_service = BannerService()
