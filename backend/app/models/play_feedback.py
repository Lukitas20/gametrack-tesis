"""Última devolución de una experiencia; separa gusto de interrupciones."""
from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base


class PlayFeedback(Base):
    __tablename__ = 'play_feedback'
    __table_args__ = (
        UniqueConstraint('user_id','game_id',name='uq_play_feedback_user_game'),
        CheckConstraint('minutes >= 0 AND minutes <= 10080',name='ck_play_feedback_minutes'),
        CheckConstraint('taste_weight >= -1 AND taste_weight <= 1',name='ck_play_feedback_taste'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    game_id: Mapped[int] = mapped_column(ForeignKey('games.id',ondelete='CASCADE'),index=True)
    played: Mapped[bool] = mapped_column(Boolean)
    enjoyment: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(20))
    replay: Mapped[bool | None] = mapped_column(Boolean)
    minutes: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(500))
    # Una incidencia no borra la última señal explícita de gusto personal.
    taste_weight: Mapped[float | None] = mapped_column(Float)
    taste_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now())
