"""add subscription_activity

Revision ID: d9e6f4a8b213
Revises: c8d5e9f3a627
Create Date: 2026-07-13 22:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'd9e6f4a8b213'
down_revision: str | None = 'c8d5e9f3a627'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('subscription_activity',
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('subscription_id', sa.Integer(), nullable=False),
    sa.Column('wanted_item_id', sa.Integer(), nullable=True),
    sa.Column('type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('message', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscription.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['wanted_item_id'], ['wanted_item.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('subscription_activity', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_subscription_activity_subscription_id'),
            ['subscription_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_subscription_activity_type'), ['type'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('subscription_activity', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_subscription_activity_type'))
        batch_op.drop_index(batch_op.f('ix_subscription_activity_subscription_id'))

    op.drop_table('subscription_activity')
