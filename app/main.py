import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import CORS_ORIGINS
from app.routers import agent, uploads, visualize

app = FastAPI(title="Artnuss Art Visualizer Agent")

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
