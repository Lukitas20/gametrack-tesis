"""Integración con Steam: importar juegos y vincular la cuenta."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.ml.recommender import invalidate_engine
from app.models import Game, User
from app.schemas.game import GameDetail
from app.schemas.user import SteamLinkRequest, UserResponse
from app.services import steam_service
from app.services import steam_profile_service
from app.services import steam_achievement_service
from app.services import steam_review_service

router = APIRouter(prefix="/steam", tags=["steam"])


@router.get("/me/reviews")
def my_steam_reviews(response: Response, page: int = Query(default=1, ge=1, le=1000),
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return steam_review_service.reviews(db, user, page)


@router.post("/me/reviews/sync")
def sync_my_steam_reviews(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return steam_review_service.reviews(db, user, force=True)


@router.get("/me/achievements/{appid}")
def my_steam_achievements(response: Response, appid: int = Path(ge=1, le=4294967295),
                        user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return steam_achievement_service.achievements(db, user, appid)


@router.post("/me/achievements/{appid}/sync")
def sync_my_steam_achievements(response: Response, appid: int = Path(ge=1, le=4294967295),
                             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return steam_achievement_service.achievements(db, user, appid, force=True)


@router.get("/me/profile")
def my_steam_profile(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return steam_profile_service.profile_payload(db, user, steam_profile_service.sync_profile(db, user))


@router.post("/me/sync")
def sync_my_steam_profile(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return steam_profile_service.profile_payload(db, user, steam_profile_service.sync_profile(db, user, force=True))


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
    """El SteamID manual no demuestra propiedad; usar el flujo OpenID."""
    raise HTTPException(status_code=400, detail="Verificá tu cuenta con Steam desde tu perfil")


@router.get("/owned/{steam_id}", response_model=list[dict])
def get_owned_games(
    steam_id: str, _: User = Depends(get_current_user)
) -> list[dict]:
    """Biblioteca pública de una cuenta de Steam.

    Requiere `STEAM_API_KEY`. Sin clave devuelve una lista vacía en lugar de
    fallar, para que la ausencia de configuración no rompa la interfaz.
    """
    return steam_service.get_owned_games(steam_id)
