from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.db.database import get_db
from app.schemas.game import GameCreate, GameResponse, GameListResponse
from app.services.game_service import get_game, get_games, create_game
from app.api.v1.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/games", tags=["games"])


@router.get("", response_model=GameListResponse)
def list_games(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    genre: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    total, results = get_games(db, page=page, page_size=page_size, search=search, genre=genre)
    return GameListResponse(total=total, page=page, page_size=page_size, results=results)


@router.get("/{game_id}", response_model=GameResponse)
def get_game_detail(game_id: int, db: Session = Depends(get_db)):
    game = get_game(db, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Juego no encontrado")
    return game


@router.post("", response_model=GameResponse, status_code=201)
def create_new_game(
    data: GameCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return create_game(db, data)