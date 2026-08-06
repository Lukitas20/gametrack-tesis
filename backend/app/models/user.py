"""Modelos de usuario y sus preferencias explícitas."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.types import enum_column
from app.models.enums import UserRole

if TYPE_CHECKING:
    from app.models.game import Game, Genre
    from app.models.game_list import GameList
    from app.models.interaction import Rating, Review


class User(Base):
    """Usuario de la plataforma: jugador o desarrollador."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255))

    full_name: Mapped[str | None] = mapped_column(String(120))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    bio: Mapped[str | None] = mapped_column(Text)

    # --- Cuenta de Steam vinculada ----------------------------------------
    # Permite entrar sin contraseña y, con STEAM_API_KEY configurada, importar
    # la biblioteca del usuario como historial inicial. Es la vía más directa
    # para que una cuenta nueva salga del arranque en frío.
    steam_id: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    steam_username: Mapped[str | None] = mapped_column(String(100))
    steam_avatar_url: Mapped[str | None] = mapped_column(Text)

    role: Mapped[UserRole] = mapped_column(
        enum_column(UserRole), default=UserRole.PLAYER, index=True
    )
    # Solo para el rol desarrollador: estudio al que pertenece. Permite filtrar
    # el dashboard de analítica a los juegos de ese estudio.
    studio: Mapped[str | None] = mapped_column(String(120), index=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    preferences: Mapped[list[UserPreference]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    ratings: Mapped[list[Rating]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    reviews: Mapped[list[Review]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    lists: Mapped[list[GameList]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )

    @property
    def is_developer(self) -> bool:
        return self.role is UserRole.DEVELOPER

    @property
    def genres(self) -> list[Genre]:
        """Géneros elegidos en el onboarding, para mostrarlos en el perfil."""
        return [preference.genre for preference in self.preferences]

    def __repr__(self) -> str:
        return f"<User {self.id} {self.username} ({self.role.value})>"


class UserPreference(Base):
    """Preferencia explícita de un usuario por un género.

    Se completa en el onboarding y alimenta la recomendación basada en
    contenido cuando el usuario todavía no tiene historial suficiente.
    """

    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("user_id", "genre_id", name="uq_user_genre"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    genre_id: Mapped[int] = mapped_column(
        ForeignKey("genres.id", ondelete="CASCADE"), index=True
    )
    # Intensidad de la preferencia en el rango [0, 1].
    weight: Mapped[float] = mapped_column(Float, default=1.0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="preferences")
    genre: Mapped[Genre] = relationship(back_populates="preferred_by")

    def __repr__(self) -> str:
        return f"<UserPreference u={self.user_id} g={self.genre_id} w={self.weight:.2f}>"
