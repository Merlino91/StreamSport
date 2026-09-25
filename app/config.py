import os
from pathlib import Path
from dotenv import load_dotenv

# Base Paths
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
CONFIG_DIR = PROJECT_DIR / "config"
TV_CHANNELS_FILE = CONFIG_DIR / "tv_channels.json"

# Load local environment variables from .env
load_dotenv(PROJECT_DIR / ".env")

# Server configuration
HOST = os.getenv("STREAMSPORT_HOST", "0.0.0.0")
PORT = int(os.getenv("STREAMSPORT_PORT", os.getenv("EASYSPORTS_PORT", "7002")))
DEBUG = os.getenv("STREAMSPORT_DEBUG", "false").lower() in ("true", "1", "yes")

# Upstream API settings
STREAMED_API_HOST = os.getenv("STREAMED_API_HOST", "streamed.pk")
STREAMED_FALLBACK_HOSTS = ["streamed.su", "streamed.pk"]
STREAMED_DOH_RESOLVER = "https://cloudflare-dns.com/dns-query"
STREAMED_CACHE_TTL = int(os.getenv("STREAMED_CACHE_TTL", "3600"))  # seconds (1 hour)
CATALOG_SYNC_INTERVAL = int(os.getenv("CATALOG_SYNC_INTERVAL", "3600"))  # seconds (1 hour)

# Motorsport Official Registry API (Orange Cat Blacktop)
OCBLACKTOP_API_KEY = os.getenv("OCBLACKTOP_API_KEY", "")
OCBLACKTOP_API_BASE = os.getenv("OCBLACKTOP_API_BASE", "https://api.ocblacktop.com/v1")



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
