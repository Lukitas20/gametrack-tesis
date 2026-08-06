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
    description: str | None
    publisher: str | None
    platforms: list[str]
    playtime_hours: int | None
    reviews_count: int
    tags: list[TagOut]
    # Para armar el link directo a Steam en la ficha pendiente.
    steam_app_id: int | None


class GamePage(BaseModel):
    """Envoltura de paginación para el listado de juegos."""

    total: int
    limit: int
    offset: int
    items: list[GameSummary]


class HomeSections(BaseModel):
    """Filas curadas de la portada. Sólo juegos con ficha completa."""

    populares: list[GameSummary]
    mejor_valorados: list[GameSummary]
    recientes: list[GameSummary]
    destacados: list[GameSummary]
