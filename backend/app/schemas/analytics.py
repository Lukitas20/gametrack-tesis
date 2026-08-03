"""Schemas del panel de analítica para desarrolladores."""

from pydantic import BaseModel


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
    rating_promedio: float
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
