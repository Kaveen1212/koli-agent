"""Keeps artwork_embeddings in step with the sellable catalogue (K-1, P2-T07).

The indexer used to walk the whole catalogue every pass and re-embed anything
whose `updatedAt` moved (a sale moves it), and it could never notice a product
that stopped being sellable. It now follows the backend's change feed:

    GET /v1/internal/products/changes?since=<seq>   (docs/CATALOGUE_FEED.md)

* UPSERT = "sellable now". The product is confirmed through `by-ids`; an id that
  does not come back is treated as a DELETE. It is re-embedded only when the
  text or image the embedding reads really changed: `contentUpdatedAt` newer
  than the stored one AND a different content hash. A stock decrement writes no
  feed row at all, so it costs zero Gemini calls.
* DELETE = not sellable. The vector is dropped, except SOLD which is kept for
  INDEX_SOLD_GRACE_DAYS (default 0 = drop at once). A kept vector is hidden
  from search (sold_at) and a relist within the window reuses it.
* The cursor (`agent.index_state.feed_cursor`) is stored only after a page has
  been processed, so a crash re-reads it; rows are idempotent state statements.
* A full resync (the old catalogue walk) runs on first start, when the cursor is
  in doubt or older than INDEX_FEED_STALE_DAYS (the backend purges feed rows at
  30 days), after a failed embedding, and every INDEX_RESYNC_HOURS as a safety
  net. It also prunes vectors whose product is no longer in the catalogue.
* INDEX_MAX_PER_RUN caps the embedding attempts (Gemini spend) of one pass; the
  cursor stops at the first unprocessed row and the next pass resumes there.

Index lag (koli_agent_index_lag_seconds) is the time since the indexer was last
fully caught up with the feed (excluding the backend's own ~10 s safety window).
"""
import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, text, update

from app.config import (
    EMBEDDING_MODEL,
    INDEX_FEED_STALE_DAYS,
    INDEX_INTERVAL_SECONDS,
    INDEX_MAX_PER_RUN,
    INDEX_RESYNC_HOURS,
    INDEX_SOLD_GRACE_DAYS,
)
from app.database import engine
from app.models.index_state import IndexState
from app.observability import record_index_run
from app.services.backend_client import (
    BackendUnavailable,
    absolute_media_url,
    catalogue_page,
    changes_page,
    products_by_ids,
)
from app.services.embedding_service import embed_artwork_image, embed_artwork_text

logger = logging.getLogger(__name__)

# Postgres advisory-lock key, so two agent processes never index at once.
_LOCK_KEY = 7_470_201
_CATALOGUE_PAGE_SIZE = 100
_FEED_PAGE_SIZE = 200          # backend default; its maximum is 500
_BY_IDS_MAX = 50               # the backend rejects more ids than this per call
_MAX_FEED_PAGES_PER_RUN = 200  # bounds one pass; the cursor resumes next time
_RESYNC_RETRY = timedelta(minutes=30)  # when a failed embedding is retried
# Give the backend time to come up when the whole stack restarts together.
_STARTUP_DELAY_SECONDS = 30
# Bump to force every vector to be rebuilt (the model name is hashed too).
_HASH_VERSION = "1"


