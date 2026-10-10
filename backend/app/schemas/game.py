"""Schemas del catálogo: juegos, géneros y etiquetas."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.services.steam_media import sanitize_screenshots


class GenreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str


class GameSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    released: date | None
    developer: str | None
    background_image: str | None
    metacritic: int | None
    avg_rating: float
    ratings_count: int
    genres: list[GenreOut]
    # False para una "ficha pendiente" (stub de Steam sin enriquecer todavía).
    is_enriched: bool


class GameDetail(GameSummary):
    screenshots: list[str] = Field(default_factory=list)

    @field_validator("screenshots", mode="before")
    @classmethod
    def empty_screenshots(cls, value):
        return sanitize_screenshots(value)

    description: str | None
    publisher: str | None
    platforms: list[str]
    playtime_hours: int | None
    reviews_count: int
    tags: list[TagOut]
    # Para armar el link directo a Steam en la ficha pendiente.
    steam_app_id: int | None
    steam_sync_status: str | None = None


class GameMedia(BaseModel):
    screenshots: list[str] = Field(default_factory=list)
    status: Literal["available", "empty", "pending", "unavailable"]


class GamePage(BaseModel):
    """Envoltura de paginación para el listado de juegos."""

    total: int
    limit: int
    offset: int
    items: list[GameSummary]


class HomeCollection(BaseModel):
    key: str
    title: str
    caption: str
    slug: str
    count: int
    games: list[GameSummary]


class HomeSections(BaseModel):
    """Filas curadas de la portada. Sólo juegos con ficha completa."""

    populares: list[GameSummary]
    mejor_valorados: list[GameSummary]
    recientes: list[GameSummary]
    destacados: list[GameSummary]
    critica: list[GameSummary] = Field(default_factory=list)
    colecciones: list[HomeCollection] = Field(default_factory=list)


class UpcomingGame(BaseModel):
    steam_app_id: int
    name: str
    background_image: str
    release_label: str
    store_url: str


class UpcomingReleases(BaseModel):
    status: str
    source: str
    updated_at: int | None
    items: list[UpcomingGame]
