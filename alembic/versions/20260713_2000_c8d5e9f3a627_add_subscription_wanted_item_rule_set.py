"""add subscription, wanted_item and rule_set

Revision ID: c8d5e9f3a627
Revises: b6c4d8e2f915
Create Date: 2026-07-13 20:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c8d5e9f3a627'
down_revision: str | None = 'b6c4d8e2f915'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('rule_set',
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('spec', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('rule_set', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_rule_set_name'), ['name'], unique=True)

    op.create_table('subscription',
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('media_item_id', sa.Integer(), nullable=False),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('selected_seasons', sa.JSON(), nullable=False),
    sa.Column('follow_future', sa.Boolean(), nullable=False),
    sa.Column('rule_set_id', sa.Integer(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.ForeignKeyConstraint(['media_item_id'], ['media_item.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['rule_set_id'], ['rule_set.id']),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('media_item_id', name='uq_subscription_media_item')
    )
    with op.batch_alter_table('subscription', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_subscription_media_item_id'), ['media_item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_subscription_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_subscription_rule_set_id'), ['rule_set_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_subscription_status'), ['status'], unique=False)

    op.create_table('wanted_item',
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('subscription_id', sa.Integer(), nullable=False),
    sa.Column('media_item_id', sa.Integer(), nullable=False),
    sa.Column('season_number', sa.Integer(), nullable=False),
    sa.Column('episode_number', sa.Integer(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('air_date', sa.Date(), nullable=True),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('next_search_at', sa.DateTime(), nullable=True),
    sa.Column('search_attempts', sa.Integer(), nullable=False),
    sa.Column('last_search_at', sa.DateTime(), nullable=True),
    sa.Column('grabbed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscription.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['media_item_id'], ['media_item.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('subscription_id', 'season_number', 'episode_number', name='uq_wanted_sub_season_episode')
    )
    with op.batch_alter_table('wanted_item', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_wanted_item_subscription_id'), ['subscription_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_wanted_item_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_wanted_item_next_search_at'), ['next_search_at'], unique=False)
        batch_op.create_index('ix_wanted_media_status', ['media_item_id', 'status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('wanted_item', schema=None) as batch_op:
        batch_op.drop_index('ix_wanted_media_status')
        batch_op.drop_index(batch_op.f('ix_wanted_item_next_search_at'))
        batch_op.drop_index(batch_op.f('ix_wanted_item_status'))
        batch_op.drop_index(batch_op.f('ix_wanted_item_subscription_id'))

    op.drop_table('wanted_item')

    with op.batch_alter_table('subscription', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_subscription_status'))
        batch_op.drop_index(batch_op.f('ix_subscription_rule_set_id'))
        batch_op.drop_index(batch_op.f('ix_subscription_kind'))
        batch_op.drop_index(batch_op.f('ix_subscription_media_item_id'))

    op.drop_table('subscription')

    with op.batch_alter_table('rule_set', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rule_set_name'))

    op.drop_table('rule_set')
