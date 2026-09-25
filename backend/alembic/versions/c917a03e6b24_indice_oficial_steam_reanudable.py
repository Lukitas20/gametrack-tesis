"""Índice oficial de Steam, checkpoints y cola de enriquecimiento.

Revision ID: c917a03e6b24
Revises: b621f7389c02
"""

from alembic import op
import sqlalchemy as sa

revision = "c917a03e6b24"
down_revision = "b621f7389c02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "steam_catalog_sync",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="idle"),
        sa.Column("cursor_appid", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("since", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scan_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("id = 1", name="ck_steam_catalog_singleton"),
    )
    op.create_table(
        "steam_catalog_entries",
        sa.Column("appid", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("source_modified", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )
    for column in ("status", "next_attempt_at", "priority"):
        op.create_index(f"ix_steam_catalog_entries_{column}", "steam_catalog_entries", [column])


def downgrade() -> None:
    for column in ("priority", "next_attempt_at", "status"):
        op.drop_index(f"ix_steam_catalog_entries_{column}", table_name="steam_catalog_entries")
    op.drop_table("steam_catalog_entries")
    op.drop_table("steam_catalog_sync")
