"""fix generations columns for visualization worker

Revision ID: a1b2c3d4e5f6
Revises: f18c73e9debd
Create Date: 2026-06-02

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f18c73e9debd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Columns the worker and sync tool actually read/write.
    op.execute('ALTER TABLE generations ADD COLUMN IF NOT EXISTS room_image_url    VARCHAR')
    op.execute('ALTER TABLE generations ADD COLUMN IF NOT EXISTS artwork_image_url VARCHAR')
    op.execute('ALTER TABLE generations ADD COLUMN IF NOT EXISTS placement_hint    VARCHAR')
    op.execute('ALTER TABLE generations ADD COLUMN IF NOT EXISTS preview_url       VARCHAR')
    # room_upload_id is no longer required (we store the URL directly).
    op.execute('ALTER TABLE generations ALTER COLUMN room_upload_id DROP NOT NULL')

    # chat_sessions.updated_at is used by the history UPSERT.
    op.execute('ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()')


def downgrade() -> None:
    op.execute('ALTER TABLE generations DROP COLUMN IF EXISTS room_image_url')
    op.execute('ALTER TABLE generations DROP COLUMN IF EXISTS artwork_image_url')
    op.execute('ALTER TABLE generations DROP COLUMN IF EXISTS placement_hint')
    op.execute('ALTER TABLE generations DROP COLUMN IF EXISTS preview_url')
