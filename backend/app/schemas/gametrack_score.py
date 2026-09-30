"""Afinidad personal separada de la calidad general y de la propiedad."""
from typing import Literal
from pydantic import BaseModel, Field
from app.schemas.game import GameSummary


class GameTrackScore(BaseModel):
    value: int | None = Field(default=None, ge=0, le=100)
    evidence: Literal["sin_datos", "inicial", "en_desarrollo", "amplia", "valoracion_propia"]
    reasons: list[str]
    version: str = "1.0"
    explanation: str = "Índice estimado de afinidad de 0 a 100; no es una probabilidad ni una nota de calidad."


class DiscoveryItem(BaseModel):
    game: GameSummary
    gametrack_score: GameTrackScore
    reasons: list[str]
    friend_owns: bool = False
    friend_rating: float | None = None
    multiplayer: bool = False


class DiscoveryResponse(BaseModel):
    mode: Literal["affinity", "critics", "friends"]
    items: list[DiscoveryItem]
    note: str
    friend_name: str | None = None
    library_status: str | None = None
    history_size: int
    personal_data: bool
