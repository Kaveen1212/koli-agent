from sqlalchemy import BigInteger, DateTime, SmallInteger, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IndexState(Base):
    """One row (id = 1): where the indexer is in the backend's change feed."""
    __tablename__ = "index_state"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    feed_cursor: Mapped[int] = mapped_column(BigInteger, nullable=True)
    last_caught_up_at = mapped_column(DateTime(timezone=True), nullable=True)
    last_resync_at = mapped_column(DateTime(timezone=True), nullable=True)
    resync_due_at = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
