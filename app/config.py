from dotenv import load_dotenv
import os


load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

DATABASE_URL = os.getenv("DATABASE_URL", "")

STORAGE_BUCKET: str = os.getenv("STORAGE_BUCKET", "")
SECRET_KEY: str    = os.getenv("SECRET_KEY", "")

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

# Public base URL used to build image URLs. In production set to your domain.
PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# Comma-separated list of allowed CORS origins. "*" only for local dev.
CORS_ORIGINS: list[str] = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
]

# Per-user daily cap on image generations (runaway-cost guard).
MAX_GENERATIONS_PER_DAY: int = int(os.getenv("MAX_GENERATIONS_PER_DAY", "20"))

# How many past conversation turns to keep in history sent to the model.
MAX_HISTORY_TURNS: int = int(os.getenv("MAX_HISTORY_TURNS", "20"))

# Approx cost in USD per image generation, used for the cost ledger.
COST_PER_GENERATION: float = float(os.getenv("COST_PER_GENERATION", "0.039"))

# Embedding model + dimension — must match the artwork_embeddings vector size.
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "gemini-embedding-2")
EMBEDDING_DIM:   int = int(os.getenv("EMBEDDING_DIM", "3072"))

# Image generation / vision model names.
IMAGE_MODEL:  str = os.getenv("IMAGE_MODEL", "gemini-3.1-flash-image")
VISION_MODEL: str = os.getenv("VISION_MODEL", "gemini-2.5-flash")
AGENT_MODEL:  str = os.getenv("AGENT_MODEL", "gemini-2.5-flash")