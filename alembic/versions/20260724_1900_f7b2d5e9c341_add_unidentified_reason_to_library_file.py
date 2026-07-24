"""add unidentified_reason to library_file

待识别文件的失败原因：扫描识别链认不出身份时（TMDB 无法访问、片名
解析失败等）把原因落在台账上，前端待识别清单展示给用户——不用翻
后端日志也能知道"为什么认不出"。已识别或人工认领后为 NULL。

Revision ID: f7b2d5e9c341
Revises: e6a9c4d2b718
Create Date: 2026-07-24 19:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7b2d5e9c341"
down_revision: str | None = "e6a9c4d2b718"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("library_file", schema=None) as batch_op:
        batch_op.add_column(sa.Column("unidentified_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("library_file", schema=None) as batch_op:
        batch_op.drop_column("unidentified_reason")
