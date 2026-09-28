"""Identidades Steam verificadas y estados de autenticación de un solo uso."""
from alembic import op
import sqlalchemy as sa

revision = "ab29c8d71e90"
down_revision = "c917a03e6b24"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("steam_identities",
        sa.Column("steam_id", sa.String(20), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True))
    op.create_table("steam_auth_flows",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("expires_at", sa.Integer(), nullable=False))


def downgrade():
    op.drop_table("steam_auth_flows")
    op.drop_table("steam_identities")
