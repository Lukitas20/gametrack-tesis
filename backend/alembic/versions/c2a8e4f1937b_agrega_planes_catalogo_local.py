"""agrega planes persistentes para el catalogo local

Revision ID: c2a8e4f1937b
Revises: 9f31c8e45a20
Create Date: 2026-08-05 21:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2a8e4f1937b"
down_revision: Union[str, None] = "9f31c8e45a20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "steam_catalog_targets",
        sa.Column("plan_key", sa.String(length=60), nullable=False),
        sa.Column("steam_app_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["steam_app_id"],
            ["steam_catalog_entries.steam_app_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("plan_key", "steam_app_id"),
    )
    op.create_index(
        "ix_steam_catalog_targets_rank", "steam_catalog_targets", ["rank"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_steam_catalog_targets_rank", table_name="steam_catalog_targets"
    )
    op.drop_table("steam_catalog_targets")
