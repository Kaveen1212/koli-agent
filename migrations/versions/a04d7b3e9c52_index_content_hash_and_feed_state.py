"""content hash, sold marker and feed cursor for the change-feed indexer (A04 / K-1)

Revision ID: a04d7b3e9c52
Revises: a01c5e7d9b21
Create Date: 2026-11-04

The indexer used to walk the whole catalogue every pass and re-embed anything
whose `updatedAt` moved, which a sale or a price change does. It now follows the
backend's change feed (GET /v1/internal/products/changes) and only pays for an
embedding when the text or image it reads really changed.

* `artwork_embeddings.content_hash`: sha256 (hex) of exactly what the embedding
  was built from. Equal hash = no Gemini call, whatever the timestamps say.
* `artwork_embeddings.source_content_updated_at`: the backend's `contentUpdatedAt`
  the row was built from (feed rows older than this are ignored).
* `artwork_embeddings.sold_at`: set when a SOLD feed row arrives and
  INDEX_SOLD_GRACE_DAYS > 0. The vector is kept (so a relist costs no embedding)
  but excluded from search; purged once the grace window has passed.
* `index_state`: one row holding the feed cursor and resync schedule.

All additive. Existing rows keep NULLs: the first full resync adopts a hash for
a row whose old `source_updated_at` is still current, and re-embeds the rest.
Nothing is dropped or rewritten on upgrade. The unqualified names resolve to the
`agent` schema through the search_path set in migrations/env.py (A01).
"""
from typing import Sequence, Union
from alembic import op


revision: str = 'a04d7b3e9c52'
down_revision: Union[str, Sequence[str], None] = 'a01c5e7d9b21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Qualified with `agent.` rather than trusting the search_path: a `public`
    # copy of these tables (a pre-A01 image run against this database) must never
    # be the one that gets altered.
    op.execute("""
        ALTER TABLE agent.artwork_embeddings
            ADD COLUMN IF NOT EXISTS content_hash CHAR(64),
            ADD COLUMN IF NOT EXISTS source_content_updated_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS sold_at TIMESTAMPTZ
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_artwork_embeddings_sold_at
            ON agent.artwork_embeddings (sold_at) WHERE sold_at IS NOT NULL
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent.index_state (
            id              SMALLINT PRIMARY KEY CHECK (id = 1),
            feed_cursor     BIGINT,
            last_caught_up_at  TIMESTAMPTZ,
            last_resync_at  TIMESTAMPTZ,
            resync_due_at   TIMESTAMPTZ,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    # The columns are derived data: dropping them loses only the shortcut that
    # avoids re-embedding, never a vector. The pre-A04 indexer re-derives its own
    # state from source_updated_at.
    op.execute("DROP TABLE IF EXISTS agent.index_state")
    op.execute("DROP INDEX IF EXISTS agent.ix_artwork_embeddings_sold_at")
    op.execute("""
        ALTER TABLE agent.artwork_embeddings
            DROP COLUMN IF EXISTS sold_at,
            DROP COLUMN IF EXISTS source_content_updated_at,
            DROP COLUMN IF EXISTS content_hash
    """)
