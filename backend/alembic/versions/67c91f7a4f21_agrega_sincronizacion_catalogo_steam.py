"""agrega sincronizacion progresiva del catalogo de Steam

Revision ID: 67c91f7a4f21
Revises: 26e1ee0668f9
Create Date: 2026-08-05 17:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "67c91f7a4f21"
down_revision: Union[str, None] = "26e1ee0668f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "steam_sync_states",
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("last_appid", sa.Integer(), server_default="0", nullable=False),
        sa.Column("if_modified_since", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "steam_catalog_entries",
        sa.Column("steam_app_id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("last_modified", sa.Integer(), nullable=True),
        sa.Column("metadata_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("steam_app_id"),
        sa.UniqueConstraint("game_id"),
    )
    op.create_index("ix_steam_catalog_entries_game_id", "steam_catalog_entries", ["game_id"])
    op.create_index("ix_steam_catalog_entries_last_modified", "steam_catalog_entries", ["last_modified"])
    op.create_index("ix_steam_catalog_entries_metadata_status", "steam_catalog_entries", ["metadata_status"])


def downgrade() -> None:
    op.drop_index("ix_steam_catalog_entries_metadata_status", table_name="steam_catalog_entries")
    op.drop_index("ix_steam_catalog_entries_last_modified", table_name="steam_catalog_entries")
    op.drop_index("ix_steam_catalog_entries_game_id", table_name="steam_catalog_entries")
    op.drop_table("steam_catalog_entries")
    op.drop_table("steam_sync_states")
