"""Experiencias de jugador y estudio en una misma cuenta."""
from alembic import op
import sqlalchemy as sa

revision = "d8a104f3b927"
down_revision = "a2e7c91d6b40"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("developer_enabled", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("onboarding_step", sa.String(20), nullable=True))
        batch.add_column(sa.Column("analysis_game_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_users_analysis_game", "games", ["analysis_game_id"], ["id"], ondelete="SET NULL")
    op.execute(sa.text("UPDATE users SET developer_enabled = true WHERE role = 'desarrollador'"))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_analysis_game", type_="foreignkey")
        batch.drop_column("analysis_game_id")
        batch.drop_column("onboarding_step")
        batch.drop_column("developer_enabled")
