"""Caché del perfil Steam e invitaciones a GameTrack."""
from alembic import op
import sqlalchemy as sa

revision = "bc30e8f12a91"
down_revision = "ab29c8d71e90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("steam_profile_cache",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("steam_id", sa.String(20), nullable=False),
        sa.Column("checked_at", sa.Integer(), nullable=False),
        sa.Column("library", sa.JSON(), nullable=False),
        sa.Column("friends", sa.JSON(), nullable=False))
    op.create_table("friend_invites",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("expires_at", sa.Integer(), nullable=False))


def downgrade():
    op.drop_table("friend_invites")
    op.drop_table("steam_profile_cache")
