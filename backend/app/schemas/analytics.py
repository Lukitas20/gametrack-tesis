"""Schemas del panel de analítica para desarrolladores."""

from datetime import datetime
from typing import Any
from pydantic import BaseModel
from app.schemas.interaction import ReviewOut


class DeveloperReviewOut(ReviewOut):
    game_name: str
    publication_date: datetime | None


class DeveloperReviewPage(BaseModel):
    items: list[DeveloperReviewOut]
    total: int
    limit: int
    offset: int
    undated: int


class SentimentDistribution(BaseModel):
    positivo: int = 0
    neutro: int = 0
    negativo: int = 0


class AspectBreakdown(BaseModel):
    aspecto: str
    menciones: int
    distribucion: SentimentDistribution
    # Proporción de menciones positivas menos negativas, en [-1, 1].
    sentimiento_neto: float
    score_promedio: float


class ReviewSummary(BaseModel):
    analizadas: int
    pendientes: int = 0
    distribucion: SentimentDistribution
    sentimiento_neto: float


class GameHeader(BaseModel):
    id: int
    nombre: str
    slug: str
    desarrollador: str | None
    background_image: str | None = None
    steam_app_id: int | None = None
    rating_local_promedio: float | None = None
    cantidad_ratings_local: int = 0
    # Índice combinado del catálogo; no representa sólo votos de GameTrack.
    rating_promedio: float
    cantidad_ratings: int


class TrainedReviewEvidence(BaseModel):
    review_id: int
    game_id: int
    game: str
    quote: str
    recommended: bool | None
    source: str


class TrainedReviewTopic(BaseModel):
    topic: str
    label: str
    mentions: int
    not_recommended: int
    recommended: int
    without_recommendation: int
    examples: list[TrainedReviewEvidence]


class TrainedReviewReport(BaseModel):
    model: dict[str, Any]
    scope: dict[str, Any]
    language: str
    sample_order: str
    sampled: int
    total: int
    topics: list[TrainedReviewTopic]
    reception: dict[str, int]
    limitations: list[str]
    used: bool = False
    predicted: int = 0


class AudienceGroup(BaseModel):
    key: str
    label: str
    size: int | None
    suppressed: bool
    criterion: str
    denominator: int
    source: str
    unit: str


class SteamTasteGame(BaseModel):
    appid: int
    game_id: int
    name: str
    authors: int


class SteamTasteReception(BaseModel):
    recommended: int
    not_recommended: int
    mixed: int
    unknown: int


class SteamTasteGroup(BaseModel):
    key: str
    label: str
    kind: str
    size: int
    denominator: int
    criterion: str
    reception: SteamTasteReception
    examples: list[SteamTasteGame]


class SteamHistoryReport(BaseModel):
    identified_reviews: int
    identified_authors: int
    sample_limit: int
    selected_authors: int
    checked_authors: int
    public_histories: int
    private_histories: int
    unavailable_histories: int
    partial_histories: int
    pending_authors: int
    identity_games_pending: int
    authors_with_two_other_recommendations: int
    other_recommendations: int
    recommendations_with_metadata: int
    oldest_checked_at: str | None
    newest_checked_at: str | None
    page_limit: int
    cache_hours: int
    groups: list[SteamTasteGroup]
    selection: str
    source: str


class AudienceReport(BaseModel):
    scope: dict[str, Any]
    games: int
    gametrack_users: int
    steam_reviews: int
    coverage: dict[str, int]
    groups: list[AudienceGroup]
    minimum_group: int
    limitations: list[str]
    steam_histories: SteamHistoryReport | None = None


class GameAnalyticsOut(BaseModel):
    juego: GameHeader
    resenas: ReviewSummary
    aspectos: list[AspectBreakdown]
    punto_debil: str | None
    punto_fuerte: str | None
    # Aspecto -> citas textuales que respaldan la valoración negativa.
    citas_negativas: dict[str, list[str]]
    trained_ai: TrainedReviewReport | None = None
    audience: AudienceReport | None = None


class StudioGameRow(BaseModel):
    id: int
    nombre: str
    background_image: str | None = None
    steam_app_id: int | None = None
    rating_local_promedio: float | None = None
    cantidad_ratings_local: int = 0
    # Índice combinado del catálogo; no representa sólo votos de GameTrack.
    rating_promedio: float
    cantidad_ratings: int = 0
    cantidad_resenas: int
    resenas_analizadas: int
    distribucion: SentimentDistribution
    sentimiento_neto: float


class StudioAnalyticsOut(BaseModel):
    estudio: str
    juegos: list[StudioGameRow]
    resenas: ReviewSummary
    aspectos: list[AspectBreakdown]
    citas_negativas: dict[str, list[str]] = {}
    trained_ai: TrainedReviewReport | None = None
    audience: AudienceReport | None = None


class PlatformOverviewOut(BaseModel):
    resenas_analizadas: int
    distribucion: SentimentDistribution
    sentimiento_neto: float
    aspectos: list[AspectBreakdown]


class ProcessResult(BaseModel):
    procesadas: int
    mensaje: str
