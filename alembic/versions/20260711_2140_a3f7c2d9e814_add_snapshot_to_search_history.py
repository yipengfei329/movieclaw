"""add result snapshot columns to search_history

Revision ID: a3f7c2d9e814
Revises: f2c8a5d7b310
Create Date: 2026-07-11 21:40:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f7c2d9e814'
down_revision: str | None = 'f2c8a5d7b310'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 结果快照两列均可空：存量历史行没有快照，重新搜索后自动补上
    with op.batch_alter_table('search_history', schema=None) as batch_op:
        batch_op.add_column(sa.Column('snapshot_json', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('snapshot_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('search_history', schema=None) as batch_op:
        batch_op.drop_column('snapshot_at')
        batch_op.drop_column('snapshot_json')
