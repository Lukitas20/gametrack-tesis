"""Afinidad y respaldo público: componentes auditables de GameTrackScore."""
from typing import Literal
from pydantic import BaseModel, Field
from app.schemas.game import GameSummary


class GameTrackScore(BaseModel):
    value: int | None = Field(default=None, ge=0, le=100)
    evidence: Literal["sin_datos", "inicial", "en_desarrollo", "amplia", "valoracion_propia"]
    reasons: list[str]
    version: str = "1.1"
    affinity: int | None = Field(default=None, ge=0, le=100)
    metascore: int | None = Field(default=None, ge=0, le=100)
    community: int | None = Field(default=None, ge=0, le=100)
    review_count: int = Field(default=0, ge=0)
    components: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    explanation: str = "Índice de 0 a 100 que combina tus gustos, la crítica y respaldo público. Es una estimación, no una probabilidad."


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
