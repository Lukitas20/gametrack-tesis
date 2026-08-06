"""Portada: filas curadas de juegos con ficha completa."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.game import GameSummary, HomeSections
from app.services.game_service import list_featured, list_home_section

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
    )
