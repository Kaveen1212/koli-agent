from dotenv import load_dotenv
import logging
import os
from urllib.parse import urlsplit


load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

DATABASE_URL = os.getenv("DATABASE_URL", "")

STORAGE_BUCKET: str = os.getenv("STORAGE_BUCKET", "")
SECRET_KEY: str    = os.getenv("SECRET_KEY", "")

# koli-ART backend. Catalogue data is read through its /v1/internal/* API
# rather than by querying Prisma's tables directly — see backend_client.py.
# In production this MUST be the docker-network URL (http://backend:3000/v1),
# never the public https://api.artnuss.com: the edge blocks /v1/internal/*
# from the internet. The localhost default is for local development only.
BACKEND_URL: str = os.getenv("BACKEND_URL", "http://localhost:4000/v1")
INTERNAL_SERVICE_TOKEN: str = os.getenv("INTERNAL_SERVICE_TOKEN", "")
BACKEND_TIMEOUT_SECONDS: float = float(os.getenv("BACKEND_TIMEOUT_SECONDS", "10"))



def parse_media_public_base_url(raw: str) -> str:
    """The media host's base URL without a trailing slash, or "" when unset or
    unusable. Only an https URL with a host (no credentials, query or fragment)
    is accepted; anything else is ignored with a warning rather than stopping
    the agent, which then keeps fetching media from the backend."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        parts.port  # noqa: B018 - raises ValueError on a non-numeric port
        valid = (parts.scheme == "https" and bool(parts.hostname)
                 and not (parts.username or parts.password or parts.query or parts.fragment))
    except ValueError:
        valid = False
    if not valid:
        logging.getLogger(__name__).warning(
            "MEDIA_PUBLIC_BASE_URL is not a valid https URL; ignoring it "
            "(media is fetched from BACKEND_URL)")
        return ""
    return raw.rstrip("/")


# Public media host (Cloudflare R2 behind e.g. https://media.artnuss.com). Set it
# in the SAME step as the backend's STORAGE_DRIVER=s3: the backend then answers
# /uploads/<key> with a redirect, which the agent never follows, so old
# "/uploads/<key>" references must be mapped to "<this>/<key>" directly.
# Unset (default): "/uploads/..." is fetched from BACKEND_URL's origin.
MEDIA_PUBLIC_BASE_URL: str = parse_media_public_base_url(os.getenv("MEDIA_PUBLIC_BASE_URL", ""))

AWS_ACCESS_KEY_ID:     str = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY: str = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION:            str = os.getenv("AWS_REGION", "auto")
AWS_ENDPOINT_URL:      str = os.getenv("AWS_ENDPOINT_URL", "")   # set for R2, leave empty for S3
AWS_CLOUDFRONT_URL:    str = os.getenv("AWS_CLOUDFRONT_URL", "")

LANGCHAIN_TRACING_V2: str = os.getenv("LANGCHAIN_TRACING_V2", "false")
LANGCHAIN_API_KEY:    str = os.getenv("LANGCHAIN_API_KEY", "")
LANGCHAIN_PROJECT:    str = os.getenv("LANGCHAIN_PROJECT", "koli-art-agent")

REDIS_HOST:     str = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT:     int = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
# Railway / Render / Fly inject a single REDIS_URL — parse it when present.
_REDIS_URL: str = os.getenv("REDIS_URL", "")
if _REDIS_URL:
    import urllib.parse as _up
    _r = _up.urlparse(_REDIS_URL)
    REDIS_HOST     = _r.hostname or REDIS_HOST
    REDIS_PORT     = _r.port    or REDIS_PORT
    REDIS_PASSWORD = _r.password or REDIS_PASSWORD

# Public base URL used to build image URLs. In production set to your domain.
PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# Caps on every image the agent downloads (see services/net.py). The backend
# accepts uploads up to 10 MB, so the default fits any image it can store.
IMAGE_FETCH_MAX_BYTES:       int   = int(os.getenv("IMAGE_FETCH_MAX_BYTES", str(10 * 1024 * 1024)))
IMAGE_FETCH_TIMEOUT_SECONDS: float = float(os.getenv("IMAGE_FETCH_TIMEOUT_SECONDS", "15"))

# Comma-separated list of allowed CORS origins. "*" only for local dev.
CORS_ORIGINS: list[str] = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
]

# Per-user daily cap on image generations (runaway-cost guard).
MAX_GENERATIONS_PER_DAY: int = int(os.getenv("MAX_GENERATIONS_PER_DAY", "20"))

# Cap across ALL users per day. The per-user cap can be sidestepped by opening
# more accounts; this one bounds the total spend.
MAX_GLOBAL_GENERATIONS_PER_DAY: int = int(os.getenv("MAX_GLOBAL_GENERATIONS_PER_DAY", "200"))

# How many past conversation turns to keep in history sent to the model.
MAX_HISTORY_TURNS: int = int(os.getenv("MAX_HISTORY_TURNS", "20"))

# Approx cost in USD per image generation, used for the cost ledger.
COST_PER_GENERATION: float = float(os.getenv("COST_PER_GENERATION", "0.039"))

# Embedding model + dimension — must match the artwork_embeddings vector size.
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "gemini-embedding-2")
EMBEDDING_DIM:   int = int(os.getenv("EMBEDDING_DIM", "3072"))

# Background catalogue indexing (services/indexer.py): how often new or edited
# products are embedded for search, and the most embeddings one pass may create
# so a bulk import can't spike Gemini spend. 0 turns the indexer off.
INDEX_INTERVAL_SECONDS: int = int(os.getenv("INDEX_INTERVAL_SECONDS", "600"))
INDEX_MAX_PER_RUN:      int = int(os.getenv("INDEX_MAX_PER_RUN", "100"))

# Image generation / vision model names.
IMAGE_MODEL:  str = os.getenv("IMAGE_MODEL", "gemini-3.1-flash-image")
VISION_MODEL: str = os.getenv("VISION_MODEL", "gemini-2.5-flash")
AGENT_MODEL:  str = os.getenv("AGENT_MODEL", "gemini-2.5-flash")

# Interactive API docs (/docs, /redoc, /openapi.json) are off unless this is
# explicitly enabled (local development only).
ENABLE_API_DOCS: bool = os.getenv("ENABLE_API_DOCS", "false").strip().lower() in ("1", "true", "yes", "on")

# Observability (app/observability.py). /metrics returns 404 unless METRICS_TOKEN
# is set, and then requires "Authorization: Bearer <token>".
METRICS_TOKEN: str = os.getenv("METRICS_TOKEN", "")
# Optional salt for the hashed user key in the per-turn log line.
LOG_HASH_SALT: str = os.getenv("LOG_HASH_SALT", "")
