import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import CORS_ORIGINS
from app.routers import agent, uploads, visualize
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_background_indexer()
    yield


app = FastAPI(title="Artnuss Art Visualizer Agent", lifespan=lifespan)

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


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
