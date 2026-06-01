"""add agent tables

Revision ID: f18c73e9debd
Revises:
Create Date: 2026-06-01 21:20:25.300638

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f18c73e9debd'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS artwork_embeddings (
            artwork_id  VARCHAR PRIMARY KEY,
            text_vector TEXT,
            image_vector TEXT,
            created_at  TIMESTAMP DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id            VARCHAR PRIMARY KEY,
            user_id       VARCHAR NOT NULL,
            messages_json TEXT NOT NULL DEFAULT '[]',
            created_at    TIMESTAMP DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS room_uploads (
            id               VARCHAR PRIMARY KEY,
            user_id          VARCHAR NOT NULL,
            image_object_key VARCHAR NOT NULL,
            analysis_json    TEXT,
            created_at       TIMESTAMP DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS generations (
            id                VARCHAR PRIMARY KEY,
            user_id           VARCHAR NOT NULL,
            type              VARCHAR NOT NULL,
            room_upload_id    VARCHAR NOT NULL,
            artwork_ids       JSONB NOT NULL DEFAULT '[]',
            result_object_key VARCHAR,
            status            VARCHAR NOT NULL DEFAULT 'queued',
            cost              NUMERIC(10, 6),
            created_at        TIMESTAMP DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.drop_table('generations')
    op.drop_table('room_uploads')
    op.drop_table('chat_sessions')
    op.drop_table('artwork_embeddings')
