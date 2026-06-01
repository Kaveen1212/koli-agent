"""
Populate artwork_embeddings (text + image vectors) for all products.
Run once after seeding, and again whenever new products are added.
Usage: uv run python scripts/ingest_embeddings.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from app.database import SessionLocal
from app.services.embedding_service import embed_artwork_text, embed_artwork_image


def _vec(values) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def ingest():
    db = SessionLocal()
    try:
        products = db.execute(text("""
            SELECT p.id, p.title, p.description, p.category, p.medium, p.images
            FROM "Product" p
            LEFT JOIN artwork_embeddings ae ON ae.artwork_id = p.id
            WHERE ae.artwork_id IS NULL
              AND p."isDeleted" = false
        """)).mappings().all()

        print(f"Found {len(products)} products without embeddings.")

        for i, p in enumerate(products):
            title = p["title"] or ""
            description = f"{p.get('description', '')} {p.get('category', '')} {p.get('medium', '')}".strip()
            images = p.get("images") or []

            try:
                text_vec = _vec(embed_artwork_text(title, description))

                # Image vector is optional — only if the product has an image.
                image_vec = None
                if images:
                    try:
                        image_vec = _vec(embed_artwork_image(images[0]))
                    except Exception as ie:
                        print(f"    image embed skipped for {title}: {ie}")

                if image_vec:
                    db.execute(text(f"""
                        INSERT INTO artwork_embeddings (artwork_id, text_vector, image_vector)
                        VALUES (:id, '{text_vec}'::vector, '{image_vec}'::vector)
                        ON CONFLICT (artwork_id) DO UPDATE
                        SET text_vector = EXCLUDED.text_vector,
                            image_vector = EXCLUDED.image_vector
                    """), {"id": p["id"]})
                else:
                    db.execute(text(f"""
                        INSERT INTO artwork_embeddings (artwork_id, text_vector)
                        VALUES (:id, '{text_vec}'::vector)
                        ON CONFLICT (artwork_id) DO UPDATE
                        SET text_vector = EXCLUDED.text_vector
                    """), {"id": p["id"]})

                db.commit()
                print(f"[{i+1}/{len(products)}] Embedded: {title}")

            except Exception as e:
                print(f"[{i+1}/{len(products)}] FAILED: {title} — {e}")
                db.rollback()

        print("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    ingest()
