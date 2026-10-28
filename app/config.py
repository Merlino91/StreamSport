import os
from pathlib import Path
from dotenv import load_dotenv

# Base Paths
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

# Load local environment variables from .env
load_dotenv(PROJECT_DIR / ".env")

# Server configuration
HOST = os.getenv("STREAMSPORT_HOST", "0.0.0.0")
PORT = int(os.getenv("STREAMSPORT_PORT", os.getenv("EASYSPORTS_PORT", "7002")))
DEBUG = os.getenv("STREAMSPORT_DEBUG", "false").lower() in ("true", "1", "yes")
PUBLIC_URL = os.getenv("PUBLIC_URL", os.getenv("BASE_URL", "")).strip().rstrip("/")

# Upstream API settings
ENABLE_STREAMED = os.getenv("ENABLE_STREAMED", "false").lower() in ("true", "1", "yes")
STREAMED_API_HOST = os.getenv("STREAMED_API_HOST", "streamed.pk")
STREAMED_FALLBACK_HOSTS = ["streamed.su", "streamed.pk"]
STREAMED_DOH_RESOLVER = "https://cloudflare-dns.com/dns-query"
STREAMED_CACHE_TTL = int(os.getenv("STREAMED_CACHE_TTL", "3600"))  # seconds (1 hour)
CATALOG_SYNC_INTERVAL = int(os.getenv("CATALOG_SYNC_INTERVAL", "3600"))  # seconds (1 hour)

# Motorsport Official Registry API (Orange Cat Blacktop)
ENABLE_OCBLACKTOP = os.getenv("ENABLE_OCBLACKTOP", "false").lower() in ("true", "1", "yes")
OCBLACKTOP_API_KEY = os.getenv("OCBLACKTOP_API_KEY", "")
OCBLACKTOP_API_BASE = os.getenv("OCBLACKTOP_API_BASE", "https://api.ocblacktop.com/v1")

# Concluded Events, Highlights and Replays Toggle
ENABLE_REPLAYS = os.getenv("ENABLE_REPLAYS", "false").lower() in ("true", "1", "yes")

# TheSportsDB Official Calendar & Credentials
ENABLE_THESPORTSDB = os.getenv("ENABLE_THESPORTSDB", "true").lower() in ("true", "1", "yes")
THESPORTSDB_USER = os.getenv("THESPORTSDB_USER", "")
THESPORTSDB_PASS = os.getenv("THESPORTSDB_PASS", "")
THESPORTSDB_API_KEY = os.getenv("THESPORTSDB_API_KEY", "123")
THESPORTSDB_CALENDAR_INTERVAL = int(os.getenv("THESPORTSDB_CALENDAR_INTERVAL", "43200"))  # 12 hours

# TvVoo (Vavoo) Provider and Italian EPG TV Guide Settings (Sky & Tivùsat)
ENABLE_TVVOO = os.getenv("ENABLE_TVVOO", "true").lower() in ("true", "1", "yes")
ENABLE_ITALIAN_EPG = os.getenv("ENABLE_ITALIAN_EPG", "true").lower() in ("true", "1", "yes")
TVVOO_CACHE_INTERVAL = int(os.getenv("TVVOO_CACHE_INTERVAL", "21600"))  # 6 hours (in seconds)
ITALIAN_EPG_CACHE_INTERVAL = int(os.getenv("ITALIAN_EPG_CACHE_INTERVAL", "7200"))  # 2 hours (in seconds)

# FCTV33 (RBTV / TCPWE) Provider Settings
ENABLE_FCTV33 = os.getenv("ENABLE_FCTV33", "true").lower() in ("true", "1", "yes")
FCTV33_API_BASE = os.getenv("FCTV33_API_BASE", "https://apis-data10.tcpwe138stya.ru")
FCTV33_CACHE_TTL = int(os.getenv("FCTV33_CACHE_TTL", "3600"))  # 1 hour (in seconds)

# Mandrakodi Provider Settings
ENABLE_MANDRAKODI = os.getenv("ENABLE_MANDRAKODI", "true").lower() in ("true", "1", "yes")
MANDRAKODI_PASSWORD = os.getenv("MANDRAKODI_PASSWORD", "UDvXkJqTBR")
MANDRAKODI_DEVICE_ID = os.getenv("MANDRAKODI_DEVICE_ID", "ABC123XYZ456")
MANDRAKODI_CACHE_TTL = int(os.getenv("MANDRAKODI_CACHE_TTL", "600"))  # 10 minutes (in seconds)
ENABLE_MANDRAKODI_AMSTAFF = os.getenv("ENABLE_MANDRAKODI_AMSTAFF", "true").lower() in ("true", "1", "yes")
ENABLE_MANDRAKODI_ACESTREAM = os.getenv("ENABLE_MANDRAKODI_ACESTREAM", "true").lower() in ("true", "1", "yes")
ENABLE_MANDRAKODI_SOCCERON = os.getenv("ENABLE_MANDRAKODI_SOCCERON", "true").lower() in ("true", "1", "yes")

