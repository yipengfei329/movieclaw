"""add extra_models to llm_provider

Revision ID: e5f9c3d8a642
Revises: d4e8b2c7f591
Create Date: 2026-07-13 11:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f9c3d8a642'
down_revision: str | None = 'd4e8b2c7f591'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('llm_provider', schema=None) as batch_op:
        batch_op.add_column(sa.Column('extra_models', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('llm_provider', schema=None) as batch_op:
        batch_op.drop_column('extra_models')
