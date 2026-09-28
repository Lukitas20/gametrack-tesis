"""Presencia persistida del worker de Steam.

Revision ID: d4e8210ba937
Revises: c917a03e6b24
"""

from alembic import op
import sqlalchemy as sa

revision = "d4e8210ba937"
down_revision = "c917a03e6b24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "steam_catalog_workers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("key_configured", sa.Boolean(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_steam_catalog_workers_heartbeat_at", "steam_catalog_workers", ["heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_steam_catalog_workers_heartbeat_at", table_name="steam_catalog_workers")
    op.drop_table("steam_catalog_workers")
