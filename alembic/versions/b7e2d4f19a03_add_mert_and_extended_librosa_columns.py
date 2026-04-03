"""add mert embedding and extended librosa feature columns

Revision ID: b7e2d4f19a03
Revises: a3f9c2e81b45
Create Date: 2026-04-03 22:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2d4f19a03"
down_revision: Union[str, Sequence[str], None] = "a3f9c2e81b45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(f"PRAGMA table_info({table})"))
    return any(row[1] == column for row in result)


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    if not _has_column(table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    _add_column_if_missing("segments", sa.Column("mert_embedding", sa.LargeBinary(), nullable=True))
    _add_column_if_missing("segments", sa.Column("mean_mfcc", sa.LargeBinary(), nullable=True))
    _add_column_if_missing("segments", sa.Column("var_mfcc", sa.LargeBinary(), nullable=True))
    _add_column_if_missing("segments", sa.Column("mean_spectral_bandwidth", sa.Float(), nullable=True))
    _add_column_if_missing("segments", sa.Column("var_spectral_bandwidth", sa.Float(), nullable=True))
    _add_column_if_missing("segments", sa.Column("mean_spectral_flatness", sa.Float(), nullable=True))
    _add_column_if_missing("segments", sa.Column("var_spectral_flatness", sa.Float(), nullable=True))
    _add_column_if_missing("segments", sa.Column("mean_spectral_rolloff", sa.Float(), nullable=True))
    _add_column_if_missing("segments", sa.Column("var_spectral_rolloff", sa.Float(), nullable=True))
    _add_column_if_missing("segments", sa.Column("mean_zcr", sa.Float(), nullable=True))
    _add_column_if_missing("segments", sa.Column("var_zcr", sa.Float(), nullable=True))
    _add_column_if_missing("segments", sa.Column("onset_density", sa.Float(), nullable=True))
    _add_column_if_missing("segments", sa.Column("mean_spectral_contrast", sa.LargeBinary(), nullable=True))
    _add_column_if_missing("segments", sa.Column("var_spectral_contrast", sa.LargeBinary(), nullable=True))

    with op.batch_alter_table("processing_log") as batch_op:
        batch_op.drop_constraint("ck_processing_log_step", type_="check")
        batch_op.create_check_constraint(
            "ck_processing_log_step",
            "step IN ('ingest', 'librosa', 'clap', 'coverhunter', 'mert')",
        )


def downgrade() -> None:
    with op.batch_alter_table("processing_log") as batch_op:
        batch_op.drop_constraint("ck_processing_log_step", type_="check")
        batch_op.create_check_constraint(
            "ck_processing_log_step",
            "step IN ('ingest', 'librosa', 'clap', 'coverhunter')",
        )

    op.drop_column("segments", "var_spectral_contrast")
    op.drop_column("segments", "mean_spectral_contrast")
    op.drop_column("segments", "onset_density")
    op.drop_column("segments", "var_zcr")
    op.drop_column("segments", "mean_zcr")
    op.drop_column("segments", "var_spectral_rolloff")
    op.drop_column("segments", "mean_spectral_rolloff")
    op.drop_column("segments", "var_spectral_flatness")
    op.drop_column("segments", "mean_spectral_flatness")
    op.drop_column("segments", "var_spectral_bandwidth")
    op.drop_column("segments", "mean_spectral_bandwidth")
    op.drop_column("segments", "var_mfcc")
    op.drop_column("segments", "mean_mfcc")
    op.drop_column("segments", "mert_embedding")
