import base64
from contextlib import asynccontextmanager
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import sys
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
    HOST,
    ID_PREFIXES,
    NUVIO_CATALOG_DEFINITIONS,
    NUVIO_CATALOG_LOOKUP,
    PORT,
    POSTERS_DIR,
    PUBLIC_URL,
    SPORT_GENRES,
    STREAMED_API_HOST,
)
from app.services.banner_service import banner_service
from app.services.catalog_service import catalog_service
from app.services.dailymotion_service import dailymotion_service
from app.services.db_service import db_service
from app.services.doh_client import doh_client
from app.services.mandrakodi_service import mandrakodi_service
from app.services.sportvideo_service import sportvideo_service
from app.services.stream_service import stream_service
from app.services.tvvoo_service import is_safe_vavoo_play_url, sanitize_ip, tvvoo_service
from app.services.youtube_service import youtube_service

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("streamsport")

# Lifespan context manager for graceful shutdown and background tasks
@asynccontextmanager
async def lifespan(app: FastAPI):
    is_testing = "unittest" in sys.modules or os.environ.get("STREAMSPORT_TESTING") == "1"
    if not is_testing:
        catalog_service.start_background_sync()
    yield
    if not is_testing:
        catalog_service.stop_background_sync()
    await tvvoo_service.manifest_manager.close()
    await doh_client.close()
    await mandrakodi_service.close()
    await sportvideo_service.close()
    db_service.close()

# Directories
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="StreamSport Stremio Addon", version=ADDON_VERSION, lifespan=lifespan)

# Enable CORS for all origins (required by Stremio)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static and Templates
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

