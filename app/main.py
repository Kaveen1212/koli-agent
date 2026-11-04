import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import CORS_ORIGINS, ENABLE_API_DOCS
from app.routers import agent, metrics, uploads, visualize
from app.services.indexer import start_background_indexer

# uvicorn only configures its own loggers, so without a handler here the app's
# info and warning messages (indexer runs, empty-reply recoveries) never reach
# `docker logs`.
_app_log = logging.getLogger("app")
if not _app_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     [%(name)s] %(message)s"))
    _app_log.addHandler(_handler)
    _app_log.setLevel(logging.INFO)

# One JSON object per chat turn, printed bare so a log collector can parse each
# line as-is (see app/observability.py for the fields).
_turn_log = logging.getLogger("koli.turn")
if not _turn_log.handlers:
    _turn_handler = logging.StreamHandler()
    _turn_handler.setFormatter(logging.Formatter("%(message)s"))
    _turn_log.addHandler(_turn_handler)
    _turn_log.setLevel(logging.INFO)
    _turn_log.propagate = False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_background_indexer()
    yield


# The schema and Swagger UI are disabled by default so the agent's tool and
# route surface is not browsable from the internet.
app = FastAPI(
    title="Artnuss Art Visualizer Agent",
    lifespan=lifespan,
    docs_url="/docs" if ENABLE_API_DOCS else None,
    redoc_url="/redoc" if ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure static dirs exist before mounting.
for sub in ("uploads", "generations"):
    os.makedirs(os.path.join("static", sub), exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(agent.router)
app.include_router(uploads.router)
app.include_router(visualize.router)
app.include_router(metrics.router)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
