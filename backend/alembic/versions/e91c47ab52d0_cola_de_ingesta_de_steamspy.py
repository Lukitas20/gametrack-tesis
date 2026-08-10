"""cola de ingesta de steamspy

Revision ID: e91c47ab52d0
Revises: cfdd581cd83f
Create Date: 2026-08-10 00:00:00.000000

Nivel 0/1 de la ingesta masiva: la cola persistente ``steamspy_sync``, la
señal de calidad de SteamSpy en ``games``, los votos comunitarios en
``game_tags`` y la naturaleza (plataforma/comunidad) de cada etiqueta.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e91c47ab52d0'
down_revision: Union[str, None] = 'cfdd581cd83f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'steamspy_sync',
        sa.Column('steam_app_id', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('priority', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('raw', sa.JSON(), nullable=True),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint('steam_app_id'),
    )
    op.create_index(
        op.f('ix_steamspy_sync_priority'), 'steamspy_sync', ['priority'], unique=False
    )
    op.create_index(
        op.f('ix_steamspy_sync_status'), 'steamspy_sync', ['status'], unique=False
    )

    with op.batch_alter_table('games', schema=None) as batch_op:
        batch_op.add_column(sa.Column('steamspy_positive', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('steamspy_negative', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('steamspy_owners', sa.BigInteger(), nullable=True))

    with op.batch_alter_table('game_tags', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('votes', sa.Integer(), server_default='1', nullable=False)
        )

    with op.batch_alter_table('tags', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'kind', sa.String(length=20), server_default='platform', nullable=False
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('tags', schema=None) as batch_op:
        batch_op.drop_column('kind')

    with op.batch_alter_table('game_tags', schema=None) as batch_op:
        batch_op.drop_column('votes')

    with op.batch_alter_table('games', schema=None) as batch_op:
        batch_op.drop_column('steamspy_owners')
        batch_op.drop_column('steamspy_negative')
        batch_op.drop_column('steamspy_positive')

    op.drop_index(op.f('ix_steamspy_sync_status'), table_name='steamspy_sync')
    op.drop_index(op.f('ix_steamspy_sync_priority'), table_name='steamspy_sync')
    op.drop_table('steamspy_sync')
