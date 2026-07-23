"""add downloader path_mappings

下载器路径映射：movieclaw 与下载器不在同一容器/主机时，同一块盘两边
路径不同，提交下载前需要把 movieclaw 视角的保存目录翻译成下载器视角。
NULL = 两边视角一致（存量配置行为不变）。

Revision ID: e6a9c4d2b718
Revises: d8b3f5c1a624
Create Date: 2026-07-24 10:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6a9c4d2b718"
down_revision: str | None = "d8b3f5c1a624"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("downloader_client", schema=None) as batch_op:
        batch_op.add_column(sa.Column("path_mappings", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("downloader_client", schema=None) as batch_op:
        batch_op.drop_column("path_mappings")
