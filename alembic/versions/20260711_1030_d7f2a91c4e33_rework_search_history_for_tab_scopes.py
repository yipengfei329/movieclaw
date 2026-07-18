"""rework search_history for tab scopes

搜索标签栏支持自定义分类（多分类 × 多站点组合）后，历史表的单一 category
列不再够用。开发阶段无需保留历史数据，直接 drop 重建为新结构：

- label：展示名快照（内置分类的中文名 / 预设名）；NULL = 「全部」
- categories_json：分类组合快照（排序去重后的 JSON 数组）；NULL = 不限分类
- site_ids_json：站点组合快照（排序去重后的 JSON 数组）；NULL = 全部站点

Revision ID: d7f2a91c4e33
Revises: e9a4c7d15b02
Create Date: 2026-07-11 10:30:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd7f2a91c4e33'
down_revision: str | None = 'e9a4c7d15b02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table('search_history')
    op.create_table('search_history',
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('keyword', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('label', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('categories_json', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('site_ids_json', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('search_count', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('search_history', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_search_history_keyword'), ['keyword'], unique=False)


def downgrade() -> None:
    op.drop_table('search_history')
    op.create_table('search_history',
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('keyword', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('category', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('search_count', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('search_history', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_search_history_keyword'), ['keyword'], unique=False)
