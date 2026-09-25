"""Amistades con solicitudes y aceptación explícita.

Revision ID: b621f7389c02
Revises: f4b82d1e6a07
"""

from alembic import op
import sqlalchemy as sa

revision = "b621f7389c02"
down_revision = "f4b82d1e6a07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "friendships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_low_id", sa.Integer(), nullable=False),
        sa.Column("user_high_id", sa.Integer(), nullable=False),
        sa.Column("requester_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("user_low_id < user_high_id", name="ck_friendship_order"),
        sa.CheckConstraint("requester_id = user_low_id OR requester_id = user_high_id", name="ck_friendship_requester"),
        sa.CheckConstraint("status IN ('pending', 'accepted')", name="ck_friendship_status"),
        sa.ForeignKeyConstraint(["user_low_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_high_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requester_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_low_id", "user_high_id", name="uq_friendship_pair"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_friendships_user_low_id", "friendships", ["user_low_id"])
    op.create_index("ix_friendships_user_high_id", "friendships", ["user_high_id"])


def downgrade() -> None:
    op.drop_index("ix_friendships_user_high_id", table_name="friendships")
    op.drop_index("ix_friendships_user_low_id", table_name="friendships")
    op.drop_table("friendships")
