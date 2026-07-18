"""add is_default to downloader_client

Revision ID: f2c8a5d7b310
Revises: e8b3d6f4a921
Create Date: 2026-07-11 17:20:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2c8a5d7b310'
down_revision: str | None = 'e8b3d6f4a921'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('downloader_client', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false())
        )
    # 存量数据回填：把最早添加的一台设为默认，满足"有下载器就有默认"的不变量
    op.execute(
        "UPDATE downloader_client SET is_default = 1 "
        "WHERE id = (SELECT MIN(id) FROM downloader_client)"
    )


def downgrade() -> None:
    with op.batch_alter_table('downloader_client', schema=None) as batch_op:
        batch_op.drop_column('is_default')
