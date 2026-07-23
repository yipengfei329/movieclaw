"""add import_watch table, migrate and drop library.ingest_dirs

Revision ID: d8b3f5c1a624
Revises: c4f7a2e8d915
Create Date: 2026-07-22 20:00:00.000000

"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8b3f5c1a624"
down_revision: str | None = "c4f7a2e8d915"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 监听导入独立成模块：源目录 → 目标库 的搬运规则，脱离库配置存在
    op.create_table(
        "import_watch",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("strategy", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("library_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["library.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("import_watch", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_import_watch_source_path"), ["source_path"], unique=True
        )
        batch_op.create_index(batch_op.f("ix_import_watch_library_id"), ["library_id"])

    # 存量配置平移：library.ingest_dirs JSON → import_watch 行（用户配置无损）
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, ingest_dirs FROM library")).fetchall()
    for library_id, raw in rows:
        try:
            dirs = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except (TypeError, ValueError):
            continue
        for cfg in dirs:
            path = str(cfg.get("path", "")).strip()
            strategy = str(cfg.get("strategy", "")).strip()
            if not path or strategy not in ("hardlink", "copy"):
                continue
            conn.execute(
                sa.text(
                    "INSERT OR IGNORE INTO import_watch "
                    "(created_at, updated_at, source_path, strategy, library_id) "
                    "VALUES (datetime('now'), datetime('now'), :path, :strategy, :library_id)"
                ).bindparams(path=path, strategy=strategy, library_id=library_id)
            )

    with op.batch_alter_table("library", schema=None) as batch_op:
        batch_op.drop_column("ingest_dirs")


def downgrade() -> None:
    with op.batch_alter_table("library", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("ingest_dirs", sa.JSON(), nullable=False, server_default="[]")
        )
    with op.batch_alter_table("import_watch", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_import_watch_library_id"))
        batch_op.drop_index(batch_op.f("ix_import_watch_source_path"))
    op.drop_table("import_watch")
