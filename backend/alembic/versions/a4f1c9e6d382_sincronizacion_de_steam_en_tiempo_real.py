"""sincronización de Steam en tiempo real

Agrega lo necesario para refrescar un juego importado de Steam (ficha,
géneros/etiquetas y reseñas nuevas) sin duplicar lo ya traído: la marca de
tiempo del último sincronizado en ``games`` y el identificador de la reseña
de origen (``recommendationid``) en ``reviews``, contra el que se filtran
las reseñas ya importadas antes de insertar. Ver
``app/services/steam_service.py``.

Revision ID: a4f1c9e6d382
Revises: 26e1ee0668f9
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4f1c9e6d382'
down_revision: Union[str, None] = '26e1ee0668f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('games', schema=None) as batch_op:
        batch_op.add_column(sa.Column('steam_synced_at', sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table('reviews', schema=None) as batch_op:
        batch_op.add_column(sa.Column('steam_review_id', sa.String(length=32), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_reviews_steam_review_id'), ['steam_review_id'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('reviews', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reviews_steam_review_id'))
        batch_op.drop_column('steam_review_id')

    with op.batch_alter_table('games', schema=None) as batch_op:
        batch_op.drop_column('steam_synced_at')
