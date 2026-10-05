"""Panel de analítica NLP/ABSA. Exclusivo del rol desarrollador."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
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
    DeveloperReviewPage,
)
from app.services import steam_service
from app.services.developer_assistant import explain_developer_report
from app.services.developer_reviews import explore_reviews
from app.models.enums import Aspect, Sentiment

router = APIRouter(prefix="/analytics", tags=["analitica"])


@router.get("/reviews", response_model=DeveloperReviewPage)
def get_reviews(response: Response,
                studio: str | None = Query(default=None, max_length=120),
                game_id: int | None = Query(default=None, gt=0),
                search: str = Query(default="", max_length=300),
                aspect: Aspect | None = None, sentiment: Sentiment | None = None,
                source: Literal["steam", "user", "seed"] | None = None,
                date_from: date | None = None, date_to: date | None = None,
                sort: Literal["newest", "oldest", "helpful"] = "newest",
                limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0),
                user: User = Depends(require_developer), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return explore_reviews(db, user, studio=studio, game_id=game_id, search=search,
                           aspect=aspect, sentiment=sentiment, source=source,
                           date_from=date_from, date_to=date_to, sort=sort, limit=limit, offset=offset)


class AssistantQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=600)
    studio: str | None = Field(default=None, max_length=120)
    game_id: int | None = Field(default=None, gt=0)


@router.get("/studios")
def studios(search: str = Query(default="", max_length=120),
            _: User = Depends(require_developer), db: Session = Depends(get_db)):
    name = func.trim(Game.developer)
    query = select(func.min(name).label('name'), func.count(Game.id).label('games')).where(
        Game.developer.is_not(None), name != ""
    )
    if search.strip():
        query = query.where(func.lower(name).contains(search.strip().lower(), autoescape=True))
    rows = db.execute(query.group_by(func.lower(name)).order_by(func.count(Game.id).desc(), func.min(name)).limit(20))
    return [dict(row._mapping) for row in rows]


@router.post("/assistant")
def assistant(payload: AssistantQuestion, user: User = Depends(require_developer),
              db: Session = Depends(get_db)):
    if payload.game_id is not None:
        game = db.get(Game, payload.game_id)
        if game is None:
            raise HTTPException(404, 'El juego no existe')
        report = game_analytics(db, game)
    else:
        target = (payload.studio or user.studio or '').strip()
        if not target:
            raise HTTPException(400, 'Elegí un estudio para consultar el informe')
        report = studio_analytics(db, target)
    # No actualizar desde Steam al preguntar: explicar la muestra ya guardada.
    import unicodedata
    question = ''.join(c for c in unicodedata.normalize('NFD', payload.question.lower())
                       if unicodedata.category(c) != 'Mn')
    overview = platform_overview(db) if any(word in question for word in ('compar', 'catalogo', 'promedio')) else None
    return explain_developer_report(report, payload.question, overview)


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
    # la última sincronización (si hace más de STEAM_SYNC_TTL_MINUTES, o si
    # es una ficha pendiente que todavía no se enriqueció).
    if not steam_service.maybe_refresh(db, game):
        raise HTTPException(status_code=404, detail="El juego no existe")
    return game_analytics(db, game)


@router.post("/process", response_model=ProcessResult)
def process_reviews(
    reanalyze: bool = Query(
        default=False, description="Reprocesa también las reseñas ya analizadas"
    ),
    limit: int | None = Query(default=None, ge=1),
    studio: str | None = Query(default=None, max_length=120),
    game_id: int | None = Query(default=None, gt=0),
    _: User = Depends(require_developer),
    db: Session = Depends(get_db),
) -> ProcessResult:
    """Ejecuta el módulo NLP sobre las reseñas pendientes."""
    game_ids = None
    if game_id is not None:
        game = db.get(Game, game_id)
        if game is None:
            raise HTTPException(404, 'El juego no existe')
        if studio is not None and (game.developer or '').strip().lower() != studio.strip().lower():
            raise HTTPException(400, 'El juego no pertenece al estudio seleccionado')
        game_ids = [game_id]
    elif studio is not None:
        game_ids = list(db.scalars(select(Game.id).where(
            func.lower(func.trim(Game.developer)) == studio.strip().lower()
        )))
    processed = analyze_pending_reviews(db, limit=limit, reanalyze=reanalyze, game_ids=game_ids)
    return ProcessResult(
        procesadas=processed,
        mensaje=f"Se analizaron {processed} reseñas.",
    )
