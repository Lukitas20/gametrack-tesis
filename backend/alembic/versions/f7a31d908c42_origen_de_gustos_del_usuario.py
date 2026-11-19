"""Distingue gustos inferidos de Steam de elecciones manuales."""
from alembic import op
import sqlalchemy as sa

revision = "f7a31d908c42"
down_revision = "e8c2a19f307b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("preferences_source", sa.String(20), nullable=True))
    op.execute("UPDATE users SET preferences_source = 'manual' WHERE EXISTS "
               "(SELECT 1 FROM user_preferences WHERE user_preferences.user_id = users.id)")


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("preferences_source")
