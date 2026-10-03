"""Afinidad y respaldo público: componentes auditables de GameTrackScore."""
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.game import GameSummary


class GameTrackScore(BaseModel):
    value: int | None = Field(default=None, ge=0, le=100)
    evidence: Literal["sin_datos", "inicial", "en_desarrollo", "amplia", "valoracion_propia", "steam", "prelanzamiento"]
    preliminary: bool = False
    reasons: list[str]
    version: str = "2.0"
    affinity: int | None = Field(default=None, ge=0, le=100)
    metascore: int | None = Field(default=None, ge=0, le=100)
    community: int | None = Field(default=None, ge=0, le=100)
    review_count: int = Field(default=0, ge=0)
    components: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    explanation: str = "Índice de 0 a 100: tu afinidad aporta al menos 85%; la crítica y la recepción pública, hasta 15%. Usa tus opiniones y los rasgos de tus juegos; las horas son una señal de interés, no una nota. Es una estimación, no una probabilidad."


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


class ExplanationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)


class ExplanationPoint(BaseModel):
    text: str
    reference_ids: list[str] = Field(default_factory=list)


class ExplanationReference(BaseModel):
    id: str
    title: str
    url: str
    detail: str


class GameExplanation(BaseModel):
    game_id: int
    game_name: str
    score: GameTrackScore
    summary: str
    positives: list[ExplanationPoint]
    cautions: list[ExplanationPoint]
    answer: list[ExplanationPoint]
    references: list[ExplanationReference]
    suggested_questions: list[str]
    engine: Literal["local"] = "local"
    method: str
