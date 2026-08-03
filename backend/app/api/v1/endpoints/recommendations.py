"""Recomendaciones personalizadas para el rol jugador."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.ml.recommender import recommend_for_user
from app.models import Game, Rating, User
from app.schemas.game import GameSummary
from app.schemas.recommendation import RecommendationOut, RecommendationResponse

router = APIRouter(prefix="/recommendations", tags=["recomendaciones"])


@router.get("", response_model=RecommendationResponse)
def get_recommendations(
    limit: int = Query(default=10, ge=1, le=50),
    strategy: str = Query(
        default="auto",
        pattern="^(auto|contenido|colaborativo|hibrido|popularidad)$",
        description=(
            "'auto' elige según el historial del usuario. Forzar una estrategia "
            "permite comparar los enfoques entre sí."
        ),
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    """Juegos recomendados para el usuario autenticado.

    La respuesta incluye el aporte de cada estrategia y el motivo en lenguaje
    natural, para que la recomendación sea auditable y no una caja negra.
    """
    recommendations = recommend_for_user(db, user, limit=limit, strategy=strategy)

    history_size = (
        db.scalar(select(func.count(Rating.id)).where(Rating.user_id == user.id)) or 0
    )
    games = {
        game.id: game
        for game in db.scalars(
            select(Game).where(Game.id.in_([r.game_id for r in recommendations]))
        )
    }

    return RecommendationResponse(
        strategy=strategy,
        history_size=history_size,
        cold_start=history_size < settings.REC_COLD_START_THRESHOLD,
        items=[
            RecommendationOut(
                game=GameSummary.model_validate(games[r.game_id]),
                score=r.score,
                source=r.source,
                reason=r.reason,
                components=r.components,
            )
            for r in recommendations
            if r.game_id in games
        ],
    )
