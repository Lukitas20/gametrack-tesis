"""Capturas oficiales de Steam para la galería de cada juego."""
from alembic import op
import sqlalchemy as sa

revision = "b5c208d915af"
down_revision = "a9d740ec812b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("games", sa.Column("screenshots", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("games", "screenshots")
