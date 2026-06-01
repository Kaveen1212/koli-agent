from sqlalchemy.orm import mapped_column, Mapped
from sqlalchemy import String, DateTime, func
from pgvector.sqlalchemy import VECTOR
from app.database import Base

class ArtworkEmbedding(Base):
    __tablename__ = "artwork_embeddings"

    artwork_id:   Mapped[str] = mapped_column(String, primary_key=True)
    text_vector:  Mapped[list] = mapped_column(VECTOR(3072), nullable=True)
    image_vector: Mapped[list] = mapped_column(VECTOR(3072), nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now())