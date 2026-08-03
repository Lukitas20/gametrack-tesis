"""Integración con Steam: importar juegos y vincular la cuenta."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.ml.recommender import invalidate_engine
from app.models import Game, User
from app.schemas.game import GameDetail
from app.schemas.user import SteamLinkRequest, UserResponse
from app.services import steam_service

router = APIRouter(prefix="/steam", tags=["steam"])


@router.post(
    "/import/{steam_app_id}",
    response_model=GameDetail,
    status_code=status.HTTP_201_CREATED,
)
def import_game_from_steam(
    steam_app_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Game:
    """Importa un juego de Steam al catálogo por su AppID.

    Si ya estaba importado lo devuelve tal cual, sin volver a pedirlo a Steam.
    """
    game = steam_service.import_game(db, steam_app_id)
    if game is None:
        raise HTTPException(
            status_code=404, detail=f"Steam no reconoce el AppID {steam_app_id}"
        )

    # El catálogo cambió: el recomendador tiene que reconstruir su modelo.
    invalidate_engine()
    return game


@router.post("/link", response_model=UserResponse)
def link_steam_account(
    payload: SteamLinkRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Vincula una cuenta de Steam al usuario autenticado."""
    try:
        return steam_service.link_steam_account(db, user, payload.steam_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/owned/{steam_id}", response_model=list[dict])
def get_owned_games(
    steam_id: str, _: User = Depends(get_current_user)
) -> list[dict]:
    """Biblioteca pública de una cuenta de Steam.

    Requiere `STEAM_API_KEY`. Sin clave devuelve una lista vacía en lugar de
    fallar, para que la ausencia de configuración no rompa la interfaz.
    """
    return steam_service.get_owned_games(steam_id)
