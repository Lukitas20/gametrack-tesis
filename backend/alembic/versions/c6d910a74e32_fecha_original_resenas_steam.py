"""Conservar la fecha de publicación de Steam sin inventar fechas históricas."""
from alembic import op
import sqlalchemy as sa

revision = "c6d910a74e32"
down_revision = "b5c208d915af"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("reviews", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_reviews_published_at", "reviews", ["published_at"])


def downgrade():
    op.drop_index("ix_reviews_published_at", table_name="reviews")
    op.drop_column("reviews", "published_at")
