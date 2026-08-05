"""Schemas del catálogo: juegos, géneros y etiquetas."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


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
    steam_app_id: int | None
    metadata_status: str
    slug: str
    name: str
    released: date | None
    developer: str | None
    background_image: str | None
    metacritic: int | None
    avg_rating: float
    ratings_count: int
    genres: list[GenreOut]


class GameDetail(GameSummary):
    description: str | None
    publisher: str | None
    platforms: list[str]
    playtime_hours: int | None
    reviews_count: int
    tags: list[TagOut]


class GamePage(BaseModel):
    """Envoltura de paginación para el listado de juegos."""

    total: int
    limit: int
    offset: int
    items: list[GameSummary]
