import uuid
from sqlalchemy.orm import mapped_column, Mapped
from sqlalchemy import String, Text, DateTime, func
from app.database import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id:            Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id:       Mapped[str] = mapped_column(String, nullable=False)
    messages_json: Mapped[str] = mapped_column(Text,   nullable=False, default="[]")
    created_at                 = mapped_column(DateTime, server_default=func.now())
    updated_at                 = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
