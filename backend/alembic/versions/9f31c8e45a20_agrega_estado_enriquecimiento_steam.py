"""agrega estado del enriquecimiento diferido de Steam

Revision ID: 9f31c8e45a20
Revises: 67c91f7a4f21
Create Date: 2026-08-05 19:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9f31c8e45a20"
down_revision: Union[str, None] = "67c91f7a4f21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "steam_enrichment_states",
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("reviews_imported", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("game_id"),
    )
    op.create_index(
        "ix_steam_enrichment_states_status",
        "steam_enrichment_states",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_steam_enrichment_states_status",
        table_name="steam_enrichment_states",
    )
    op.drop_table("steam_enrichment_states")
