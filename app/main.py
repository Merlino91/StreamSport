import base64
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import re
from typing import Optional, Tuple
import urllib.parse
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import (
    ADDON_DESCRIPTION,
    ADDON_ID,
    ADDON_NAME,
    ADDON_VERSION,
    CATALOG_DEFINITIONS,
    CATALOG_ID,
    CATALOG_NAME,
    CATALOG_TYPE,
    ID_PREFIXES,
    SPORT_GENRES,
    STREAMED_API_HOST,
)
from app.services.catalog_service import catalog_service
from app.services.dailymotion_service import dailymotion_service
from app.services.doh_client import doh_client
from app.services.stream_service import stream_service
from app.services.youtube_service import youtube_service

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("streamsport")

# Lifespan context manager for graceful shutdown and background tasks
@asynccontextmanager
async def lifespan(app: FastAPI):
    catalog_service.start_background_sync()
    yield
    catalog_service.stop_background_sync()
    await doh_client.close()

# Directories
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="StreamSport Stremio Addon", version=ADDON_VERSION, lifespan=lifespan)

# Enable CORS for all origins (required by Stremio)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static and Templates
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

POSTERS_DIR = BASE_DIR.parent / "data" / "posters"
POSTERS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/posters", StaticFiles(directory=str(POSTERS_DIR)), name="posters")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def decode_config(config_str: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Decodes the Base64 config path parameter.
    Format: 'epUrl|epPass|tz' (or JSON fallback).
    Returns (ep_url, ep_pass, tz).
    """
    if not config_str:
        return None, None, "Europe/Rome"

    try:
        b64 = config_str.replace("-", "+").replace("_", "/")
        b64 += "=" * ((4 - len(b64) % 4) % 4)
        raw = base64.b64decode(b64).decode("utf-8")

        if "|" in raw:
            parts = raw.split("|")
            ep_url = parts[0].strip() if len(parts) > 0 else None
            ep_pass = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
            tz = parts[2].strip() if len(parts) > 2 and parts[2].strip() else "Europe/Rome"
            return ep_url, ep_pass, tz

        data = json.loads(raw)
        return data.get("epUrl"), data.get("epPass"), data.get("tz", "Europe/Rome")
    except Exception as e:
        logger.debug("Failed decoding config '%s': %s", config_str, e)
        return None, None, "Europe/Rome"


def get_base_url(request: Request) -> str:
    """
    Extracts the correct public base URL respecting reverse proxy headers.
    """
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def build_manifest(configured: bool = True) -> dict:
    """Builds the official Stremio Addon manifest with dedicated thematic sports catalogs."""
    catalogs = []
    for cat in CATALOG_DEFINITIONS:
        catalogs.append({
            "id": cat["id"],
            "type": CATALOG_TYPE,
            "name": cat["name"],
            "extra": [
                {
                    "name": "genre",
                    "options": cat["genres"],
                    "isRequired": False,
                },
                {
                    "name": "search",
                    "isRequired": False,
                },
                {
                    "name": "skip",
                    "isRequired": False,
                },
            ],
        })

    return {
        "id": ADDON_ID,
        "version": ADDON_VERSION,
        "name": ADDON_NAME,
        "description": ADDON_DESCRIPTION,
        "types": [CATALOG_TYPE],
        "resources": ["catalog", "meta", "stream"],
        "idPrefixes": ID_PREFIXES,
        "catalogs": catalogs,
        "behaviorHints": {
            "configurable": False,
            "configurationRequired": not configured,
        },
    }


def extract_extra_params(request: Request, extra: Optional[str] = None) -> Tuple[Optional[str], Optional[str], int]:
    """
    Extracts (genre, search, skip) from query parameters or extra path segments.
    Resilient to raw ampersands inside genre names.
    """
    genre = None
    search = None
    skip = 0

    # 1. Query parameters
    if hasattr(request, "query_params"):
        if "genre" in request.query_params:
            genre = request.query_params.get("genre")
        if "search" in request.query_params:
            search = request.query_params.get("search")
        if "skip" in request.query_params:
            try:
                skip = int(request.query_params.get("skip", 0))
            except ValueError:
                skip = 0

    # 2. Path segments
    if extra:
        # Extract skip
        m_skip = re.search(r"\bskip=(\d+)", extra)
        if m_skip:
            try:
                skip = int(m_skip.group(1))
            except ValueError:
                skip = 0

        # Extract search
        m_search = re.search(r"\bsearch=([^&]+)", extra)
        if m_search:
            search = urllib.parse.unquote(m_search.group(1)).strip()

        # Extract genre: capture up to the next &skip= or &search= or end-of-string
        m_genre = re.search(r"\bgenre=(.*?)(?:&(?:skip|search)=|$)", extra)
        if m_genre:
            genre = urllib.parse.unquote(m_genre.group(1)).strip()
        else:
            qs = urllib.parse.parse_qs(extra)
            if "genre" in qs and qs["genre"]:
                genre = qs["genre"][0].strip()

    return genre, search, skip


# ==========================================
# Web Configuration Routes
# ==========================================

@app.get("/", response_class=HTMLResponse)
@app.get("/configure", response_class=HTMLResponse)
async def configure_page(
    request: Request,
    epUrl: Optional[str] = None,
    epPass: Optional[str] = None,
    tz: Optional[str] = None,
    save: Optional[str] = None,
):
    """Renders the configuration Web UI."""
    install_url = None
    stremio_url = None

    if save and epUrl:
        ep_pass_clean = epPass or ""
        tz_clean = tz or "Europe/Rome"
        raw_config = f"{epUrl.strip()}|{ep_pass_clean.strip()}|{tz_clean.strip()}"
        encoded_config = base64.b64encode(raw_config.encode("utf-8")).decode("utf-8")

        base_url = get_base_url(request)
        install_url = f"{base_url}/{encoded_config}/manifest.json"
        stremio_url = install_url.replace("http://", "stremio://").replace("https://", "stremio://")

    context = {
        "request": request,
        "genres": SPORT_GENRES,
        "epUrl": epUrl or "",
        "epPass": epPass or "",
        "tz": tz or "Europe/Rome",
        "install_url": install_url,
        "stremio_url": stremio_url,
        "configured": bool(install_url),
    }

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=context,
    )


# ==========================================
# Stremio Protocol Routes
# ==========================================

@app.get("/manifest.json")
async def unconfigured_manifest():
    return JSONResponse(content=build_manifest(configured=False))


@app.get("/{config}/manifest.json")
async def configured_manifest(config: str):
    ep_url, _, _ = decode_config(config)
    is_configured = bool(ep_url)
    return JSONResponse(content=build_manifest(configured=is_configured))


@app.get("/catalog/{type}/{id}.json")
@app.get("/catalog/{type}/{id}/{extra}.json")
async def unconfigured_catalog(
    request: Request,
    type: str,
    id: str,
    extra: Optional[str] = None,
):
    base_url = get_base_url(request)
    genre, search, skip = extract_extra_params(request, extra)
    metas = await catalog_service.get_catalog(
        catalog_id=id,
        genre_filter=genre,
        search_query=search,
        skip=skip,
        user_tz="Europe/Rome",
        base_url=base_url,
    )
    return JSONResponse(content={"metas": metas})


@app.get("/{config}/catalog/{type}/{id}.json")
@app.get("/{config}/catalog/{type}/{id}/{extra}.json")
async def configured_catalog(
    request: Request,
    config: str,
    type: str,
    id: str,
    extra: Optional[str] = None,
):
    base_url = get_base_url(request)
    ep_url, ep_pass, tz = decode_config(config)
    genre, search, skip = extract_extra_params(request, extra)
    metas = await catalog_service.get_catalog(
        catalog_id=id,
        genre_filter=genre,
        search_query=search,
        skip=skip,
        user_tz=tz,
        base_url=base_url,
    )
    return JSONResponse(content={"metas": metas})


@app.get("/meta/{type}/{id}.json")
async def unconfigured_meta(request: Request, type: str, id: str):
    base_url = get_base_url(request)
    meta = await catalog_service.get_meta_detail(id, user_tz="Europe/Rome", base_url=base_url)
    if not meta:
        raise HTTPException(status_code=404, detail="Meta not found")
    return JSONResponse(content={"meta": meta})


@app.get("/{config}/meta/{type}/{id}.json")
async def configured_meta(request: Request, config: str, type: str, id: str):
    base_url = get_base_url(request)
    _, _, tz = decode_config(config)
    meta = await catalog_service.get_meta_detail(id, user_tz=tz, base_url=base_url)
    if not meta:
        raise HTTPException(status_code=404, detail="Meta not found")
    return JSONResponse(content={"meta": meta})


@app.get("/stream/{type}/{id}.json")
async def unconfigured_stream(request: Request, type: str, id: str):
    base_url = get_base_url(request)
    streams = await stream_service.get_streams_for_event(id, ep_url=None, user_tz="UTC", base_url=base_url)
    return JSONResponse(content={"streams": streams})


@app.get("/{config}/stream/{type}/{id}.json")
async def configured_stream(request: Request, config: str, type: str, id: str):
    base_url = get_base_url(request)
    ep_url, ep_pass, tz = decode_config(config)
    streams = await stream_service.get_streams_for_event(
        id,
        ep_url=ep_url,
        ep_pass=ep_pass,
        user_tz=tz,
        base_url=base_url,
    )
    return JSONResponse(content={"streams": streams})


@app.get("/dailymotion/stream/{video_id}.m3u8")
async def dailymotion_stream_proxy(video_id: str):
    """
    On-demand resolver and redirect for Dailymotion video highlights.
    Extracts direct HLS master .m3u8 stream via yt-dlp with TLS impersonation.
    """
    stream_url = await dailymotion_service.resolve_stream_url(video_id)
    if stream_url:
        return RedirectResponse(url=stream_url, status_code=307)

    logger.warning("Dailymotion stream extraction failed for %s", video_id)
    raise HTTPException(
        status_code=502,
        detail="Impossibile estrarre lo streaming da Dailymotion. Usa la voce YouTube per questo evento.",
    )


@app.get("/youtube/stream/{video_id}.m3u8")
async def youtube_stream_proxy(video_id: str):
    """
    On-demand master HLS playlist resolver combining video and audio for YouTube highlights.
    Serves the master .m3u8 manifest directly linking the video and audio renditions.
    """
    if not re.match(r"^[a-zA-Z0-9_-]{11}$", video_id):
        raise HTTPException(status_code=400, detail="ID video YouTube non valido")

    content, redirect_url = await youtube_service.resolve_stream_manifest(video_id)
    if content:
        return Response(
            content=content,
            media_type="application/vnd.apple.mpegurl",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=3600",
            },
        )
    if redirect_url:
        return RedirectResponse(url=redirect_url, status_code=307)

    logger.warning("YouTube stream extraction failed for %s", video_id)
    raise HTTPException(
        status_code=502,
        detail="Impossibile estrarre lo streaming da YouTube. Riprova più tardi.",
    )


@app.get("/image-proxy")
async def image_proxy(url: str):
    """Proxies upstream images through DoH to prevent ISP blockades."""
    if not url:
        raise HTTPException(status_code=400, detail="Missing url parameter")

    content, content_type = await doh_client.proxy_image(url)
    if not content:
        raise HTTPException(status_code=502, detail="Failed to proxy image")

    return Response(content=content, media_type=content_type, headers={"Cache-Control": "public, max-age=7200"})


@app.get("/health")
async def health():
    return {"status": "ok", "addon": "StreamSport"}


if __name__ == "__main__":
    import uvicorn
    from app.config import HOST, PORT
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
