"""Conservar la identidad pública para reparar nombres de reseñas de Steam."""
from alembic import op
import sqlalchemy as sa

revision = "a2e7c91d6b40"
down_revision = "c6d910a74e32"
branch_labels = None
depends_on = None


def upgrade():
    # Sólo amplía el esquema. Las reseñas históricas conservan sus datos;
    # un identificador desconocido queda NULL hasta observarlo en Steam.
    op.add_column("reviews", sa.Column("steam_author_id", sa.String(32), nullable=True))


def downgrade():
    with op.batch_alter_table("reviews") as batch:
        batch.drop_column("steam_author_id")
