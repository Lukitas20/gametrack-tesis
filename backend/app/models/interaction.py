"""Interacciones usuario-juego: ratings, reseñas y su análisis ABSA."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.types import enum_column
from app.models.enums import Aspect, GameStatus, Sentiment

if TYPE_CHECKING:
    from app.models.game import Game
    from app.models.user import User


class Rating(Base):
    """Puntuación e historial de juego de un usuario sobre un título.

    Es la señal de comportamiento que alimenta la matriz usuario-ítem del
    filtrado colaborativo: además del puntaje explícito guarda las horas
    jugadas y el estado, que funcionan como feedback implícito.
    """

    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint("user_id", "game_id", name="uq_rating_user_game"),
        CheckConstraint("score >= 1 AND score <= 5", name="ck_rating_score_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), index=True
    )

    # Puntaje explícito en escala 1-5 (medias estrellas permitidas).
    score: Mapped[float] = mapped_column(Float)
    hours_played: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[GameStatus] = mapped_column(
        enum_column(GameStatus), default=GameStatus.COMPLETED
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="ratings")
    game: Mapped[Game] = relationship(back_populates="ratings")

    def __repr__(self) -> str:
        return f"<Rating u={self.user_id} g={self.game_id} score={self.score}>"


class Review(Base):
    """Reseña en texto libre escrita por un usuario.

    Los campos de sentimiento quedan nulos hasta que el módulo NLP procesa la
    reseña; ``is_analyzed`` distingue "todavía sin analizar" de "analizada y
    resultó neutra".
    """

    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("user_id", "game_id", name="uq_review_user_game"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nula para reseñas importadas de Steam: no pertenecen a ningún usuario
    # registrado en GameTrack. La constraint única no las choca entre sí
    # porque SQL no compara NULLs como iguales.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), index=True
    )

    title: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(5), default="es")
    # "user" (escrita en GameTrack) o "steam" (importada de una reseña real).
    source: Mapped[str] = mapped_column(String(20), default="user", server_default="user")
    # Nombre a mostrar: el username al momento de publicar, o el nombre de
    # perfil de Steam para las importadas. Se copia acá en vez de resolverse
    # por join porque las reseñas de Steam no tienen ``user`` al que unirse.
    author_name: Mapped[str | None] = mapped_column(String(120))
    # Recomendación binaria estilo Steam, independiente del puntaje.
    is_recommended: Mapped[bool | None] = mapped_column(Boolean)
    hours_at_review: Mapped[float | None] = mapped_column(Float)
    helpful_count: Mapped[int] = mapped_column(Integer, default=0)

    # --- Salida del módulo NLP --------------------------------------------
    sentiment: Mapped[Sentiment | None] = mapped_column(enum_column(Sentiment), index=True)
    # Polaridad global en el rango [-1, 1].
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    sentiment_confidence: Mapped[float | None] = mapped_column(Float)
    is_analyzed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user: Mapped[User | None] = relationship(back_populates="reviews")
    game: Mapped[Game] = relationship(back_populates="reviews")
    aspects: Mapped[list[ReviewAspect]] = relationship(
        back_populates="review", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Review {self.id} u={self.user_id} g={self.game_id}>"


class ReviewAspect(Base):
    """Sentimiento de una reseña hacia un aspecto concreto (salida del ABSA).

    Una reseña genera entre cero y cuatro filas: solo se persisten los
    aspectos efectivamente mencionados en el texto.
    """

    __tablename__ = "review_aspects"
    __table_args__ = (
        UniqueConstraint("review_id", "aspect", name="uq_review_aspect"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), index=True
    )
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), index=True
    )

    aspect: Mapped[Aspect] = mapped_column(enum_column(Aspect), index=True)
    sentiment: Mapped[Sentiment] = mapped_column(enum_column(Sentiment), index=True)
    # Polaridad hacia el aspecto en el rango [-1, 1].
    score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    # Fragmento del texto que justifica la clasificación (explicabilidad).
    evidence: Mapped[str | None] = mapped_column(Text)

    review: Mapped[Review] = relationship(back_populates="aspects")

    def __repr__(self) -> str:
        return f"<ReviewAspect r={self.review_id} {self.aspect.value}={self.sentiment.value}>"
