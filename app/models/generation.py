import uuid
from sqlalchemy.orm import mapped_column, Mapped
from sqlalchemy import String, JSON, Numeric, DateTime, func
from app.database import Base


class Generation(Base):
    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    generation_type: Mapped[str] = mapped_column("type", String, nullable=False)
    room_upload_id: Mapped[str] = mapped_column(String, nullable=True)
    room_image_url: Mapped[str] = mapped_column(String, nullable=True)
    artwork_image_url: Mapped[str] = mapped_column(String, nullable=True)
    placement_hint: Mapped[str] = mapped_column(String, nullable=True)
    artwork_ids = mapped_column(JSON, nullable=False, default=list)
    result_object_key: Mapped[str] = mapped_column(String, nullable=True)
    preview_url: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    cost = mapped_column(Numeric(10, 6), nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now())
