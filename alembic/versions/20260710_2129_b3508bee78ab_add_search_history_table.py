"""add search_history table

Revision ID: b3508bee78ab
Revises: a53c51c366d8
Create Date: 2026-07-10 21:29:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'b3508bee78ab'
down_revision: str | None = 'a53c51c366d8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    with op.batch_alter_table('search_history', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_search_history_keyword'))

    op.drop_table('search_history')
