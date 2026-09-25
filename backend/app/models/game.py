"""Modelos de catálogo: juegos, géneros y etiquetas."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.game_list import GameListItem
    from app.models.interaction import Rating, Review
    from app.models.user import UserPreference


game_genres = Table(
    "game_genres",
    Base.metadata,
    Column("game_id", ForeignKey("games.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
)

game_tags = Table(
    "game_tags",
    Base.metadata,
    Column("game_id", ForeignKey("games.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    # Votos de la comunidad de Steam para esta etiqueta en este juego (vía
    # SteamSpy). Es la evidencia de "qué tan característico" es el rasgo, no
    # sólo si aplica. 1 para asociaciones sin votos (categorías de la tienda,
    # dataset curado): la membresía sigue valiendo, sin evidencia extra.
    #
    # Se escribe con UPDATE explícito desde ``steamspy_service`` y no a
    # través de ``Game.tags``: la relación ``secondary`` sigue manejando la
    # membresía (y deja el default 1), que es el único uso que tiene el resto
    # del código. Convertirla en association object habría tocado cada
    # lectura de ``game.tags`` para esto solo.
    Column("votes", Integer, nullable=False, server_default="1"),
)


class Genre(Base):
    """Género principal (Acción, RPG, Estrategia, ...)."""

    __tablename__ = "genres"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(60))

    games: Mapped[list[Game]] = relationship(
        secondary=game_genres, back_populates="genres"
    )
    preferred_by: Mapped[list[UserPreference]] = relationship(
        back_populates="genre", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Genre {self.slug}>"


class Tag(Base):
    """Etiqueta descriptiva de grano fino (mundo-abierto, roguelike, ...)."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(60))
    # Naturaleza de la etiqueta, porque cumplen roles distintos:
    #  - "platform": categorías de la tienda de Steam (Un jugador, JcJ,
    #    Cooperativo en línea...). Binarias y curadas por el developer:
    #    los filtros duros de modalidad del asistente.
    #  - "community": etiquetas votadas por usuarios vía SteamSpy (Story
    #    Rich, Souls-like, Relaxing...). La semántica blanda: el corpus del
    #    modelo de contenido y el vocabulario del ánimo.
    kind: Mapped[str] = mapped_column(
        String(20), default="platform", server_default="platform"
    )

    games: Mapped[list[Game]] = relationship(secondary=game_tags, back_populates="tags")

    def __repr__(self) -> str:
        return f"<Tag {self.slug}>"


