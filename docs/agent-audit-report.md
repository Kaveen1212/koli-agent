# Artnuss Agent — Deep Audit Report

_Date: 2026-06-02 · Scope: full backend at `Koli agent/app`_

This report is the result of reading every core module and cross-referencing
the call paths (router → service → worker → DB). Findings are grouped by
severity. Each item cites the file and the concrete evidence.

> **Resolution status (2026-06-02 hardening pass):**
> FIXED — C1, C2, H3, H4, H5, M1, M2, M3, M4, M5, M6, M7, M8(partial), L1, L5.
> Migration `a1b2c3d4e5f6` applied. All 10 tests pass; full app imports clean.
> STILL OPEN — **H1 (authentication)**: `user_id` is still client-supplied; wiring
> the koli-art-backend JWT requires the shared signing secret and is deferred.
> L2 (tests hit live APIs), L3 (content moderation), L4 done via .gitignore.

---

## Executive Summary

The **synchronous agent path works** (chat → search → analyze room → visualize on
wall via the LangGraph tool). It has been tested end-to-end.

The **asynchronous REST path is broken** and several core design-doc features are
either half-implemented or inconsistent between the two visualization code paths.
There is **no authentication**, **no rate limiting**, and **secrets are committed**
to the repo.

Overall: solid prototype, **not production-ready**. ~8 issues are must-fix.

---

## 🔴 CRITICAL — will crash or corrupt data

### C1. Async visualization worker reads columns that do not exist
**Files:** `app/routers/visualize.py:29-37`, `app/worker/tasks.py:40-43`, migration `f18c73e9debd`

The `generations` table columns are:
`id, user_id, type, room_upload_id, artwork_ids, result_object_key, status, cost, created_at`.

The router inserts the room URL into `room_upload_id` and the artwork id into
`artwork_ids` (JSON). It never stores `artwork_image_url` or `placement_hint`.

But the worker reads:
```python
room_image_url   = row["room_image_url"]      # ❌ no such column
artwork_image_url= row["artwork_image_url"]   # ❌ no such column
placement_hint   = row.get("placement_hint")  # ❌ no such column
```
→ Every `/visualize/wall` job throws `KeyError`, is marked `failed`, and never
produces an image. **The entire async queue path is dead.**

**Fix:** align the schema. Add `room_image_url`, `artwork_image_url`,
`placement_hint` columns (or read from `room_upload_id` / `artwork_ids`) and make
the router store what the worker reads.

---

### C2. Image embeddings use a different, likely-nonexistent model
**File:** `app/services/embedding_service.py:30-41`

`embed_query` and `embed_artwork_text` use `gemini-embedding-2`.
`embed_artwork_image` uses `gemini-embedding-exp-03-07` — a **different, older
model**. Embeddings from two different models live in **incompatible vector
spaces**, so image-vector search could never match text-vector search even if it
were wired up. The old model name may also 404 like the other deprecated models did.

**Fix:** use one model (`gemini-embedding-2`) for all three functions, or remove
`embed_artwork_image` until multimodal search is actually built.

---

## 🟠 HIGH — core feature missing or security risk

### H1. No authentication — users can read each other's data
**Files:** `app/routers/agent.py:8-11`, `app/routers/visualize.py:12-17`

`user_id` is supplied in the request body with no auth token. Anyone can pass
another user's `user_id` and read/overwrite their chat history
(`_load_history` / `_save_history`) or their generations. There is no JWT check,
no session validation.

**Fix:** require the koli-art-backend JWT, derive `user_id` from the verified
token, never from the request body.

### H2. Secrets committed to the repo
**File:** `.env`

`GEMINI_API_KEY` and a real `LANGCHAIN_API_KEY` (`lsv2_pt_...`) are committed.
Anyone with repo access has the keys.

**Fix:** rotate both keys now, ensure `.env` is in `.gitignore`, keep only
`.env.example` in git.

### H3. `image_vector` column is never populated — multimodal search not built
**Files:** `app/models/artwork_embedding.py`, `scripts/ingest_embeddings.py`

The schema has `image_vector vector(3072)` and the design doc calls for
"search by room photo," but `ingest_embeddings.py` only writes `text_vector`.
Searching the catalog by an uploaded image is impossible today.

**Fix:** either populate `image_vector` (with the SAME model as queries — see C2)
and add an image-search path, or drop the column and the unbuilt requirement from
scope.

### H4. CORS misconfiguration
**File:** `app/main.py:14-20`

`allow_origins=["*"]` together with `allow_credentials=True` is rejected by
browsers and is unsafe for production.

**Fix:** set explicit origins (the frontend URL) from config; only enable
credentials with a concrete origin list.

### H5. No rate limiting or real cost tracking
**Files:** `app/worker/tasks.py:63` (`cost = 0.02` hardcoded), all routers

The design doc names image generation as the "runaway-spend risk." There is no
per-user rate limit and `cost` is a hardcoded placeholder, so the `generations`
cost ledger is meaningless.

