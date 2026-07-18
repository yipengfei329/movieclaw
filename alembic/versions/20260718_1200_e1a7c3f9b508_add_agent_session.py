"""add agent_session

Revision ID: e1a7c3f9b508
Revises: d9e6f4a8b213
Create Date: 2026-07-18 12:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'e1a7c3f9b508'
down_revision: str | None = 'd9e6f4a8b213'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('agent_session',
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('last_prompt', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('entry_count', sa.Integer(), nullable=False),
    sa.Column('leaf_uuid', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('active_run_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('last_heartbeat_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('agent_session')
