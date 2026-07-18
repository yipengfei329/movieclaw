"""add vertical to search_history

Revision ID: c7d2f9a4e186
Revises: b5c1e8f3a927
Create Date: 2026-07-12 10:30:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d2f9a4e186'
down_revision: str | None = 'b5c1e8f3a927'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 存量历史行都是站点资源搜索（媒体搜索此前不落历史），默认 torrent
    with op.batch_alter_table('search_history', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'vertical', sa.String(), nullable=False, server_default='torrent'
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('search_history', schema=None) as batch_op:
        batch_op.drop_column('vertical')
