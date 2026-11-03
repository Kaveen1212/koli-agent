# Artnuss Art Visualizer Agent

An AI shopping assistant for the Artnuss Art Marketplace. Customers chat with the agent to find art, analyze their room, and see real catalog artworks placed on their actual walls before buying.

---

## What It Does

- **Semantic art search** — customers describe what they want ("calm earthy tones", "minimalist abstract") and the agent finds matching real products using vector similarity search
- **Room analysis** — customer uploads a wall photo, Gemini vision reads the color, lighting, and style
- **Wall visualization** — Gemini image editing places a real catalog artwork onto the customer's room photo
- **Conversation memory** — every conversation is persisted per user so context survives restarts

---

## Architecture

```
Customer message
      ↓
FastAPI  →  LangGraph agent (gemini-2.5-flash)
                  ↓
         Tool-calling loop
         ├── search_art            → pgvector semantic search → koli_art PostgreSQL
         ├── find_artwork_by_title → direct SQL title lookup
         ├── get_item_details      → direct SQL by product ID
         ├── filter_by_category    → SQL category filter
         ├── analyze_room          → Gemini vision reads the room photo
         ├── visualize_on_wall     → Gemini image editing (room + artwork)
         └── stage_decor           → Gemini image editing (surface + décor item)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI |
| Agent framework | LangGraph + LangChain Google GenAI |
| AI models | Gemini 2.5 Flash (agent + vision), Gemini image model (visualization), Gemini Embedding 2 (search) |
| Database | PostgreSQL + pgvector (vector similarity search) |
| Async jobs | Redis + RQ worker |
| Image storage | Local disk (dev) / AWS S3 or Cloudflare R2 (production) |
| Observability | LangSmith |

---

## Project Structure

```
app/
  agent/
    orchestrator.py       ← LangGraph graph, conversation history, Gemini calls
    prompts.py            ← SYSTEM_PROMPT for the agent
    tools/
      artwork.py          ← search_art, get_item_details, filter_by_category, find_artwork_by_title
      room.py             ← analyze_room (Gemini vision)
      visualize.py        ← visualize_on_wall (Gemini image editing)
      decor.py            ← stage_decor (Gemini image editing)
  models/
    artwork_embedding.py  ← pgvector embeddings table
    chat_session.py       ← conversation history per user
    room_upload.py        ← uploaded room photos
    generation.py         ← visualization job tracking + cost
  services/
    embedding_service.py  ← Gemini Embedding 2 (text + image vectors)
    search_service.py     ← pgvector cosine similarity search
    image_service.py      ← Gemini vision + image editing
    storage_service.py    ← S3/R2 upload, download, URL generation
    gemini_retry.py       ← retry wrapper for transient 503/429 errors
  routers/
    agent.py              ← POST /agent/message
    uploads.py            ← POST /images/upload
    visualize.py          ← POST /visualize/wall, GET /visualize/status/{id}
  worker/
    queue.py              ← Redis + RQ connection
    tasks.py              ← run_wall_visualization background job
scripts/
  seed_products.py        ← insert test products into the database
  ingest_embeddings.py    ← embed all products for semantic search
tests/
  test_embedding_service.py
  test_search_service.py
  test_orchestrator.py
  test_tools.py
```

---

## Prerequisites

- Python 3.12
- Docker (for PostgreSQL and Redis)
- uv package manager
- Gemini API key from Google AI Studio
- LangSmith API key (optional, for tracing)

---

## Setup

**1. Start the database and Redis:**
```bash
cd koli-art-backend
docker compose up -d
```

**2. Install dependencies:**
```bash
cd "Koli agent"
uv sync
```

**3. Configure environment — copy `.env.example` to `.env` and fill in:**

```env
GEMINI_API_KEY=your_gemini_api_key
DATABASE_URL=postgresql://koli_user:koli_pass@localhost:5432/koli_art

REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=koli_redis_pass

# LangSmith (optional)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_key
LANGCHAIN_PROJECT=koli-art-agent

# Storage — leave empty to use local disk in dev
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=auto
AWS_ENDPOINT_URL=
AWS_CLOUDFRONT_URL=
STORAGE_BUCKET=koli-art-media
```

**4. Enable pgvector in the database (run once):**
```bash
docker exec -it koli-postgres psql -U koli_user -d koli_art -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**5. Run database migrations:**
```bash
uv run python -m alembic upgrade head
```

The agent's tables (`artwork_embeddings`, `chat_sessions`, `generations`, `room_uploads`) and its `alembic_version` live in the **`agent` schema**, not `public` (which belongs to the backend's Prisma). The app and Alembic both put `agent,public` on the `search_path`. Never run a pre-`a01c5e7d9b21` image against a migrated database; for an emergency rollback use `alembic downgrade -1` or `scripts/revert_agent_schema.sql`.

