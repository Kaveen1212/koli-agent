"""Single source of truth for producing a wall/decor visualization.

Both the synchronous agent tool and the asynchronous RQ worker call into here so
behavior (Gemini render → watermark → storage → generations record → cost) is
identical regardless of entry point.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import text
from app.database import SessionLocal
from app.config import (
    MAX_GENERATIONS_PER_DAY, MAX_GLOBAL_GENERATIONS_PER_DAY, COST_PER_GENERATION,
)
from app.services.image_service import generate_wall_visualization
from app.services.watermark import apply_watermark
from app.services.storage_service import save_image


class RateLimitExceeded(Exception):
    pass


def count_today(user_id: str | None = None) -> int:
    """Generations since midnight UTC — for one user, or for everyone when None."""
    params = {"start": datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)}
    sql = "SELECT COUNT(*) AS n FROM generations WHERE created_at >= :start"
    if user_id is not None:
        sql += " AND user_id = :uid"
        params["uid"] = user_id
    db = SessionLocal()
    try:
        row = db.execute(text(sql), params).mappings().first()
        return int(row["n"]) if row else 0
    finally:
        db.close()


def check_daily_limits(user_id: str) -> None:
    """Raise RateLimitExceeded if this user, or the whole site, is at today's cap."""
    if MAX_GENERATIONS_PER_DAY and count_today(user_id) >= MAX_GENERATIONS_PER_DAY:
        raise RateLimitExceeded(
            f"Daily visualization limit of {MAX_GENERATIONS_PER_DAY} reached.")
    if MAX_GLOBAL_GENERATIONS_PER_DAY and count_today() >= MAX_GLOBAL_GENERATIONS_PER_DAY:
        raise RateLimitExceeded(
            "Visualizations are unavailable for the rest of today. Please try again tomorrow.")


def _render(room_url: str, artwork_url: str, placement: str) -> bytes:
    """Generate the composite via Gemini and apply the Artnuss watermark."""
    result = generate_wall_visualization(
        room_image_url=room_url,
        artwork_image_url=artwork_url,
        placement_hint=placement,
    )
    if "error" in result:
        raise RuntimeError(result["error"])
    return apply_watermark(result["image_bytes"])


def visualize_now(user_id: str, room_url: str, artwork_url: str,
                  placement: str, gen_type: str = "wall") -> dict:
    """Synchronous path (used by the agent tool). Enforces the daily limits,
    renders, stores, records the generation, and returns the preview URL.
    """
    check_daily_limits(user_id)

    generation_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.execute(text("""
            INSERT INTO generations
                (id, user_id, type, room_image_url, artwork_image_url,
                 placement_hint, artwork_ids, status)
            VALUES (:id, :uid, :type, :room, :art, :place, :ids, 'processing')
        """), {
            "id": generation_id, "uid": user_id, "type": gen_type,
            "room": room_url, "art": artwork_url, "place": placement,
            "ids": "[]",
        })
        db.commit()

        watermarked = _render(room_url, artwork_url, placement)
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

        return {"generation_id": generation_id, "preview_url": preview_url,
                "label": "AI visualization preview — actual artwork may vary slightly."}

    except Exception:
        db.execute(text("UPDATE generations SET status = 'failed' WHERE id = :id"),
                   {"id": generation_id})
        db.commit()
        raise
    finally:
        db.close()