# Sportzonline (SPON) Provider Settings
ENABLE_SPON = os.getenv("ENABLE_SPON", "true").lower() in ("true", "1", "yes")
SPON_PRIMARY_URL = os.getenv("SPON_PRIMARY_URL", "https://sportsonline.cx/prog.txt")
SPON_FALLBACK_URL = os.getenv("SPON_FALLBACK_URL", "https://sportsonline.st/prog.txt")
SPON_CACHE_TTL = int(os.getenv("SPON_CACHE_TTL", "7200"))  # 2 hours (in seconds)
SPON_ITALIAN_ONLY = os.getenv("SPON_ITALIAN_ONLY", "true").lower() in ("true", "1", "yes")



# Addon Metadata
ADDON_ID = "com.streamsport.addon"
ADDON_NAME = "StreamSport"
ADDON_VERSION = "1.0.0"
ADDON_DESCRIPTION = "Eventi sportivi live con catalogo a generi, canali italiani e internazionali, e sintesi/replay post-partita."

# Dedicated Sport Catalogs Definitions
CATALOG_TYPE = "Live Sports"
CATALOG_ID = "streamsport_events"  # Fallback/legacy catalog ID
CATALOG_NAME = "Tutti gli Eventi"

CATALOG_DEFINITIONS = [
    {
        "id": "calcio_italiano",
        "name": "🇮🇹 Calcio Italiano",
        "genres": [
            "Serie A",
            "Serie B",
            "Serie C",
            "Coppa Italia e Supercoppa",
            "Calcio Femminile",
            "Primavera e Giovanili",
        ],
    },
    {
        "id": "calcio_estero",
        "name": "🌍 Calcio Internazionale e Coppe",
        "genres": [
            "Champions League",
            "Europa e Conference League",
            "Premier League",
            "La Liga",
            "Bundesliga e Ligue 1",
            "Altri Campionati Europei",
            "Americhe e Leghe Extra-UE",
            "Europei Under 21 e Nazionali Giovanili",
            "Nazionali e Amichevoli",
        ],
    },
    {
        "id": "tennis",
        "name": "🎾 Tennis",
        "genres": [
            "ATP",
            "WTA",
            "Coppa Davis e BJK Cup",
            "Grandi Slam",
            "Challenger e Altri",
        ],
    },
    {
        "id": "motori",
        "name": "🏎️ Motori",
        "genres": [
            "Formula 1",
            "MotoGP e Superbike",
            "Rally e WRC",
            "NASCAR e IndyCar",
        ],
    },
    {
        "id": "basket",
        "name": "🏀 Basket",
        "genres": [
            "NBA",
            "LBA Serie A",
            "Eurolega ed Eurocup",
            "WNBA e Femminile",
            "Campionati Esteri ed NBL",
            "NCAA e College Basket",
            "FIBA e Tornei Nazionali",
        ],
    },
    {
        "id": "volley",
        "name": "🏐 Volley",
        "genres": [
            "Superlega e Serie A1",
            "Champions League Volley",
            "Volley Femminile",
            "Nazionali e Internazionale",
        ],
    },
    {
        "id": "football_americano",
        "name": "🏈 Football Americano",
        "genres": [
            "NFL",
            "NCAA College Football",
            "CFL e Altre Leghe",
        ],
    },
    {
        "id": "baseball",
        "name": "⚾ Baseball",
        "genres": [
            "MLB",
            "NCAA College Baseball",
            "Campionati Internazionali",
        ],
    },
    {
        "id": "hockey",
        "name": "🏒 Hockey",
        "genres": [
            "NHL",
            "KHL e Leghe Europee",
            "Mondiali e Nazionali",
        ],
    },
    {
        "id": "combattimento",
        "name": "🥊 Sport da Combattimento",
        "genres": [
            "UFC",
            "MMA",
            "Boxe",
            "Wrestling e WWE",
        ],
    },
    {
        "id": "altri_sport",
        "name": "🎯 Altri Sport",
        "genres": [
            "Golf",
            "Freccette / Darts",
            "Rugby e AFL",
            "Ciclismo",
            "Cricket",
            "Pallamano",
            "Biliardo, Padel e Altri",
        ],
    },
]

CATALOG_MAP = {c["id"]: c for c in CATALOG_DEFINITIONS}
SPORT_GENRES = [g for c in CATALOG_DEFINITIONS for g in c["genres"]]

# ID Prefixes recognized by StreamSport
ID_PREFIXES = ["streamsport:"]
