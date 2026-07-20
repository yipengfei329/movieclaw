"""add library_file table and wanted_item pipeline fields

Revision ID: a7d4e9c2b361
Revises: f3a9c1d7e582
Create Date: 2026-07-19 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d4e9c2b361"
down_revision: str | None = "f3a9c1d7e582"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_file",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("library_id", sa.Integer(), nullable=False),
        sa.Column("media_item_id", sa.Integer(), nullable=True),
        sa.Column("season_number", sa.Integer(), nullable=False),
        sa.Column("episode_number", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("container", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("resolution", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("video_codec", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("hdr", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("bit_depth", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("bit_rate", sa.Integer(), nullable=True),
        sa.Column("media_source", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("release_group", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("site_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("torrent_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("missing_since", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["library_id"], ["library.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["media_item_id"], ["media_item.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("library_file", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_library_file_file_path"), ["file_path"], unique=True)
        batch_op.create_index(
            batch_op.f("ix_library_file_library_id"), ["library_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_library_file_source"), ["source"], unique=False)
        batch_op.create_index(
            "ix_library_file_media_unit",
            ["media_item_id", "season_number", "episode_number"],
            unique=False,
        )

    with op.batch_alter_table("wanted_item", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("info_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(sa.Column("downloaded_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("imported_at", sa.DateTime(), nullable=True))
        batch_op.create_index(batch_op.f("ix_wanted_item_info_hash"), ["info_hash"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("wanted_item", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_wanted_item_info_hash"))
        batch_op.drop_column("imported_at")
        batch_op.drop_column("downloaded_at")
        batch_op.drop_column("info_hash")

    with op.batch_alter_table("library_file", schema=None) as batch_op:
        batch_op.drop_index("ix_library_file_media_unit")
        batch_op.drop_index(batch_op.f("ix_library_file_source"))
        batch_op.drop_index(batch_op.f("ix_library_file_library_id"))
        batch_op.drop_index(batch_op.f("ix_library_file_file_path"))

    op.drop_table("library_file")
