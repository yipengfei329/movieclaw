"""add download_hint table

Revision ID: b8e5d2c9f437
Revises: a7d4e9c2b361
Create Date: 2026-07-21 14:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e5d2c9f437"
down_revision: str | None = "a7d4e9c2b361"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_hint",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("save_path", sa.Text(), nullable=False),
        sa.Column("subtitle", sa.Text(), nullable=False),
        sa.Column("site_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("download_hint", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_download_hint_save_path"), ["save_path"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("download_hint", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_download_hint_save_path"))

    op.drop_table("download_hint")
