"""Schemas de ratings y reseñas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Aspect, GameStatus, Sentiment
from app.schemas.game import GameSummary


class RatingCreate(BaseModel):
    game_id: int
    score: float = Field(ge=1, le=5)
    hours_played: float = Field(default=0.0, ge=0)
    status: GameStatus = GameStatus.COMPLETED


class RatingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    game_id: int
    score: float
    hours_played: float
    status: GameStatus
    created_at: datetime


class RatingWithGame(RatingOut):
    game: GameSummary


class ReviewAspectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    aspect: Aspect
    sentiment: Sentiment
    score: float
    confidence: float | None
    evidence: str | None


class ReviewCreate(BaseModel):
    game_id: int
    title: str | None = Field(default=None, max_length=200)
    content: str = Field(min_length=10)
    is_recommended: bool | None = None
    hours_at_review: float | None = Field(default=None, ge=0)


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    game_id: int
    user_id: int | None
    author_name: str | None
    source: str
    title: str | None
    content: str
    is_recommended: bool | None
    helpful_count: int
    created_at: datetime

    sentiment: Sentiment | None
    sentiment_score: float | None
    sentiment_confidence: float | None
    is_analyzed: bool
    aspects: list[ReviewAspectOut]


class TextAnalysisRequest(BaseModel):
    """Texto suelto para analizar sin guardarlo."""

    content: str = Field(min_length=1)


class TextAnalysisOut(BaseModel):
    """Resultado de analizar un texto suelto, sin persistirlo."""

    sentiment: Sentiment
    score: float
    confidence: float
    aspects: list[ReviewAspectOut]
