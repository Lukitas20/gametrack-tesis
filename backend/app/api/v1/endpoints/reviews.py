from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.schemas.review import ReviewCreate, ReviewResponse
from app.services.review_service import (
    get_review, get_reviews_by_game, get_reviews_by_user,
    get_user_review_for_game, create_review, update_review, delete_review
)
from app.services.game_service import get_game, update_game_rating
from app.api.v1.deps import get_current_user
from app.models.user import User
from typing import List

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("/game/{game_id}", response_model=List[ReviewResponse])
def list_game_reviews(
    game_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    game = get_game(db, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Juego no encontrado")
    _, results = get_reviews_by_game(db, game_id, page, page_size)
    return results


@router.get("/user/{user_id}", response_model=List[ReviewResponse])
def list_user_reviews(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    _, results = get_reviews_by_user(db, user_id, page, page_size)
    return results


@router.post("", response_model=ReviewResponse, status_code=201)
def create_new_review(
    data: ReviewCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    game = get_game(db, data.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Juego no encontrado")

    existing = get_user_review_for_game(db, current_user.id, data.game_id)
    if existing:
        raise HTTPException(status_code=400, detail="Ya escribiste una reseña para este juego")

    review = create_review(db, current_user.id, data)
    update_game_rating(db, data.game_id)
    return review


@router.put("/{review_id}", response_model=ReviewResponse)
def update_existing_review(
    review_id: int,
    data: ReviewCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    review = get_review(db, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Reseña no encontrada")
    if review.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="No tenés permiso para editar esta reseña")

    updated = update_review(db, review, data.model_dump(exclude_unset=True))
    update_game_rating(db, review.game_id)
    return updated


@router.delete("/{review_id}", status_code=204)
def delete_existing_review(
    review_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    review = get_review(db, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Reseña no encontrada")
    if review.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="No tenés permiso para eliminar esta reseña")

    delete_review(db, review)
    update_game_rating(db, review.game_id)