"""add library table and subscription.library_id

Revision ID: f3a9c1d7e582
Revises: c5e2a8d4f176
Create Date: 2026-07-18 22:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a9c1d7e582"
down_revision: str | None = "c5e2a8d4f176"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library",
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("root_paths", sa.JSON(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("library", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_library_kind"), ["kind"], unique=False)
        batch_op.create_index(batch_op.f("ix_library_name"), ["name"], unique=True)

    with op.batch_alter_table("subscription", schema=None) as batch_op:
        batch_op.add_column(sa.Column("library_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_subscription_library_id"), ["library_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_subscription_library_id",
            "library",
            ["library_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("subscription", schema=None) as batch_op:
        batch_op.drop_constraint("fk_subscription_library_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_subscription_library_id"))
        batch_op.drop_column("library_id")

    with op.batch_alter_table("library", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_library_name"))
        batch_op.drop_index(batch_op.f("ix_library_kind"))

    op.drop_table("library")
