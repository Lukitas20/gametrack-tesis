"""Portada: filas curadas de juegos con ficha completa."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.game import GameSummary, HomeSections, HomeCollection, UpcomingReleases
from app.services.game_service import list_featured, list_home_section, list_home_collections
from app.services.home_release_service import upcoming_releases

router = APIRouter(prefix="/home", tags=["portada"])


@router.get("", response_model=HomeSections)
def get_home(
    limit: int = Query(default=8, ge=1, le=20), db: Session = Depends(get_db)
) -> HomeSections:
    """Populares, mejor valorados, recientes y destacados.

    No incluye las recomendaciones personalizadas: esas ya tienen su propio
    endpoint (``/recommendations``), con lógica de estrategias que no aplica
    acá. El frontend pide las dos cosas en paralelo para armar la portada.
    """
    return HomeSections(
        populares=[
            GameSummary.model_validate(game)
            for game in list_home_section(db, "popularidad", limit)
        ],
        mejor_valorados=[
            GameSummary.model_validate(game) for game in list_home_section(db, "rating", limit)
        ],
        recientes=[
            GameSummary.model_validate(game)
            for game in list_home_section(db, "lanzamiento", limit)
        ],
        destacados=[GameSummary.model_validate(game) for game in list_featured(db, limit)],
        critica=[GameSummary.model_validate(game) for game in list_home_section(db, "metacritic", limit)],
        colecciones=[HomeCollection(**{**collection, "games": [GameSummary.model_validate(game) for game in collection["games"]]})
            for collection in list_home_collections(db)],
    )


@router.get("/upcoming", response_model=UpcomingReleases)
def get_upcoming():
    return upcoming_releases()
