"""add coverhunter, content_type, and song_match_candidates

Revision ID: a3f9c2e81b45
Revises: 210ed4a1b667
Create Date: 2026-03-24 22:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3f9c2e81b45"
down_revision: Union[str, Sequence[str], None] = "210ed4a1b667"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("recordings", sa.Column("coverhunter_embedding", sa.LargeBinary(), nullable=True))
    op.add_column("recordings", sa.Column("content_type", sa.String(16), nullable=True))
    op.add_column("recordings", sa.Column("content_type_source", sa.String(16), nullable=True))

    op.create_table(
        "song_match_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recording_id", sa.Integer(), sa.ForeignKey("recordings.id"), nullable=False, index=True),
        sa.Column("song_id", sa.Integer(), sa.ForeignKey("songs.id"), nullable=False, index=True),
        sa.Column("nearest_recording_id", sa.Integer(), sa.ForeignKey("recordings.id"), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="ck_song_match_candidates_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("song_match_candidates")
    op.drop_column("recordings", "content_type_source")
    op.drop_column("recordings", "content_type")
    op.drop_column("recordings", "coverhunter_embedding")
