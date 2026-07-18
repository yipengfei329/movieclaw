"""add downloader_client table

Revision ID: e8b3d6f4a921
Revises: d7f2a91c4e33
Create Date: 2026-07-11 15:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'e8b3d6f4a921'
down_revision: str | None = 'd7f2a91c4e33'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('downloader_client',
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('client_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('username', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('password', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('save_path', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('last_error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('last_checked_at', sa.DateTime(), nullable=True),
    sa.Column('version', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('downloader_client', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_downloader_client_name'), ['name'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('downloader_client', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_downloader_client_name'))

    op.drop_table('downloader_client')
