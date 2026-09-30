"""store embeddings as pgvector columns and track when each was built

Revision ID: b7c1d2e3f4a5
Revises: a1b2c3d4e5f6
Create Date: 2026-09-30

f18c73e9debd created text_vector / image_vector as TEXT, so every
nearest-neighbour query failed: there is no `text <=> vector` operator.
"""
from typing import Sequence, Union
from alembic import op


revision: str = 'b7c1d2e3f4a5'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nothing else creates it, and database.py registers the vector type on
    # every connection — without the extension, every query fails.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Embeddings are derived data that the background indexer rebuilds, so drop
    # them rather than cast TEXT of unknown shape (any bad row would abort the
    # migration, and with it the agent's startup).
    op.execute("DELETE FROM artwork_embeddings")
    op.execute("ALTER TABLE artwork_embeddings "
               "ALTER COLUMN text_vector TYPE vector(3072) USING NULL")
    op.execute("ALTER TABLE artwork_embeddings "
               "ALTER COLUMN image_vector TYPE vector(3072) USING NULL")

    # The product's updatedAt when it was embedded, so edited products are
    # re-embedded instead of staying searchable under their old description.
    op.execute("ALTER TABLE artwork_embeddings "
               "ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE artwork_embeddings DROP COLUMN IF EXISTS source_updated_at")
    op.execute("ALTER TABLE artwork_embeddings "
               "ALTER COLUMN text_vector TYPE TEXT USING text_vector::text")
    op.execute("ALTER TABLE artwork_embeddings "
               "ALTER COLUMN image_vector TYPE TEXT USING image_vector::text")