def _vec(values) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def content_hash(product: dict) -> str:
    """sha256 of exactly what _embed reads, so equal hash means equal vectors.

    The first image is hashed as the backend stores it, so moving media to
    another host (MEDIA_PUBLIC_BASE_URL) does not trigger a re-embed.
    """
    images = product.get("images") or []
    payload = json.dumps(
        [_HASH_VERSION, EMBEDDING_MODEL, product.get("title") or "",
         product.get("description") or "", product.get("category") or "",
         product.get("medium") or "", images[0] if images else None],
        ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _embed(product: dict) -> tuple[str, str | None]:
    details = " ".join(filter(None, (
        product.get("description"), product.get("category"), product.get("medium"))))
    text_vec = _vec(embed_artwork_text(product.get("title") or "", details))

    image_vec = None
    images = product.get("images") or []
    if images:
        try:
            image_vec = _vec(embed_artwork_image(absolute_media_url(images[0])))
        except Exception as exc:
            # The text vector alone still makes the product searchable.
            logger.warning("Image embedding skipped for %s: %s", product.get("id"), exc)
    return text_vec, image_vec


# --- storage -----------------------------------------------------------------

class PgStore:
    """The indexer's view of agent.artwork_embeddings and agent.index_state.

    Every write commits at once, so a crash loses at most the item in flight.
    """

    _STATE_COLUMNS = ("feed_cursor", "last_caught_up_at", "last_resync_at", "resync_due_at")

    def __init__(self, conn):
        self.conn = conn

    def state(self) -> dict:
        self.conn.execute(text(
            "INSERT INTO index_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING"))
        row = self.conn.execute(text(
            "SELECT feed_cursor, last_caught_up_at, last_resync_at, resync_due_at "
            "FROM index_state WHERE id = 1")).mappings().one()
        self.conn.commit()
        return dict(row)

    def save_state(self, **fields) -> None:
        """Store some of the four state columns. Anything else is a ValueError.

        The UPDATE is built with SQLAlchemy Core from the IndexState table, so
        a column name can only be one the model defines (checked against the
        allow-list first) and every value is a bind parameter.
        """
        unknown = set(fields) - set(self._STATE_COLUMNS)
        if unknown:
            raise ValueError(f"unknown index_state columns: {sorted(unknown)}")
        if not fields:
            return
        self.conn.execute(text(
            "INSERT INTO index_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING"))
        self.conn.execute(update(IndexState).where(IndexState.id == 1)
                          .values(**fields, updated_at=func.now()))
        self.conn.commit()

    @staticmethod
    def _row(r) -> dict:
        return {"hash": (r["content_hash"] or "").strip() or None,
                "content_updated_at": r["source_content_updated_at"],
                "updated_at": r["source_updated_at"], "sold_at": r["sold_at"]}

    def known(self) -> dict[str, dict]:
        rows = self.conn.execute(text(
            "SELECT artwork_id, content_hash, source_content_updated_at, "
            "source_updated_at, sold_at FROM artwork_embeddings")).mappings().all()
        self.conn.commit()
        return {r["artwork_id"]: self._row(r) for r in rows}

    def get(self, artwork_id: str) -> Optional[dict]:
        r = self.conn.execute(text(
            "SELECT artwork_id, content_hash, source_content_updated_at, "
            "source_updated_at, sold_at FROM artwork_embeddings WHERE artwork_id = :id"),
            {"id": artwork_id}).mappings().first()
        self.conn.commit()
        return self._row(r) if r else None

    def put(self, artwork_id, text_vec, image_vec, content_hash_, content_updated_at,
            source_updated_at) -> None:
        try:
            self.conn.execute(text("""
                INSERT INTO artwork_embeddings
                    (artwork_id, text_vector, image_vector, content_hash,
                     source_content_updated_at, source_updated_at, sold_at)
                VALUES (:id, CAST(:tv AS vector), CAST(:iv AS vector), :h, :cu, :upd, NULL)
                ON CONFLICT (artwork_id) DO UPDATE
                SET text_vector = EXCLUDED.text_vector,
                    image_vector = EXCLUDED.image_vector,
                    content_hash = EXCLUDED.content_hash,
                    source_content_updated_at = COALESCE(
                        EXCLUDED.source_content_updated_at,
                        artwork_embeddings.source_content_updated_at),
                    source_updated_at = COALESCE(
                        EXCLUDED.source_updated_at, artwork_embeddings.source_updated_at),
                    sold_at = NULL
            """), {"id": artwork_id, "tv": text_vec, "iv": image_vec, "h": content_hash_,
                   "cu": content_updated_at, "upd": source_updated_at})
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def touch(self, artwork_id, content_hash_=None, content_updated_at=None) -> None:
        """Keep the vector; record a newer version stamp and clear the sold marker."""
        self.conn.execute(text("""
            UPDATE artwork_embeddings
            SET content_hash = COALESCE(:h, content_hash),
                source_content_updated_at = COALESCE(:cu, source_content_updated_at),
                sold_at = NULL
            WHERE artwork_id = :id
        """), {"id": artwork_id, "h": content_hash_, "cu": content_updated_at})
        self.conn.commit()

    def delete(self, artwork_ids: list[str]) -> int:
        if not artwork_ids:
            return 0
        n = self.conn.execute(text(
            "DELETE FROM artwork_embeddings WHERE artwork_id = ANY(:ids)"),
            {"ids": list(artwork_ids)}).rowcount
        self.conn.commit()
        return n

    def mark_sold(self, artwork_id: str) -> bool:
        n = self.conn.execute(text(
            "UPDATE artwork_embeddings SET sold_at = COALESCE(sold_at, now()) "
            "WHERE artwork_id = :id"), {"id": artwork_id}).rowcount
        self.conn.commit()
        return bool(n)

    def purge_sold(self, grace_days: float) -> int:
        n = self.conn.execute(text(
            "DELETE FROM artwork_embeddings "
            "WHERE sold_at IS NOT NULL AND sold_at <= now() - (:d * interval '1 day')"),
            {"d": grace_days}).rowcount
        self.conn.commit()
        return n


# --- the pass ----------------------------------------------------------------

class _Budget(Exception):
    """The per-pass embedding cap was reached; resume next pass."""


def _new_stats() -> dict:
    return {"checked": 0, "embedded": 0, "failed": 0, "needs": 0, "pruned": 0,
            "pruned_reasons": {}, "kept_sold": 0, "sold_purged": 0, "feed_rows": 0,
            "resynced": False, "caught_up": False}


def _embed_and_store(store, product: dict, h: str, content_updated_at, source_updated_at,
                     stats: dict, max_embeds: int) -> None:
    """One paid embedding. Raises _Budget (before any spend) when the cap is hit."""
    stats["needs"] += 1
    if stats["embedded"] + stats["failed"] >= max_embeds:
        raise _Budget()
    try:
        text_vec, image_vec = _embed(product)
        store.put(product["id"], text_vec, image_vec, h, content_updated_at, source_updated_at)
        stats["embedded"] += 1
    except Exception:
        stats["failed"] += 1
        logger.exception("Embedding failed for product %s", product.get("id"))


def _prune(store, ids: list[str], reason: str, stats: dict) -> None:
    n = store.delete(ids)
    if n:
        stats["pruned"] += n
        stats["pruned_reasons"][reason] = stats["pruned_reasons"].get(reason, 0) + n


def _delete(store, product_id: str, reason: str, stats: dict) -> None:
    if reason == "SOLD" and INDEX_SOLD_GRACE_DAYS > 0:
        if store.mark_sold(product_id):
            stats["kept_sold"] += 1
        return
    _prune(store, [product_id], reason, stats)


def _apply_upsert(store, product: dict, content_updated_at: datetime | None,
                  stats: dict, max_embeds: int) -> None:
    """Index one sellable product, embedding only if its content really changed."""
    pid = product["id"]
    h = content_hash(product)
    row = store.get(pid)
    if row is None:
        _embed_and_store(store, product, h, content_updated_at, None, stats, max_embeds)
        return
    newer = content_updated_at is not None and (
        row["content_updated_at"] is None or content_updated_at > row["content_updated_at"])
    if row["hash"] and not newer:
        # Same version we hold (a restock, a relist, a stale row): no embedding.
        if row["sold_at"] is not None:
            store.touch(pid)
        return
    if row["hash"] == h:
        # contentUpdatedAt moved but nothing the embedding reads did (the backend
        # also tracks fields the agent does not use): record it, spend nothing.
        store.touch(pid, h, content_updated_at)
        return
    _embed_and_store(store, product, h, content_updated_at, None, stats, max_embeds)


def _final_items(items: list[dict]) -> dict[str, dict]:
    """Collapse a page to one row per product: the last wins (rows are state
    statements). `first_seq` remembers the earliest row so a cursor stop is safe."""
    final: dict[str, dict] = {}
    for it in items:
        prev = final.get(it["productId"])
        final[it["productId"]] = {**it, "first_seq": prev["first_seq"] if prev else it["seq"]}
    return final


def _process_page(store, items: list[dict], stats: dict, max_embeds: int) -> Optional[int]:
    """Apply one feed page. Returns None when fully processed, else the highest
    seq that is safe to store as the cursor (the cap stopped the page early)."""
    final = _final_items(items)
    stats["checked"] += len(final)
    upserts: list[dict] = []
    for it in final.values():
        if it["op"] == "DELETE":
            _delete(store, it["productId"], it.get("reason") or "UNKNOWN", stats)
        else:
            upserts.append(it)

    for start in range(0, len(upserts), _BY_IDS_MAX):
        chunk = upserts[start:start + _BY_IDS_MAX]
        sellable = {p["id"]: p for p in products_by_ids([it["productId"] for it in chunk])}
        for pos, it in enumerate(chunk):
            product = sellable.get(it["productId"])
            if product is None:
                # The feed said "sellable", the read path (the authority) says no.
                _prune(store, [it["productId"]], "NOT_SELLABLE", stats)
                continue
            try:
                _apply_upsert(store, product, _parse_ts(it.get("contentUpdatedAt")),
                              stats, max_embeds)
            except _Budget:
                pending = chunk[pos:] + upserts[start + _BY_IDS_MAX:]
                return min(p["first_seq"] for p in pending) - 1
    return None


def _feed_head() -> int:
    """The newest cursor the feed will hand out now, without processing anything."""
    since = 0
    for _ in range(_MAX_FEED_PAGES_PER_RUN):
        page = changes_page(since=since, limit=500)
        nxt = int(page["nextSince"])
        if not page.get("hasMore") or nxt <= since:
            return max(since, nxt)
        since = nxt
    return since


def _drain_feed(store, cursor: int, stats: dict, max_embeds: int, now: datetime) -> str:
    """Process the feed from `cursor`. Returns "caught_up", "capped" or "doubt"."""
    since = cursor
    for _ in range(_MAX_FEED_PAGES_PER_RUN):
        page = changes_page(since=since, limit=_FEED_PAGE_SIZE)
        items = page.get("items") or []
        nxt = int(page["nextSince"])
        if nxt < since:
            logger.warning("Change feed went backwards (cursor %d, nextSince %d); resyncing",
                           since, nxt)
            return "doubt"
        stats["feed_rows"] += len(items)
        stop = _process_page(store, items, stats, max_embeds) if items else None
        if stop is not None:
            store.save_state(feed_cursor=max(since, stop))
            return "capped"
        progressed = nxt != since
        if progressed:
            since = nxt
            store.save_state(feed_cursor=since)
        if not page.get("hasMore"):
            return "caught_up"
        if not progressed:
            logger.warning("Change feed reports hasMore but made no progress at %d", since)
            return "capped"
    return "capped"


def _reconcile_catalogue_item(store, product: dict, known: dict, stats: dict,
                              max_embeds: int) -> None:
    h = content_hash(product)
    row = known.get(product["id"])
    updated = _parse_ts(product.get("updatedAt"))
    if row is not None:
        if row["hash"] == h:
            if row["sold_at"] is not None:
                store.touch(product["id"])
            return
        if (row["hash"] is None and row["updated_at"] is not None and updated is not None
                and updated <= row["updated_at"]):
            # A vector from before A04 that is still current: adopt a hash for it
            # instead of paying to rebuild what is already right.
            store.touch(product["id"], h)
            return
    _embed_and_store(store, product, h, None, updated, stats, max_embeds)


def _resync(store, stats: dict, max_embeds: int) -> bool:
    """Walk the whole sellable catalogue. Returns True if the walk completed
    (and anything that fell out of the catalogue was pruned)."""
    stats["resynced"] = True
    known = store.known()
    seen: set[str] = set()
    after = None
    while True:
        page = catalogue_page(after=after, limit=_CATALOGUE_PAGE_SIZE)
        for product in page:
            stats["checked"] += 1
            seen.add(product["id"])
            try:
                _reconcile_catalogue_item(store, product, known, stats, max_embeds)
            except _Budget:
                return False
        if len(page) < _CATALOGUE_PAGE_SIZE:
            break
        after = page[-1]["id"]
    gone = [i for i, row in known.items() if i not in seen and row["sold_at"] is None]
    if gone and not seen:
        # An empty catalogue next to a populated index is far more likely a
        # backend problem than every product vanishing at once.
        logger.warning("Resync saw an empty catalogue; not pruning %d vectors", len(gone))
        return True
    for start in range(0, len(gone), 500):
        _prune(store, gone[start:start + 500], "RESYNC", stats)
    return True


def sync(store, max_embeds: int = INDEX_MAX_PER_RUN, now: datetime | None = None) -> dict:
    """One indexing pass against `store` (see the module docstring).

    One bad product is logged and counted, never fatal. Raises BackendUnavailable
    if the backend can't be read at all; the cursor is then left where it was.
    """
    now = now or datetime.now(timezone.utc)
    stats = _new_stats()
    stats["sold_purged"] = store.purge_sold(INDEX_SOLD_GRACE_DAYS)
    state = store.state()

    cursor = state["feed_cursor"]
    caught_up_at = state["last_caught_up_at"]
    if cursor is not None and caught_up_at is not None and \
            now - caught_up_at > timedelta(days=INDEX_FEED_STALE_DAYS):
        logger.warning("Indexer was behind the feed for more than %s days; resyncing",
                       INDEX_FEED_STALE_DAYS)
        cursor = None
    due = state["resync_due_at"]

    if cursor is None or due is None or due <= now:
        head = _feed_head() if cursor is None else None
        if _resync(store, stats, max_embeds):
            store.save_state(last_resync_at=now,
                             resync_due_at=now + timedelta(hours=INDEX_RESYNC_HOURS))
            if cursor is None:
                cursor = head
                store.save_state(feed_cursor=head)
        else:
            store.save_state(resync_due_at=now)  # keep going next pass
            if cursor is None:
                return stats

    outcome = _drain_feed(store, cursor, stats, max_embeds, now)
    if outcome == "doubt":
        store.save_state(feed_cursor=None, resync_due_at=now)
    elif outcome == "caught_up":
        stats["caught_up"] = True
        store.save_state(last_caught_up_at=now)
    if stats["failed"]:
        # The cursor moved past the failed product; the resync picks it up again.
        retry_at = now + _RESYNC_RETRY
        current = store.state()["resync_due_at"]
        store.save_state(resync_due_at=min(current, retry_at) if current else retry_at)
    return stats


def index_catalog(max_embeds: int = INDEX_MAX_PER_RUN) -> dict:
    """Run one pass under the advisory lock. Also what scripts/ingest_embeddings.py calls."""
    with engine.connect() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}).scalar():
            return {**_new_stats(), "skipped": "another indexer holds the lock"}
        try:
            conn.commit()
            return sync(PgStore(conn), max_embeds)
        finally:
            try:
                conn.rollback()
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
                conn.commit()
            except Exception:
                # A pooled connection would keep holding the lock; discarding it
                # ends its database session, which releases the lock.
                conn.invalidate()


