from sqlalchemy.orm import mapped_column, Mapped
from sqlalchemy import String, Text, DateTime, func
from app.database import Base
import uuid


class RoomUpload(Base):
    __tablename__ = "room_uploads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    image_object_key: Mapped[str] = mapped_column(String, nullable=False)
    analysis_json: Mapped[str] = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now())
