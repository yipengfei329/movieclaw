"""add poster_mode to search_history

Revision ID: b5c1e8f3a927
Revises: a3f7c2d9e814
Create Date: 2026-07-11 22:30:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5c1e8f3a927'
down_revision: str | None = 'a3f7c2d9e814'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 存量历史行没有记录展示模式，默认列表模式（False），下次搜索时刷新为实际偏好
    with op.batch_alter_table('search_history', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'poster_mode', sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('search_history', schema=None) as batch_op:
        batch_op.drop_column('poster_mode')