def _run_forever() -> None:
    time.sleep(_STARTUP_DELAY_SECONDS)
    first_run = True
    while True:
        try:
            stats = index_catalog()
            record_index_run(stats, failed=False)
            if first_run or stats["embedded"] or stats["failed"] or stats["pruned"] \
                    or stats["resynced"]:
                logger.info(
                    "Catalogue index: checked %d, embedded %d, failed %d, pruned %d, "
                    "kept-sold %d, resync %s, caught up %s",
                    stats["checked"], stats["embedded"], stats["failed"], stats["pruned"],
                    stats["kept_sold"], stats["resynced"], stats["caught_up"])
        except BackendUnavailable as exc:
            record_index_run(None, failed=True)
            logger.warning("Catalogue index skipped, backend unavailable: %s", exc)
        except Exception:
            record_index_run(None, failed=True)
            logger.exception("Catalogue indexing failed")
        first_run = False
        time.sleep(INDEX_INTERVAL_SECONDS)


def start_background_indexer() -> None:
    """Start the indexing loop in a daemon thread (a no-op when disabled)."""
    if INDEX_INTERVAL_SECONDS <= 0:
        logger.info("Catalogue indexer disabled (INDEX_INTERVAL_SECONDS=0)")
        return
    threading.Thread(target=_run_forever, name="catalogue-indexer", daemon=True).start()
