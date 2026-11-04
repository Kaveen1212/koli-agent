from sqlalchemy.orm import mapped_column, Mapped
from sqlalchemy import CHAR, Index, String, DateTime, func, text
from pgvector.sqlalchemy import VECTOR
from app.database import Base

class ArtworkEmbedding(Base):
    __tablename__ = "artwork_embeddings"

    __table_args__ = (
        Index("ix_artwork_embeddings_sold_at", "sold_at",
              postgresql_where=text("sold_at IS NOT NULL")),
    )

    artwork_id:   Mapped[str] = mapped_column(String, primary_key=True)
    text_vector:  Mapped[list] = mapped_column(VECTOR(3072), nullable=True)
    image_vector: Mapped[list] = mapped_column(VECTOR(3072), nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now())
    # Legacy (b7c1d2e3f4a5): the product's updatedAt when embedded.
    source_updated_at = mapped_column(DateTime(timezone=True), nullable=True)
    # A04: what the embedding was built from, and when (see services/indexer.py).
    content_hash = mapped_column(CHAR(64), nullable=True)
    source_content_updated_at = mapped_column(DateTime(timezone=True), nullable=True)
    sold_at = mapped_column(DateTime(timezone=True), nullable=True)
