"""Semantic search over the artwork catalogue.

Split of responsibilities:
  - the vector index (artwork_embeddings) is the agent's own table, queried here
  - product facts come from the backend API, never from its tables

Previously this JOINed Prisma's "Product" table directly, which coupled the two
services through the database schema and would silently break on any backend
migration.
"""
from sqlalchemy import text

from app.database import SessionLocal
from app.services.embedding_service import embed_query
from app.services.backend_client import products_by_ids, lookup_products, BackendUnavailable


# Over-fetch from the vector index because some nearest neighbours will have
# been delisted or sold out; the backend drops those, and we still want `limit`
# results back.
_CANDIDATE_MULTIPLIER = 4


def _nearest_ids(query_vector: list[float], limit: int) -> list[str]:
    """Nearest artwork ids from the local vector index, closest first."""
    vector_literal = "[" + ",".join(str(v) for v in query_vector) + "]"

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT artwork_id
                FROM artwork_embeddings
                ORDER BY text_vector <=> CAST(:vec AS vector)
                LIMIT :limit
                """
            ),
            {"vec": vector_literal, "limit": limit},
        )
        return [row["artwork_id"] for row in rows.mappings().all()]
    finally:
        db.close()


def search_artworks(query: str, category: str = "",
                    price_max: float = 0, limit: int = 5) -> list:
    """Find artworks by meaning — 'calm earthy tones' works, not just keywords."""
    try:
        query_vector = embed_query(query)
    except Exception:
        # Embedding failed — fall back to a plain category lookup rather than
        # returning nothing at all.
        return _fallback(category, limit)

    candidate_ids = _nearest_ids(query_vector, limit * _CANDIDATE_MULTIPLIER)
    if not candidate_ids:
        return _fallback(category, limit)

    try:
        products = products_by_ids(candidate_ids)
    except BackendUnavailable:
        return []

    # Filters the vector index can't apply — it stores no price or category.
    if category:
        needle = category.lower()
        products = [p for p in products if needle in (p.get("category") or "").lower()]
    if price_max:
        products = [p for p in products if float(p.get("price") or 0) <= price_max]

    return products[:limit]


def _fallback(category: str, limit: int) -> list:
    try:
        return lookup_products(category=category, limit=limit)
    except BackendUnavailable:
        return []


def get_product_by_id(artwork_id: str) -> dict:
    """Direct lookup by id. Returns {} when the artwork is no longer sellable."""
    try:
        rows = products_by_ids([artwork_id])
    except BackendUnavailable:
        return {}
    return rows[0] if rows else {}
