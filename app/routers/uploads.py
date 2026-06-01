import os
import uuid
from fastapi import APIRouter, File, UploadFile, HTTPException, Form
from sqlalchemy import text
from app.services.storage_service import save_image
from app.database import SessionLocal

router = APIRouter(prefix="/images", tags=["Images"])


@router.post("/upload")
async def upload_image(file: UploadFile = File(...), user_id: str = Form(None)):
    if file.content_type not in ["image/jpeg", "image/png"]:
        raise HTTPException(status_code=400, detail="Only .jpeg and .png files allowed")

    ext = (file.filename or "image.jpg").split(".")[-1].lower()
    filename = f"{uuid.uuid4()}.{ext}"
    file_bytes = await file.read()

    object_key, image_url = save_image(file_bytes, prefix="uploads", filename=filename)

    # Record the upload so visualizations can reference it and analysis can be cached.
    if user_id:
        db = SessionLocal()
        try:
            db.execute(text("""
                INSERT INTO room_uploads (id, user_id, image_object_key)
                VALUES (:id, :uid, :key)
            """), {"id": str(uuid.uuid4()), "uid": user_id, "key": object_key})
            db.commit()
        finally:
            db.close()

    return {"image_url": image_url, "object_key": object_key}
