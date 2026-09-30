"""Keeps artwork_embeddings in step with the sellable catalogue.

Products used to become searchable only when someone remembered to run
scripts/ingest_embeddings.py. Now a background thread (start_background_indexer)
walks the catalogue through the backend API every INDEX_INTERVAL_SECONDS and
embeds anything new, or edited since it was last embedded.
"""
import logging
import threading
import time
from datetime import datetime

from sqlalchemy import text

from app.config import INDEX_INTERVAL_SECONDS, INDEX_MAX_PER_RUN
from app.database import engine
from app.services.backend_client import catalogue_page, absolute_media_url, BackendUnavailable
from app.services.embedding_service import embed_artwork_text, embed_artwork_image

logger = logging.getLogger(__name__)

# Postgres advisory-lock key, so two agent processes never index at once.
_LOCK_KEY = 7_470_201
_PAGE_SIZE = 100
# Give the backend time to come up when the whole stack restarts together.
_STARTUP_DELAY_SECONDS = 30


def _vec(values) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


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


def _needs_embedding(product: dict, known: dict) -> bool:
    if product["id"] not in known:
        return True
    embedded_from = known[product["id"]]
    updated = _parse_ts(product.get("updatedAt"))
    return updated is not None and (embedded_from is None or embedded_from < updated)


def index_catalog(max_embeds: int = INDEX_MAX_PER_RUN) -> dict:
    """Embed sellable products that have no embedding yet, or changed since.

    One bad product is logged and counted, never fatal. Raises
    BackendUnavailable if the catalogue can't be fetched at all.
    """
    stats = {"checked": 0, "embedded": 0, "failed": 0}
    with engine.connect() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}).scalar():
            return {**stats, "skipped": "another indexer holds the lock"}
        try:
            known = dict(conn.execute(text(
                "SELECT artwork_id, source_updated_at FROM artwork_embeddings")).all())
            conn.commit()

            after = None
            while stats["embedded"] < max_embeds:
                page = catalogue_page(after=after, limit=_PAGE_SIZE)
                for product in page:
                    stats["checked"] += 1
                    if not _needs_embedding(product, known):
                        continue
                    if stats["embedded"] >= max_embeds:
                        break
                    try:
                        text_vec, image_vec = _embed(product)
                        conn.execute(text("""
                            INSERT INTO artwork_embeddings
                                (artwork_id, text_vector, image_vector, source_updated_at)
                            VALUES (:id, CAST(:tv AS vector), CAST(:iv AS vector), :upd)
                            ON CONFLICT (artwork_id) DO UPDATE
                            SET text_vector = EXCLUDED.text_vector,
                                image_vector = EXCLUDED.image_vector,
                                source_updated_at = EXCLUDED.source_updated_at
                        """), {"id": product["id"], "tv": text_vec, "iv": image_vec,
                               "upd": _parse_ts(product.get("updatedAt"))})
                        conn.commit()
                        stats["embedded"] += 1
                    except Exception:
                        conn.rollback()
                        stats["failed"] += 1
                        logger.exception("Embedding failed for product %s", product.get("id"))
                if len(page) < _PAGE_SIZE:
                    break
                after = page[-1]["id"]
        finally:
            try:
                conn.rollback()
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
                conn.commit()
            except Exception:
                # A pooled connection would keep holding the lock; discarding it
                # ends its database session, which releases the lock.
                conn.invalidate()
    return stats


def _run_forever() -> None:
    time.sleep(_STARTUP_DELAY_SECONDS)
    first_run = True
    while True:
        try:
            stats = index_catalog()
            if first_run or stats["embedded"] or stats["failed"]:
                logger.info("Catalogue index: checked %d, embedded %d, failed %d",
                            stats["checked"], stats["embedded"], stats["failed"])
        except BackendUnavailable as exc:
            logger.warning("Catalogue index skipped, backend unavailable: %s", exc)
        except Exception:
            logger.exception("Catalogue indexing failed")
        first_run = False
        time.sleep(INDEX_INTERVAL_SECONDS)


def start_background_indexer() -> None:
    """Start the indexing loop in a daemon thread (a no-op when disabled)."""
    if INDEX_INTERVAL_SECONDS <= 0:
        logger.info("Catalogue indexer disabled (INDEX_INTERVAL_SECONDS=0)")
        return
    threading.Thread(target=_run_forever, name="catalogue-indexer", daemon=True).start()
