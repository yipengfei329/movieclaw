"""add failure tracking fields to site_sync_cursor

Revision ID: b6c4d8e2f915
Revises: a9b3e6d2c754
Create Date: 2026-07-13 18:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6c4d8e2f915'
down_revision: str | None = 'a9b3e6d2c754'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('site_sync_cursor', schema=None) as batch_op:
        # 上次成功同步时间：老数据无法追溯，回填 NULL（=从未成功记录过）即可，
        # 下一次成功同步会自然写入
        batch_op.add_column(sa.Column('last_success_at', sa.DateTime(), nullable=True))
        # server_default 让已有行回填 0（当前没有连续失败）；应用层始终显式赋值
        batch_op.add_column(sa.Column(
            'consecutive_failures', sa.Integer(), nullable=False, server_default='0'
        ))


def downgrade() -> None:
    with op.batch_alter_table('site_sync_cursor', schema=None) as batch_op:
        batch_op.drop_column('consecutive_failures')
        batch_op.drop_column('last_success_at')
