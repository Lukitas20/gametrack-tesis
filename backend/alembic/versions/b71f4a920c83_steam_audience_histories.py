"""Historias públicas mínimas y coordinación de consultas de audiencias."""
from alembic import op
import sqlalchemy as sa

revision = "b71f4a920c83"
down_revision = "d8a104f3b927"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("steam_audience_histories",
        sa.Column("steam_id", sa.String(32), primary_key=True),
        sa.Column("checked_at", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False))
    op.create_table("audience_collection_state",
        sa.Column("key", sa.String(150), primary_key=True),
        sa.Column("lease_until", sa.Float(), nullable=False),
        sa.Column("token", sa.String(40), nullable=False),
        sa.Column("identity_checks", sa.JSON(), nullable=False))


def downgrade():
    op.drop_table("audience_collection_state")
    op.drop_table("steam_audience_histories")
