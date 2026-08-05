"""Panel de analítica NLP/ABSA. Exclusivo del rol desarrollador."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import require_developer
from app.db.database import get_db
from app.ml.analytics import (
    analyze_pending_reviews,
    game_analytics,
    platform_overview,
    studio_analytics,
)
from app.models import Game, User
from app.schemas.analytics import (
    GameAnalyticsOut,
    PlatformOverviewOut,
    ProcessResult,
    StudioAnalyticsOut,
)
from app.services import steam_service

router = APIRouter(prefix="/analytics", tags=["analitica"])


@router.get("/overview", response_model=PlatformOverviewOut)
def get_overview(
    _: User = Depends(require_developer), db: Session = Depends(get_db)
) -> dict:
    """Referencia global del catálogo, para comparar contra un juego propio."""
    return platform_overview(db)


@router.get("/studio", response_model=StudioAnalyticsOut)
def get_studio_analytics(
    studio: str | None = Query(
        default=None, description="Por defecto, el estudio del usuario autenticado"
    ),
    user: User = Depends(require_developer),
    db: Session = Depends(get_db),
) -> dict:
    target = studio or user.studio
    if not target:
        raise HTTPException(
            status_code=400,
            detail="El usuario no tiene estudio asignado; indicá uno con ?studio=",
        )
    return studio_analytics(db, target)


@router.get("/games/{game_id}", response_model=GameAnalyticsOut)
def get_game_analytics(
    game_id: int,
    _: User = Depends(require_developer),
    db: Session = Depends(get_db),
) -> dict:
    """Sentimiento y desglose por aspecto de un juego."""
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="El juego no existe")
    # Antes de analizar, trae las reseñas de Steam que hayan aparecido desde
    # la última sincronización (si hace más de STEAM_SYNC_TTL_MINUTES).
    steam_service.maybe_refresh(db, game)
    return game_analytics(db, game)


@router.post("/process", response_model=ProcessResult)
def process_reviews(
    reanalyze: bool = Query(
        default=False, description="Reprocesa también las reseñas ya analizadas"
    ),
    limit: int | None = Query(default=None, ge=1),
    _: User = Depends(require_developer),
    db: Session = Depends(get_db),
) -> ProcessResult:
    """Ejecuta el módulo NLP sobre las reseñas pendientes."""
    processed = analyze_pending_reviews(db, limit=limit, reanalyze=reanalyze)
    return ProcessResult(
        procesadas=processed,
        mensaje=f"Se analizaron {processed} reseñas.",
    )
