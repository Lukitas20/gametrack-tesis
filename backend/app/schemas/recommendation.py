"""Schemas del motor de recomendación."""

from pydantic import BaseModel, Field

from app.models.enums import RecommendationSource
from app.schemas.game import GameSummary


class RecommendationOut(BaseModel):
    game: GameSummary
    score: float
    source: RecommendationSource
    reason: str
    components: dict[str, float]
    signals: list[str] = Field(default_factory=list)


class RecommendationResponse(BaseModel):
    strategy: str
    effective_strategy: str = "popularidad"
    discovery: str = "balanced"
    profile_hint: str = ""
    local_model: dict = Field(default_factory=dict)
    affinity_model: dict = Field(default_factory=dict)
    history_size: int
    cold_start: bool
    items: list[RecommendationOut]
