from sqlalchemy import text
from app.database import SessionLocal
from app.config import COST_PER_GENERATION
from app.services.visualization_service import _render
from app.services.storage_service import save_image


def run_wall_visualization(generation_id: str) -> None:
    """Background task (RQ worker). Renders the visualization for a queued
    generations row, stores it, and marks the row done. Marks failed on error.
    """
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT * FROM generations WHERE id = :id"),
            {"id": generation_id},
        ).mappings().first()
        if not row:
            return
        row = dict(row)

        db.execute(text("UPDATE generations SET status = 'processing' WHERE id = :id"),
                   {"id": generation_id})
        db.commit()

        watermarked = _render(
            room_url=row["room_image_url"],
            artwork_url=row["artwork_image_url"],
            placement=row.get("placement_hint") or "centered on the main wall",
        )
        _, preview_url = save_image(watermarked, prefix="generations",
                                    filename=f"{generation_id}.jpg")

        db.execute(text("""
            UPDATE generations
            SET status = 'done', preview_url = :url,
                result_object_key = :key, cost = :cost
            WHERE id = :id
        """), {"url": preview_url, "key": f"generations/{generation_id}.jpg",
               "cost": COST_PER_GENERATION, "id": generation_id})
        db.commit()

    except Exception:
        db.execute(text("UPDATE generations SET status = 'failed' WHERE id = :id"),
                   {"id": generation_id})
        db.commit()
        raise
    finally:
        db.close()