**6. Seed test data and generate embeddings:**
```bash
uv run python scripts/seed_products.py
uv run python scripts/ingest_embeddings.py
```

---

## Running

Open two terminals:

**Terminal 1 — API server:**
```bash
cd "Koli agent"
uv run python -m uvicorn app.main:app --reload
```

**Terminal 2 — Background worker:**
```bash
cd "Koli agent"
uv run rq worker --url redis://:koli_redis_pass@localhost:6379
```

Observability: every chat turn logs one JSON line on the `koli.turn` logger (`requestId`, hashed `user` key, `latencyMs`, `toolCalls`, `tokens`, `status`; never raw ids or message text). The agent reads `X-Request-ID` if the caller sends it and echoes it back. `/metrics` exposes `koli_agent_turns_total`, `koli_agent_turn_latency_p95_seconds`, `koli_agent_gemini_errors_total`, `koli_agent_index_lag_seconds` and `koli_agent_quota_rejections_total` (plus a latency histogram, tool calls, tokens, index state).

API docs (local dev only): set `ENABLE_API_DOCS=true`, then open http://127.0.0.1:8000/docs. They are disabled by default (and in production).

---

## CI

GitHub Actions runs lint, `alembic upgrade head` + `pytest` against pgvector, and a `docker build` on every PR. See [docs/CI.md](docs/CI.md), including how to make it a required check on `main`.

---

## API Endpoints

### `POST /agent/message`
Send a message to the agent.

```json
{
  "user_id": "user123",
  "message": "show me abstract art under $200",
  "image_url": null
}
```

With a room photo:
```json
{
  "user_id": "user123",
  "message": "what art would look good on this wall?",
  "image_url": "http://localhost:8000/static/uploads/room.jpg"
}
```

---

### `POST /images/upload`
Upload a room or surface photo. Returns a URL to pass to `/agent/message`.

```json
{ "image_url": "http://localhost:8000/static/uploads/abc123.jpg" }
```

---

### `POST /visualize/wall`
Enqueue an async wall visualization job.

```json
{
  "user_id": "user123",
  "room_image_url": "http://localhost:8000/static/uploads/room.jpg",
  "artwork_image_url": "https://cdn.example.com/artwork.jpg",
  "artwork_id": "product-uuid",
  "placement": "centered on the main wall"
}
```
Returns: `{ "generation_id": "...", "status": "queued" }`

---

### `GET /visualize/status/{generation_id}`
Check the status of a visualization job.

---

## Tests

```bash
uv run pytest tests/ -v
```

10 tests cover: embedding service, semantic search, agent loop, and all tools.

---

## Adding New Products to Search

When sellers upload new products via the koli-art-backend, run:

```bash
uv run python scripts/ingest_embeddings.py
```

This embeds any products not yet in `artwork_embeddings`. Only embedded products appear in `search_art` results.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Google AI Studio API key |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_HOST` | Yes | Redis host |
| `REDIS_PORT` | Yes | Redis port |
| `REDIS_PASSWORD` | Yes | Redis password |
| `LANGCHAIN_TRACING_V2` | No | `true` to enable LangSmith tracing |
| `LANGCHAIN_API_KEY` | No | LangSmith API key |
| `LANGCHAIN_PROJECT` | No | LangSmith project name |
| `STORAGE_BUCKET` | No | S3/R2 bucket name |
| `AWS_ACCESS_KEY_ID` | No | S3/R2 access key |
| `AWS_SECRET_ACCESS_KEY` | No | S3/R2 secret key |
| `AWS_ENDPOINT_URL` | No | R2 endpoint URL (leave empty for AWS S3) |
| `AWS_CLOUDFRONT_URL` | No | CDN URL for public image serving |
| `BACKEND_URL` | Yes | Backend API base URL. In production use the docker-network URL `http://backend:3000/v1`, never the public `https://api.artnuss.com` (the edge blocks `/v1/internal/*`). Default `http://localhost:4000/v1` is for local dev |
| `ENABLE_API_DOCS` | No | `true` exposes `/docs`, `/redoc`, `/openapi.json` (local dev only; default off) |
| `METRICS_TOKEN` | No | Enables `GET /metrics` (Prometheus), scraped with `Authorization: Bearer <token>`. Unset = the route returns 404. Keep it off public routes |
| `LOG_HASH_SALT` | No | Optional salt for the hashed user key in the per-turn log line |

Backend/deploy verified: auto-deploy pipeline live as of 2026-07-05.
