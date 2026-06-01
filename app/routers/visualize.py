from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import uuid
from sqlalchemy import text
from app.database import SessionLocal
from app.worker.queue import get_queue
from app.worker.tasks import run_wall_visualization
from app.config import MAX_GENERATIONS_PER_DAY
from app.services.visualization_service import count_today

router = APIRouter(prefix="/visualize", tags=["visualize"])


class VisualizeRequest(BaseModel):
    room_image_url:  str
    artwork_image_url: str
    artwork_id:      str
    user_id:         str
    placement:       str = "centered on the main wall"


@router.post("/wall")
async def visualize_on_wall(request: VisualizeRequest):
    """Enqueue a wall visualization job. Returns immediately with a generation_id.
    Poll GET /visualize/status/{generation_id} to check when it's done.
    """
    # Daily rate-limit guard (runaway-cost protection).
    if MAX_GENERATIONS_PER_DAY and count_today(request.user_id) >= MAX_GENERATIONS_PER_DAY:
        raise HTTPException(status_code=429,
                            detail=f"Daily visualization limit of {MAX_GENERATIONS_PER_DAY} reached.")

    generation_id = str(uuid.uuid4())

    db = SessionLocal()
    try:
        db.execute(text("""
            INSERT INTO generations
                (id, user_id, type, room_image_url, artwork_image_url,
                 placement_hint, artwork_ids, status)
            VALUES (:id, :uid, 'wall', :room, :art, :place, :ids, 'queued')
        """), {
            "id":    generation_id,
            "uid":   request.user_id,
            "room":  request.room_image_url,
            "art":   request.artwork_image_url,
            "place": request.placement,
            "ids":   f'["{request.artwork_id}"]',
        })
        db.commit()
    finally:
        db.close()

    get_queue().enqueue(run_wall_visualization, generation_id)
    return {"generation_id": generation_id, "status": "queued"}


@router.get("/status/{generation_id}")
async def get_status(generation_id: str):
    """Check the status of a visualization job."""
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT id, status, result_object_key, cost FROM generations WHERE id = :id"),
            {"id": generation_id}
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Generation not found")
        return dict(row)
    finally:
        db.close()
