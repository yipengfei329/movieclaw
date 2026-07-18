"""add media_item and media_season

Revision ID: a9b3e6d2c754
Revises: e5f9c3d8a642
Create Date: 2026-07-13 14:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'a9b3e6d2c754'
down_revision: str | None = 'e5f9c3d8a642'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('media_item',
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('tmdb_id', sa.Integer(), nullable=False),
    sa.Column('imdb_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('douban_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('original_title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('year', sa.Integer(), nullable=True),
    sa.Column('aliases', sa.JSON(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('poster_path', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('backdrop_path', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('metadata_refreshed_at', sa.DateTime(), nullable=True),
    sa.Column('next_refresh_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kind', 'tmdb_id', name='uq_media_item_kind_tmdb')
    )
    with op.batch_alter_table('media_item', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_media_item_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_media_item_imdb_id'), ['imdb_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_media_item_douban_id'), ['douban_id'], unique=False)

    op.create_table('media_season',
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('media_item_id', sa.Integer(), nullable=False),
    sa.Column('season_number', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('air_date', sa.Date(), nullable=True),
    sa.Column('episode_count', sa.Integer(), nullable=True),
    sa.Column('episodes', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['media_item_id'], ['media_item.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('media_item_id', 'season_number', name='uq_media_season_item_season')
    )
    with op.batch_alter_table('media_season', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_media_season_media_item_id'), ['media_item_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('media_season', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_media_season_media_item_id'))

    op.drop_table('media_season')

    with op.batch_alter_table('media_item', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_media_item_douban_id'))
        batch_op.drop_index(batch_op.f('ix_media_item_imdb_id'))
        batch_op.drop_index(batch_op.f('ix_media_item_kind'))

    op.drop_table('media_item')
