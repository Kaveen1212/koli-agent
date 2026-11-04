import uuid
from fastapi import APIRouter, File, UploadFile, HTTPException, Form, Depends
from sqlalchemy import text
from app.services.storage_service import save_image
from app.database import SessionLocal
from app.security import require_service_token

# Service-token protected: only the backend may reach these paid/Gemini paths.
router = APIRouter(
    prefix="/images",
    tags=["Images"],
    dependencies=[Depends(require_service_token)],
)


# The extension comes from the checked type, never from the client's filename
# (which could say .html or .svg), and the bytes must start like that type.
_IMAGE_TYPES = {
    "image/jpeg": ("jpg", b"\xff\xd8\xff"),
    "image/png": ("png", b"\x89PNG\r\n\x1a\n"),
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.post("/upload")
async def upload_image(file: UploadFile = File(...),
                       user_id: str = Form(None, max_length=128)):
    if file.content_type not in _IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only .jpeg and .png files allowed")
    ext, magic = _IMAGE_TYPES[file.content_type]

    file_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image is larger than 10 MB")
    if not file_bytes.startswith(magic):
        raise HTTPException(status_code=400, detail="File content is not a JPEG or PNG image")
    filename = f"{uuid.uuid4()}.{ext}"

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
