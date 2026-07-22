"""add library.ingest_dirs and ingest_entry table

Revision ID: c4f7a2e8d915
Revises: b8e5d2c9f437
Create Date: 2026-07-22 16:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f7a2e8d915"
down_revision: str | None = "b8e5d2c9f437"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 库级下载监听目录配置：[{"path": "...", "strategy": "hardlink"|"copy"}]
    with op.batch_alter_table("library", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("ingest_dirs", sa.JSON(), nullable=False, server_default="[]")
        )

    # 监听目录条目的处理台账：防止重复导入、失败可追溯可重试
    op.create_table(
        "ingest_entry",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("library_id", sa.Integer(), nullable=False),
        sa.Column("entry_path", sa.Text(), nullable=False),
        sa.Column("fingerprint", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("imported_count", sa.Integer(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["library.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ingest_entry", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ingest_entry_library_id"), ["library_id"])
        batch_op.create_index(batch_op.f("ix_ingest_entry_entry_path"), ["entry_path"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("ingest_entry", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ingest_entry_entry_path"))
        batch_op.drop_index(batch_op.f("ix_ingest_entry_library_id"))
    op.drop_table("ingest_entry")

    with op.batch_alter_table("library", schema=None) as batch_op:
        batch_op.drop_column("ingest_dirs")
