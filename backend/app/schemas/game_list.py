"""Schemas de las listas de juegos."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ListType
from app.schemas.game import GameSummary


class GameListItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    game_id: int
    position: int
    note: str | None
    added_at: datetime
    game: GameSummary


class GameListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    list_type: ListType
    is_public: bool
    items: list[GameListItemOut]


class GameListSummary(BaseModel):
    """Vista liviana para los selectores de "guardar en lista"."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    list_type: ListType
    total: int


class GameListCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    is_public: bool = True


class GameListItemCreate(BaseModel):
    game_id: int
    note: str | None = None
