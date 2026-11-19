"""Devoluciones propias después de jugar, separadas de estrellas y reseñas."""
from alembic import op
import sqlalchemy as sa
revision='a9d740ec812b'
down_revision='f7a31d908c42'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('play_feedback',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),
        sa.Column('game_id',sa.Integer(),sa.ForeignKey('games.id',ondelete='CASCADE'),nullable=False),
        sa.Column('played',sa.Boolean(),nullable=False),sa.Column('enjoyment',sa.String(20),nullable=False),
        sa.Column('reason',sa.String(20),nullable=False),sa.Column('replay',sa.Boolean()),
        sa.Column('minutes',sa.Integer()),sa.Column('note',sa.String(500)),sa.Column('taste_weight',sa.Float()),
        sa.Column('taste_at',sa.DateTime(timezone=True)),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.UniqueConstraint('user_id','game_id',name='uq_play_feedback_user_game'),
        sa.CheckConstraint('minutes >= 0 AND minutes <= 10080',name='ck_play_feedback_minutes'),
        sa.CheckConstraint('taste_weight >= -1 AND taste_weight <= 1',name='ck_play_feedback_taste'))
    op.create_index('ix_play_feedback_user_id','play_feedback',['user_id'])
    op.create_index('ix_play_feedback_game_id','play_feedback',['game_id'])

def downgrade():
    op.drop_table('play_feedback')
