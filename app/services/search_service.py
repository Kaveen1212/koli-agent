from sqlalchemy import text
from app.database import SessionLocal
from app.services.embedding_service import embed_query

def search_artworks(query: str, category: str = "",
                    price_max: float = 0, limit: int = 5) -> list:
    """
    Semantic search — finds artworks by meaning, not keywords.
    Embeds the query and finds nearest vectors in artwork_embeddings.
    """
    query_vector = embed_query(query)
    # Embed vector as a literal string — safe because values are model-generated floats
    vector_literal = "[" + ",".join(str(v) for v in query_vector) + "]"

    db = SessionLocal()
    try:
        sql = f"""
            SELECT
                p.id, p.title, p.price, p.medium, p.size, p.category, p.images,
                ae.text_vector <=> '{vector_literal}'::vector AS distance
            FROM "Product" p
            JOIN artwork_embeddings ae ON ae.artwork_id = p.id
            WHERE p.status = 'ONLINE'
              AND p."isDeleted" = false
              AND p.stock > 0
        """
        params = {"limit": limit}

        if category:
            sql += ' AND p.category ILIKE :category'
            params["category"] = f"%{category}%"

        if price_max:
            sql += ' AND p.price <= :price_max'
            params["price_max"] = price_max

        sql += ' ORDER BY distance LIMIT :limit'

        rows = db.execute(text(sql), params)
        return [dict(row) for row in rows.mappings().all()]
    finally:
        db.close()

def get_product_by_id(artwork_id: str) -> dict:
    """Direct lookup by ID — O(1), does not load 200 rows."""
    db = SessionLocal()
    try:
        result = db.execute(text("""
            SELECT id, title, description, price, medium, size, category, images
            FROM "Product"
            WHERE id = :artwork_id
              AND "isDeleted" = false
        """), {"artwork_id": artwork_id})
        row = result.mappings().first()
        return dict(row) if row else {}
    finally:
        db.close()
