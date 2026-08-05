"""Integración con Steam: importar juegos y vincular la cuenta."""

from datetime import UTC, datetime
from threading import Lock

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_current_user
from app.db.database import get_db
from app.ml.recommender import invalidate_engine
from app.models import Game, Review, SteamEnrichmentState, User
from app.schemas.game import GameDetail
from app.schemas.user import SteamLinkRequest, UserResponse
from app.services import steam_service

router = APIRouter(prefix="/steam", tags=["steam"])

# Evita que dos visitas casi simultáneas lancen dos importaciones iguales. El
# estado real también queda en SQLite; este set sólo coordina el proceso web.
_active_enrichments: set[int] = set()
_active_enrichments_lock = Lock()


def _enrichment_payload(
    game: Game, state_row: SteamEnrichmentState | None
) -> dict:
    return {
        "game_id": game.id,
        "status": state_row.status if state_row else "pending",
        "metadata_status": game.metadata_status,
        "reviews_imported": state_row.reviews_imported if state_row else 0,
        "last_error": state_row.last_error if state_row else None,
    }


def _run_steam_enrichment(game_id: int, bind) -> None:
    """Trabajo que se ejecuta después de responder el POST al navegador."""
    worker_db = sessionmaker(bind=bind, autocommit=False, autoflush=False)()
    try:
        state_row = worker_db.get(SteamEnrichmentState, game_id)
        if state_row is None:
            state_row = SteamEnrichmentState(game_id=game_id)
            worker_db.add(state_row)
        state_row.status = "running"
        state_row.started_at = datetime.now(UTC)
        state_row.completed_at = None
        state_row.last_error = None
        worker_db.commit()

        reviews_imported = steam_service.enrich_catalog_game(worker_db, game_id)

        state_row = worker_db.get(SteamEnrichmentState, game_id)
        state_row.status = "complete"
        state_row.reviews_imported = reviews_imported
        state_row.completed_at = datetime.now(UTC)
        state_row.last_error = None
        worker_db.commit()
    except Exception as error:  # el estado de error se consulta desde la UI
        worker_db.rollback()
        state_row = worker_db.get(SteamEnrichmentState, game_id)
        if state_row is None:
            state_row = SteamEnrichmentState(game_id=game_id)
            worker_db.add(state_row)
        state_row.status = "failed"
        state_row.last_error = str(error)[:500]
        state_row.completed_at = datetime.now(UTC)
        worker_db.commit()
    finally:
        worker_db.close()
        with _active_enrichments_lock:
            _active_enrichments.discard(game_id)


@router.post("/enrich/{game_id}", status_code=status.HTTP_202_ACCEPTED)
def enqueue_game_enrichment(
    game_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Encola metadata y reseñas de Steam sin bloquear la ficha pública."""
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="El juego no existe")
    if game.steam_app_id is None:
        raise HTTPException(status_code=400, detail="El juego no pertenece a Steam")

    state_row = db.get(SteamEnrichmentState, game_id)
    existing_reviews = db.scalar(
        select(func.count(Review.id)).where(
            Review.game_id == game_id, Review.source == "steam"
        )
    ) or 0

    if state_row is None:
        state_row = SteamEnrichmentState(game_id=game_id)
        db.add(state_row)

    # Compatibilidad con juegos importados antes de existir esta tabla: si ya
    # tienen ficha y reseñas, se marcan listos sin consultar Steam otra vez.
    if game.metadata_status == "complete" and existing_reviews:
        state_row.status = "complete"
        state_row.reviews_imported = int(existing_reviews)
        state_row.last_error = None
        state_row.completed_at = state_row.completed_at or datetime.now(UTC)
        db.commit()
        return _enrichment_payload(game, state_row)

    if state_row.status == "complete" and game.metadata_status == "complete":
        return _enrichment_payload(game, state_row)

    with _active_enrichments_lock:
        if game_id in _active_enrichments:
            return _enrichment_payload(game, state_row)
        _active_enrichments.add(game_id)

    state_row.status = "queued"
    state_row.last_error = None
    db.commit()
    background_tasks.add_task(_run_steam_enrichment, game_id, db.get_bind())
    return _enrichment_payload(game, state_row)


@router.get("/enrich/{game_id}/status")
def get_game_enrichment_status(
    game_id: int, db: Session = Depends(get_db)
) -> dict:
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="El juego no existe")
    return _enrichment_payload(game, db.get(SteamEnrichmentState, game_id))


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
