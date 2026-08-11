"""mediana de horas de resenadores

Revision ID: f4b82d1e6a07
Revises: e91c47ab52d0
Create Date: 2026-08-10 00:00:00.000000

La duración operativa de un juego finito: mediana de ``hours_at_review`` de
sus reseñas importadas. Reemplaza en la práctica a ``playtime_hours`` (RAWG,
fuente muerta) como dato del filtro "¿cuánto tiempo tenés?" del asistente.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b82d1e6a07'
down_revision: Union[str, None] = 'e91c47ab52d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('games', schema=None) as batch_op:
        batch_op.add_column(sa.Column('median_review_hours', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('games', schema=None) as batch_op:
        batch_op.drop_column('median_review_hours')
