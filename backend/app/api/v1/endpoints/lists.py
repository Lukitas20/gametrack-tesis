"""Listas de juegos del usuario autenticado."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models import Game, GameList, User
from app.schemas.game_list import (
    GameListCreate,
    GameListItemCreate,
    GameListOut,
    GameListSummary,
)
from app.services import list_service

router = APIRouter(tags=["listas"])


def _require_list(db: Session, user: User, list_id: int) -> GameList:
    game_list = list_service.get_user_list(db, user, list_id)
    if game_list is None:
        raise HTTPException(status_code=404, detail="La lista no existe")
    return game_list


@router.get("/me/lists", response_model=list[GameListOut])
def get_my_lists(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[GameList]:
    """Listas del usuario, creando las del sistema si faltaran."""
    return list_service.list_user_lists(db, user)


@router.get("/me/lists/summary", response_model=list[GameListSummary])
def get_my_lists_summary(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[GameListSummary]:
    """Versión liviana para el selector de "guardar en lista"."""
    return [
        GameListSummary(
            id=game_list.id,
            name=game_list.name,
            list_type=game_list.list_type,
            total=len(game_list.items),
        )
        for game_list in list_service.list_user_lists(db, user)
    ]


@router.get("/me/lists/containing/{game_id}", response_model=list[int])
def get_lists_containing(
    game_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[int]:
    """Ids de las listas que ya contienen el juego, para marcar los botones."""
    return list_service.lists_containing(db, user, game_id)


@router.post("/me/lists", response_model=GameListOut, status_code=status.HTTP_201_CREATED)
def create_list(
    data: GameListCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GameList:
    duplicated = any(
        existing.name.lower() == data.name.lower()
        for existing in list_service.list_user_lists(db, user)
    )
    if duplicated:
        raise HTTPException(status_code=400, detail="Ya tenés una lista con ese nombre")
    return list_service.create_list(db, user, data.name, data.description, data.is_public)


@router.post("/me/lists/{list_id}/items", response_model=GameListOut)
def add_game_to_list(
    list_id: int,
    data: GameListItemCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GameList:
    game_list = _require_list(db, user, list_id)
    if db.get(Game, data.game_id) is None:
        raise HTTPException(status_code=404, detail="El juego no existe")
    return list_service.add_game(db, game_list, data.game_id, data.note)


@router.delete("/me/lists/{list_id}/items/{game_id}", response_model=GameListOut)
def remove_game_from_list(
    list_id: int,
    game_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GameList:
    return list_service.remove_game(db, _require_list(db, user, list_id), game_id)
