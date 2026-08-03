"""Schemas del motor de recomendación."""

from pydantic import BaseModel

from app.models.enums import RecommendationSource
from app.schemas.game import GameSummary


class RecommendationOut(BaseModel):
    game: GameSummary
    score: float
    source: RecommendationSource
    reason: str
    # Aporte de cada estrategia, para poder auditar la combinación.
    components: dict[str, float]


class RecommendationResponse(BaseModel):
    strategy: str
    # Cantidad de juegos valorados por el usuario: explica por qué se eligió
    # esa estrategia y si hubo arranque en frío.
    history_size: int
    cold_start: bool
    items: list[RecommendationOut]
