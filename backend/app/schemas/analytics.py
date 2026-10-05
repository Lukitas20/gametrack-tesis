"""Schemas del panel de analítica para desarrolladores."""

from datetime import datetime
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


class GameAnalyticsOut(BaseModel):
    juego: GameHeader
    resenas: ReviewSummary
    aspectos: list[AspectBreakdown]
    punto_debil: str | None
    punto_fuerte: str | None
    # Aspecto -> citas textuales que respaldan la valoración negativa.
    citas_negativas: dict[str, list[str]]


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


class PlatformOverviewOut(BaseModel):
    resenas_analizadas: int
    distribucion: SentimentDistribution
    sentimiento_neto: float
    aspectos: list[AspectBreakdown]


class ProcessResult(BaseModel):
    procesadas: int
    mensaje: str
