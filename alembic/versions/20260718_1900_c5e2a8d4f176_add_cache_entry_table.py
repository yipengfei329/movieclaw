"""add cache_entry table

Revision ID: c5e2a8d4f176
Revises: e1a7c3f9b508
Create Date: 2026-07-18 19:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c5e2a8d4f176'
down_revision: str | None = 'e1a7c3f9b508'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('cache_entry',
    sa.Column('namespace', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('cache_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('payload', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('namespace', 'cache_key')
    )
    with op.batch_alter_table('cache_entry', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_cache_entry_fetched_at'), ['fetched_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('cache_entry', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_cache_entry_fetched_at'))

    op.drop_table('cache_entry')