class Game(Base):
    """Videojuego del catálogo.

    Los campos ``avg_rating``, ``ratings_count`` y ``reviews_count`` están
    desnormalizados a propósito: son los que consulta el fallback por
    popularidad, que debe responder sin recalcular agregados en cada request.
    """

    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ID en RAWG. Nulo para los juegos del dataset local curado.
    external_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    # AppID de Steam. Se guarda aparte de external_id porque un mismo juego
    # puede existir en las dos fuentes con identificadores distintos.
    steam_app_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str | None] = mapped_column(Text)

    released: Mapped[date | None] = mapped_column(Date, index=True)
    developer: Mapped[str | None] = mapped_column(String(120), index=True)
    publisher: Mapped[str | None] = mapped_column(String(120))
    platforms: Mapped[list[str]] = mapped_column(JSON, default=list)

    background_image: Mapped[str | None] = mapped_column(Text)
    metacritic: Mapped[int | None] = mapped_column(Integer)
    # Puntaje de la fuente externa (RAWG), escala 0-5.
    external_rating: Mapped[float | None] = mapped_column(Float)
    # Horas típicas según RAWG. Fuente muerta en la práctica (la API está
    # caída hace meses y nunca llegó a poblarse): se conserva por si revive,
    # pero la duración operativa es ``median_review_hours``.
    playtime_hours: Mapped[int | None] = mapped_column(Integer)
    # Mediana de horas jugadas por los reseñadores de Steam al momento de
    # reseñar (``Review.hours_at_review``). Orienta el compromiso acumulado,
    # pero no mide duración de campaña ni de sesión. Para juegos-servicio
    # el filtro de tiempo no interpreta esa acumulación como duración
    # (ver ``app.ml.quiz_vocab.is_session_based``). Nula con menos de
    # MIN_HOURS_SAMPLES muestras: una mediana de 2 reseñas no significa nada.
    median_review_hours: Mapped[float | None] = mapped_column(Float)

    # Agregados calculados sobre los ratings internos de GameTrack.
    avg_rating: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    ratings_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    reviews_count: Mapped[int] = mapped_column(Integer, default=0)
    # avg_rating penalizado por incertidumbre (avg - 1/sqrt(evidencia)): lo
    # que se ordena como "mejor valorados", para que dos reseñas de 5
    # estrellas no le ganen a cien reseñas de 4,8. avg_rating se deja intacto
    # porque es lo que se muestra en la ficha ("4,8 ★"), no lo que se ordena.
    popularity_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)

    # Última vez que se refrescó la ficha desde Steam (ver
    # ``steam_service.maybe_refresh``). Nula para juegos que no vienen de
    # Steam o que todavía no se sincronizaron ni una vez.
    steam_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Totales REALES de reseñas que declara Steam, no las que llegamos a
    # importar (el import está capado en STEAM_REVIEWS_IMPORT_LIMIT). Sin
    # esto, `ratings_count` mide el tamaño de nuestra muestra y no la
    # popularidad: un indie con 556 reseñas y CS2 con 9,7 millones se veían
    # igual de confiables, y el de la muestra más positiva ganaba siempre.
    steam_total_reviews: Mapped[int | None] = mapped_column(Integer)
    steam_positive_reviews: Mapped[int | None] = mapped_column(Integer)

    # Señal masiva de SteamSpy (nivel 0/1 de la ingesta). Se guarda aparte de
    # ``steam_total_reviews``/``steam_positive_reviews`` porque la fuente y la
    # cadencia difieren: aquéllos vienen del endpoint de reseñas de la tienda
    # al refrescar UNA ficha; éstos llegan en lote para TODO el catálogo, aun
    # para juegos que nadie abrió nunca. ``steamspy_owners`` es el punto medio
    # de la banda de dueños estimados ("100,000,000 .. 200,000,000").
    steamspy_positive: Mapped[int | None] = mapped_column(Integer)
    steamspy_negative: Mapped[int | None] = mapped_column(Integer)
    steamspy_owners: Mapped[int | None] = mapped_column(BigInteger)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    genres: Mapped[list[Genre]] = relationship(
        secondary=game_genres, back_populates="games", lazy="selectin"
    )
    tags: Mapped[list[Tag]] = relationship(
        secondary=game_tags, back_populates="games", lazy="selectin"
    )
    ratings: Mapped[list[Rating]] = relationship(
        back_populates="game", cascade="all, delete-orphan"
    )
    reviews: Mapped[list[Review]] = relationship(
        back_populates="game", cascade="all, delete-orphan"
    )
    list_items: Mapped[list[GameListItem]] = relationship(
        back_populates="game", cascade="all, delete-orphan"
    )

    @property
    def is_enriched(self) -> bool:
        """``False`` sólo para una "ficha pendiente" de Steam: un juego que
        entró al catálogo por su índice completo de AppIDs (ver
        ``steam_service.get_app_list``) pero todavía no tiene géneros,
        descripción ni reseñas. Se completa sola la primera vez que alguien
        la abre (``steam_service.maybe_refresh``). Los juegos que no vienen
        de Steam (dataset curado, RAWG) siempre están enriquecidos.
        """
        return self.steam_app_id is None or self.steam_synced_at is not None

    @property
    def content_soup(self) -> str:
        """Texto plano que consume el vectorizador TF-IDF.

        Géneros y etiquetas se repiten para que pesen más que la descripción,
        que aporta muchos términos poco discriminantes.
        """
        genres = " ".join(g.slug for g in self.genres)
        tags = " ".join(t.slug for t in self.tags)
        developer = (self.developer or "").lower().replace(" ", "-")
        return " ".join(
            [genres, genres, genres, tags, tags, developer, self.description or ""]
        ).strip()

    def __repr__(self) -> str:
        return f"<Game {self.id} {self.name}>"