POSTERS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/posters", StaticFiles(directory=str(POSTERS_DIR)), name="posters")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def decode_config(config_str: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    """
    Decodes the Base64 config path parameter.
    Format: 'epUrl|epPass|tz|client' (or JSON fallback).
    Returns (ep_url, ep_pass, tz, client).
    """
    if not config_str:
        return None, None, "Europe/Rome", "stremio"

    try:
        b64 = config_str.replace("-", "+").replace("_", "/")
        b64 += "=" * ((4 - len(b64) % 4) % 4)
        raw = base64.b64decode(b64).decode("utf-8")

        if "|" in raw:
            parts = raw.split("|")
            ep_url = parts[0].strip() if len(parts) > 0 and parts[0].strip() else None
            ep_pass = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
            tz = parts[2].strip() if len(parts) > 2 and parts[2].strip() else "Europe/Rome"
            client = parts[3].strip().lower() if len(parts) > 3 and parts[3].strip() else "stremio"
            return ep_url, ep_pass, tz, client

        data = json.loads(raw)
        return (
            data.get("epUrl"),
            data.get("epPass"),
            data.get("tz", "Europe/Rome"),
            (data.get("client") or "stremio").strip().lower(),
        )
    except Exception as e:
        logger.debug("Failed decoding config '%s': %s", config_str, e)
        return None, None, "Europe/Rome", "stremio"


_HOST_REGEX = re.compile(r"^[a-zA-Z0-9.\-_]+(?::\d{1,5})?$")


def get_base_url(request: Request) -> str:
    """
    Extracts the correct public base URL respecting reverse proxy headers,
    with PUBLIC_URL priority and anti-host-spoofing validation.
    """
    if PUBLIC_URL:
        return PUBLIC_URL

    # 1. Scheme validation (strictly http or https)
    raw_proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").strip().lower()
    proto = "https" if raw_proto == "https" else "http"

    # 2. Host validation and sanitization
    raw_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc or ""
    candidate_host = raw_host.split(",")[0].strip()

    if candidate_host and _HOST_REGEX.match(candidate_host):
        host = candidate_host
    else:
        host = request.url.netloc or f"{HOST}:{PORT}"

    return f"{proto}://{host}"


def extract_clean_client_ip(request: Request) -> Optional[str]:
    """
    Extracts and formally validates client IP address.
    Prioritizes Cloudflare's CF-Connecting-IP, then X-Forwarded-For, then direct client host.
    Ensures candidate is a valid public IPv4 or IPv6 address to prevent header spoofing,
    upstream injection, or forwarding private LAN/Docker IPs to upstream streaming providers.
    """
    raw_ip = (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for")
        or (request.client.host if request.client else None)
    )
    if not raw_ip:
        return None

    return sanitize_ip(raw_ip)


def build_manifest(configured: bool = True, client: str = "stremio") -> dict:
    """Builds the official Stremio/Nuvio Addon manifest with dedicated thematic sports catalogs."""
    is_nuvio = (client or "").strip().lower() == "nuvio"
    defs = NUVIO_CATALOG_DEFINITIONS if is_nuvio else CATALOG_DEFINITIONS

    catalogs = []
    for cat in defs:
        extra_list = [
            {
                "name": "search",
                "isRequired": False,
            },
            {
                "name": "skip",
                "isRequired": False,
            },
        ]
        # Only add genre filter for Stremio, as Nuvio uses flat standalone catalogs
        if not is_nuvio and cat.get("genres"):
            extra_list.insert(0, {
                "name": "genre",
                "options": cat["genres"],
                "isRequired": False,
            })

        catalogs.append({
            "id": cat["id"],
            "type": CATALOG_TYPE,
            "name": cat["name"],
            "extra": extra_list,
        })

    addon_name = f"{ADDON_NAME} (Nuvio)" if is_nuvio else ADDON_NAME
    addon_id = f"{ADDON_ID}.nuvio" if is_nuvio else ADDON_ID

    return {
        "id": addon_id,
        "version": ADDON_VERSION,
        "name": addon_name,
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
    client: Optional[str] = "stremio",
    save: Optional[str] = None,
):
    """Renders the configuration Web UI."""
    install_url = None
    stremio_url = None

    if save and epUrl:
        ep_pass_clean = epPass or ""
        tz_clean = tz or "Europe/Rome"
        client_clean = (client or "stremio").strip().lower()
        if client_clean == "nuvio":
            raw_config = f"{epUrl.strip()}|{ep_pass_clean.strip()}|{tz_clean.strip()}|nuvio"
        else:
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
        "client": (client or "stremio").strip().lower(),
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
    return JSONResponse(content=build_manifest(configured=False, client="stremio"))


@app.get("/nuvio/manifest.json")
async def unconfigured_nuvio_manifest():
    return JSONResponse(content=build_manifest(configured=False, client="nuvio"))


@app.get("/{config}/manifest.json")
async def configured_manifest(config: str):
    ep_url, _, _, client = decode_config(config)
    is_configured = bool(ep_url)
    return JSONResponse(content=build_manifest(configured=is_configured, client=client))


@app.get("/{config}/nuvio/manifest.json")
async def configured_nuvio_manifest(config: str):
    ep_url, _, _, _ = decode_config(config)
    is_configured = bool(ep_url)
    return JSONResponse(content=build_manifest(configured=is_configured, client="nuvio"))


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
    actual_catalog = id
    actual_genre = genre
    is_nuvio = False
    if id in NUVIO_CATALOG_LOOKUP:
        actual_catalog, actual_genre = NUVIO_CATALOG_LOOKUP[id]
        is_nuvio = True

    metas = await catalog_service.get_catalog(
        catalog_id=actual_catalog,
        genre_filter=actual_genre,
        search_query=search,
        skip=skip,
        user_tz="Europe/Rome",
        base_url=base_url,
        is_nuvio=is_nuvio,
    )
    return JSONResponse(
        content={"metas": metas},
        headers={"Cache-Control": "public, max-age=180"},
    )


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
    ep_url, ep_pass, tz, client = decode_config(config)
    genre, search, skip = extract_extra_params(request, extra)

    actual_catalog = id
    actual_genre = genre
    is_nuvio = (client == "nuvio")
    if id in NUVIO_CATALOG_LOOKUP:
        actual_catalog, actual_genre = NUVIO_CATALOG_LOOKUP[id]
        is_nuvio = True

    metas = await catalog_service.get_catalog(
        catalog_id=actual_catalog,
        genre_filter=actual_genre,
        search_query=search,
        skip=skip,
        user_tz=tz,
        base_url=base_url,
        is_nuvio=is_nuvio,
    )
    return JSONResponse(
        content={"metas": metas},
        headers={"Cache-Control": "public, max-age=180"},
    )


@app.get("/meta/{type}/{id}.json")
async def unconfigured_meta(request: Request, type: str, id: str):
    base_url = get_base_url(request)
    meta = await catalog_service.get_meta_detail(id, user_tz="Europe/Rome", base_url=base_url)
    if not meta:
        raise HTTPException(status_code=404, detail="Meta not found")
    return JSONResponse(
        content={"meta": meta},
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/{config}/meta/{type}/{id}.json")
async def configured_meta(request: Request, config: str, type: str, id: str):
    base_url = get_base_url(request)
    _, _, tz, _ = decode_config(config)
    meta = await catalog_service.get_meta_detail(id, user_tz=tz, base_url=base_url)
    if not meta:
        raise HTTPException(status_code=404, detail="Meta not found")
    return JSONResponse(
        content={"meta": meta},
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/stream/{type}/{id}.json")
async def unconfigured_stream(request: Request, type: str, id: str):
    base_url = get_base_url(request)
    try:
        streams = await stream_service.get_streams_for_event(id, ep_url=None, user_tz="UTC", base_url=base_url)
    except Exception as e:
        logger.error("Error generating streams for %s: %s", id, e, exc_info=True)
        streams = []
    return JSONResponse(content={"streams": streams})


@app.get("/{config}/stream/{type}/{id}.json")
async def configured_stream(request: Request, config: str, type: str, id: str):
    base_url = get_base_url(request)
    ep_url, ep_pass, tz, _ = decode_config(config)
    try:
        streams = await stream_service.get_streams_for_event(
            id,
            ep_url=ep_url,
            ep_pass=ep_pass,
            user_tz=tz,
            base_url=base_url,
            config=config,
        )
    except Exception as e:
        logger.error("Error generating streams for %s: %s", id, e, exc_info=True)
        streams = []
    return JSONResponse(content={"streams": streams})


@app.get("/live/manifest.m3u8")
@app.get("/{config}/live/manifest.m3u8")
async def live_manifest_proxy(request: Request, url: str, config: Optional[str] = None):
    """
    Live M3U8 Manifest Proxy Endpoint (Auto token renewal, 0% video bandwidth).
    Serves rewritten playlists pointing directly to upstream CDN chunks,
    proactively renewing tokens in background to prevent stream freezing.
    Automatically redirects to EasyProxy (HTTP 307) if upstream native resolution fails.
    """
    if not url:
        raise HTTPException(status_code=400, detail="Parametro url mancante")

    if not is_safe_vavoo_play_url(url):
        raise HTTPException(status_code=400, detail="URL del flusso non valida o non consentita (anti-SSRF)")

    client_ip = extract_clean_client_ip(request)

    manifest_content = None
    try:
        manifest_content = await tvvoo_service.get_live_manifest(url, client_ip)
    except Exception as exc:
        logger.warning("[LiveManifest] Upstream exception for %s: %s", url[:80], exc)

    if not manifest_content:
        # Automatic Fallback to EasyProxy if configured
        ep_url, ep_pass = None, None
        if config:
            ep_url, ep_pass, _, _ = decode_config(config)

        if ep_url:
            easyproxy_url = stream_service.build_easyproxy_url(ep_url, ep_pass, "Vavoo", url)
            logger.info(
                "[LiveManifest] Upstream resolution failed for %s. Seamlessly redirecting player to EasyProxy fallback: %s",
                url[:60], easyproxy_url[:80]
            )
            return RedirectResponse(url=easyproxy_url, status_code=307)

        raise HTTPException(status_code=502, detail="Impossibile generare il manifest live dello stream")

    return Response(
        content=manifest_content,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


def is_trusted_stream_redirect(url: str, allowed_suffixes: Tuple[str, ...]) -> bool:
    """Validates that a resolved stream redirect points strictly to trusted CDN domains."""
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        host = (parsed.hostname or "").lower()
        return any(host == domain or host.endswith("." + domain) for domain in allowed_suffixes)
    except Exception:
        return False


@app.get("/dailymotion/stream/{video_id}.m3u8")
async def dailymotion_stream_proxy(video_id: str):
    """
    On-demand resolver and redirect for Dailymotion video highlights.
    Extracts direct HLS master .m3u8 stream via yt-dlp with TLS impersonation.
    """
    if not re.match(r"^[a-zA-Z0-9_-]{3,32}$", video_id):
        raise HTTPException(status_code=400, detail="ID video Dailymotion non valido")

    stream_url = await dailymotion_service.resolve_stream_url(video_id)
    if stream_url:
        if not is_trusted_stream_redirect(stream_url, ("dmcdn.net", "dailymotion.com")):
            logger.warning("Blocked redirect to untrusted Dailymotion domain: %s", stream_url)
            raise HTTPException(status_code=502, detail="URL del flusso Dailymotion non attendibile")
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
        if not is_trusted_stream_redirect(redirect_url, ("googlevideo.com", "youtube.com")):
            logger.warning("Blocked redirect to untrusted YouTube domain: %s", redirect_url)
            raise HTTPException(status_code=502, detail="URL del flusso YouTube non attendibile")
        return RedirectResponse(url=redirect_url, status_code=307)

    logger.warning("YouTube stream extraction failed for %s", video_id)
    raise HTTPException(
        status_code=502,
        detail="Impossibile estrarre lo streaming da YouTube. Riprova più tardi.",
    )


@app.get("/image-proxy")
async def image_proxy(url: str):
    """Proxies upstream images through DoH to prevent ISP blockades with anti-SSRF protection."""
    if not url:
        raise HTTPException(status_code=400, detail="Missing url parameter")

    is_safe, reason = await doh_client.is_safe_image_target_async(url)
    if not is_safe:
        raise HTTPException(status_code=403, detail=f"Access denied: {reason}")

    content, content_type = await doh_client.proxy_image(url)
    if not content:
        raise HTTPException(status_code=502, detail="Failed to proxy image or invalid image content")

    return Response(content=content, media_type=content_type, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/poster/{match_id}.jpg")
async def get_dynamic_poster(
    match_id: str,
    s: Optional[str] = "upcoming",
    t: Optional[str] = "",
    tv: Optional[int] = 0,
):
    """
    Renders dynamic 16:9 poster with status badge (upcoming time / live)
    and TvVoo bookmark ribbon.
    Uses cached JPEG on disk (zero CPU after first generation).
    """
    clean_id = match_id.replace("streamsport:", "")
    match = catalog_service.get_cached_match(clean_id)
    if not match:
        match = await db_service.get_match_by_id_async(clean_id)

    content = await banner_service.get_or_create_poster(
        match=match,
        match_id=clean_id,
        status=s or "upcoming",
        time_str=t or "",
        has_tvvoo=bool(tv),
    )
    if not content:
        raise HTTPException(status_code=404, detail="Poster not found")

    return Response(
        content=content,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=1800"},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "addon": "StreamSport"}


if __name__ == "__main__":
    import uvicorn
    from app.config import HOST, PORT
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
