"""Schemas del motor de recomendación."""

from pydantic import BaseModel, Field

from app.models.enums import RecommendationSource
from app.schemas.game import GameSummary


class RecommendationOut(BaseModel):
    game: GameSummary
    score: float
    source: RecommendationSource
    reason: str
    # Aportes ponderados en escala 0–1: su suma es score. No son probabilidades.
    components: dict[str, float]
    signals: list[str] = Field(default_factory=list)


class RecommendationResponse(BaseModel):
    strategy: str
    effective_strategy: str = "popularidad"
    discovery: str = "balanced"
    profile_hint: str = ""
    local_model: dict = Field(default_factory=dict)
    # Cantidad de juegos valorados por el usuario: explica por qué se eligió
    # esa estrategia y si hubo arranque en frío.
    history_size: int
    cold_start: bool
    items: list[RecommendationOut]
