from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.api.v1.deps import get_current_user
from app.models.user import User
from app.services.steam_service import get_app_details, parse_steam_game
from app.services.game_service import get_game_by_steam_id, create_game
from app.schemas.game import GameCreate, GameResponse

router = APIRouter(prefix="/steam", tags=["steam"])


@router.post("/import/{steam_app_id}", response_model=GameResponse)
async def import_game_from_steam(
    steam_app_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = get_game_by_steam_id(db, steam_app_id)
    if existing:
        return existing

    data = await get_app_details(steam_app_id)
    if not data:
        raise HTTPException(status_code=404, detail="Juego no encontrado en Steam")

    parsed = parse_steam_game(data)
    parsed["steam_app_id"] = steam_app_id

    game = create_game(db, GameCreate(**parsed))
    return game