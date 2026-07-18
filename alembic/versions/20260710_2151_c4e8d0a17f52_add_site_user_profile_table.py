"""add site_user_profile table

Revision ID: c4e8d0a17f52
Revises: b3508bee78ab
Create Date: 2026-07-10 21:51:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c4e8d0a17f52'
down_revision: str | None = 'b3508bee78ab'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('site_user_profile',
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('username', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_class', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('uploaded_bytes', sa.Integer(), nullable=False),
    sa.Column('downloaded_bytes', sa.Integer(), nullable=False),
    sa.Column('ratio', sa.Float(), nullable=True),
    sa.Column('bonus', sa.Float(), nullable=True),
    sa.Column('seeding_count', sa.Integer(), nullable=False),
    sa.Column('leeching_count', sa.Integer(), nullable=False),
    sa.Column('avatar_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('join_date', sa.DateTime(), nullable=True),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('site_user_profile', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_site_user_profile_site_id'), ['site_id'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('site_user_profile', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_site_user_profile_site_id'))

    op.drop_table('site_user_profile')