**Fix:** add per-user/day generation limits; compute real cost from the model +
resolution.

---

## 🟡 MEDIUM — correctness, consistency, scale

### M1. Two divergent visualization code paths
- **Sync (used by the agent):** `app/agent/tools/visualize.py` → saves to disk,
  applies the Artnuss watermark, returns `preview_url`, but **does NOT write to
  the `generations` table** (no record, no cost).
- **Async (REST `/visualize/wall`):** `worker/tasks.py` → writes to `generations`,
  uploads to S3, but is **broken (C1)** and applies **no watermark**.

The watermark, the DB record, and the storage logic should live in one shared
function used by both paths. Today behavior depends on which path you hit.

### M2. `stage_decor` is inconsistent with `visualize_on_wall`
**File:** `app/agent/tools/decor.py:31-36`

Returns raw `preview_base64` (megabytes) instead of saving to disk and returning a
`preview_url`. No watermark. This bloats LangSmith traces and the DB-stored chat
history, the exact problem already fixed for `visualize_on_wall`.

### M3. Conversation history grows unbounded + non-atomic save
**File:** `app/agent/orchestrator.py:90-102`

`_save_history` does `DELETE` then `INSERT` (not in a transaction → a crash
between them loses history; concurrent messages from the same user race). History
is never trimmed, so it will eventually exceed Gemini's token limit and the row
size. `updated_at` is never set.

**Fix:** single `UPSERT` in one transaction; cap history to last N turns.

### M4. Image re-sent to Gemini on every turn
**File:** `app/agent/orchestrator.py:27-54`

`_prepare_for_gemini` re-reads the stored image URL from disk and re-base64s it on
**every** agent turn for the life of the conversation. Wastes tokens/money each
turn and crashes if the file was deleted.

**Fix:** only attach the image on the turn it was uploaded, or summarize it via
`analyze_room` and drop the raw bytes from history.

### M5. Blocking I/O inside async route serializes the server
**Files:** `app/routers/agent.py:13-19`, `orchestrator.process_message`

`async def send_message` calls the fully **synchronous** `process_message`, which
does blocking DB queries and blocking Gemini HTTP calls. This blocks the event
loop — requests are effectively serialized; one slow Gemini call stalls all users.

**Fix:** run `process_message` in a threadpool (`await run_in_threadpool(...)`) or
make the path async.

### M6. `room_uploads` table is dead
**Files:** `app/routers/uploads.py`, `app/models/room_upload.py`

Uploads write to disk/S3 only; no row is inserted into `room_uploads` and
`analysis_json` is never stored. The table and model are unused.

### M7. Hardcoded `http://localhost:8000`
**Files:** `uploads.py:31`, `tools/visualize.py` (`_save_image`), others

Public URLs are hardcoded to localhost. Nothing will resolve in production.

**Fix:** add `PUBLIC_BASE_URL` to config and build URLs from it.

### M8. SQL builds a vector via f-string interpolation
**File:** `app/services/search_service.py:13-20`

`vector_literal` is interpolated directly into the SQL string. The values are
model floats so it is practically safe, but it is an injection-shaped pattern and
brittle. Prefer registering the pgvector type and binding a parameter (the
`register_vector` adapter is already imported in `database.py`).

---

## 🟢 LOW — cleanup

- **L1.** `app/crud.py` (`get_artwork`) is dead code — nothing imports it since the
  tools moved to `search_service`.
- **L2.** Tests (`tests/`) hit the **real** Gemini API and real DB — they cost
  money, need network + quota, and have no coverage for `visualize_on_wall` or the
  async worker (the broken path). Add mocks and a worker test.
- **L3.** No content moderation on uploaded room photos (design-doc privacy/safety
  item).
- **L4.** Ensure `static/uploads` and `static/generations` are gitignored so user
  content isn't committed.
- **L5.** `chat_session.updated_at` exists but is never written.

---

## What is genuinely working

- LangGraph tool-calling loop with 7 tools, system-prompt-enforced "search before
  recommend" (no more hallucinated products).
- pgvector semantic text search against the real `koli_art` DB.
- Room analysis via Gemini vision (reads real uploaded photo).
- Sync wall visualization with Artnuss watermark + disk save + `preview_url`.
- LangSmith tracing (after the base64-in-history fix shrank traces).
- Retry wrapper for transient 503/429.
- Alembic migration scoped to only the 4 agent tables (does not touch Prisma tables).

---

## Recommended fix order

1. **C1** — repair the async worker schema (or delete the async path and have the
   agent tool write to `generations`). Pick one visualization path (M1).
2. **H2** — rotate and gitignore secrets.
3. **H1** — add auth; derive `user_id` from a verified token.
4. **C2 / H3** — fix embedding-model consistency; decide on multimodal search.
5. **M3 / M4 / M5** — history upsert + trim, stop re-sending images, unblock the
   event loop.
6. **H4 / H5 / M7** — CORS, rate limit, real cost, config-driven base URL.
7. Cleanup L1–L5.
