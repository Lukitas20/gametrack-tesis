"""Valoraciones y reseñas del usuario autenticado."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.ml.analytics import analyze_text
from app.models import Game, Rating, Review, User
from app.schemas.interaction import (
    RatingCreate,
    RatingWithGame,
    ReviewAspectOut,
    ReviewCreate,
    ReviewOut,
    TextAnalysisOut,
    TextAnalysisRequest,
)
from app.services.interaction_service import (
    create_review,
    list_user_ratings,
    upsert_rating,
)

router = APIRouter(tags=["interacciones"])


def _require_game(db: Session, game_id: int) -> Game:
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="El juego no existe")
    return game


@router.get("/me/ratings", response_model=list[RatingWithGame])
def get_my_ratings(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[Rating]:
    return list_user_ratings(db, user)


@router.post("/ratings", response_model=RatingWithGame, status_code=status.HTTP_201_CREATED)
def create_or_update_rating(
    data: RatingCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Rating:
    """Valora un juego. Si ya estaba valorado, actualiza la nota.

    Al guardar se invalida el modelo de recomendación, así que la próxima
    consulta a `/recommendations` ya refleja el cambio.
    """
    _require_game(db, data.game_id)
    return upsert_rating(db, user, data)


@router.post("/reviews", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
def publish_review(
    data: ReviewCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Review:
    """Publica una reseña. El módulo NLP la analiza en el momento."""
    _require_game(db, data.game_id)
    return create_review(db, user, data)


@router.post("/reviews/analyze", response_model=TextAnalysisOut)
def analyze_free_text(payload: TextAnalysisRequest) -> TextAnalysisOut:
    """Analiza un texto suelto sin guardarlo.

    Sirve para demostrar el módulo NLP en vivo sobre cualquier reseña escrita
    a mano, sin ensuciar la base.
    """
    analysis = analyze_text(payload.content)
    return TextAnalysisOut(
        sentiment=analysis.sentiment,
        score=analysis.score,
        confidence=analysis.confidence,
        aspects=[
            ReviewAspectOut(
                aspect=opinion.aspect,
                sentiment=opinion.sentiment,
                score=opinion.score,
                confidence=opinion.confidence,
                evidence=opinion.evidence,
            )
            for opinion in analysis.aspects
        ],
    )
