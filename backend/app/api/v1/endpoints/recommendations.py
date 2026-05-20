from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.api.v1.deps import get_current_user
from app.models.user import User
from app.services.recommendation_service import (
    get_content_based_recommendations,
    get_hybrid_recommendations,
)
from typing import List

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/similar/{game_id}")
def similar_games(
    game_id: int,
    n: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
):
    recs = get_content_based_recommendations(db, game_id, n=n)
    if not recs:
        raise HTTPException(status_code=404, detail="No se encontraron recomendaciones")
    return recs


@router.get("/for-me")
def my_recommendations(
    n: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    recs = get_hybrid_recommendations(db, current_user.id, n=n)
    if not recs:
        raise HTTPException(status_code=404, detail="No hay suficientes datos para generar recomendaciones")
    return recs