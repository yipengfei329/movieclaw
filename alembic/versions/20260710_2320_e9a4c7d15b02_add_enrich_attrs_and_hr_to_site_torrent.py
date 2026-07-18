"""add enrich attrs and hr to site_torrent

新增三列，均可空、无需回填（NULL 有明确语义）：
- attrs: 数据扩充层（movieclaw_enrich）产出的结构化属性 JSON；NULL=尚未扩充
- enrich_version: 产出 attrs 的提取器版本；应用启动时据此重算过期行
- hit_and_run: H&R 考核标记三态；NULL=站点不提供/未适配

Revision ID: e9a4c7d15b02
Revises: c4e8d0a17f52
Create Date: 2026-07-10 23:20:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9a4c7d15b02'
down_revision: str | None = 'c4e8d0a17f52'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('site_torrent', sa.Column('hit_and_run', sa.Boolean(), nullable=True))
    op.add_column('site_torrent', sa.Column('attrs', sa.JSON(), nullable=True))
    op.add_column('site_torrent', sa.Column('enrich_version', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('site_torrent', schema=None) as batch_op:
        batch_op.drop_column('enrich_version')
        batch_op.drop_column('attrs')
        batch_op.drop_column('hit_and_run')
