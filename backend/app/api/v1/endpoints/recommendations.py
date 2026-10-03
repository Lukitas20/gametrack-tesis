"""Recomendaciones personalizadas para el rol jugador."""

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.ml.recommender import recommend_for_user, get_engine
from app.models import Game, Rating, User
from app.schemas.game import GameSummary
from app.schemas.recommendation import RecommendationOut, RecommendationResponse

from app.schemas.gametrack_score import DiscoveryResponse, GameTrackScore, ExplanationQuestion, GameExplanation

router = APIRouter(prefix="/recommendations", tags=["recomendaciones"])


def personal_score_user(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.friendship_service import require_player
    from app.services.steam_review_service import prepare_personal_history
    require_player(user)
    if user.steam_verified:
        prepare_personal_history(db, user)
    return user


@router.get('/upcoming/{appid}/explanation', response_model=GameExplanation)
def upcoming_explanation(response: Response, appid: int = Path(gt=0, le=4294967295),
                         user: User = Depends(personal_score_user), db: Session = Depends(get_db)):
    from app.services.prelaunch_score_service import explain_upcoming
    response.headers['Cache-Control'] = 'no-store'
    return explain_upcoming(db,user,appid)


@router.post('/upcoming/{appid}/explanation', response_model=GameExplanation)
def ask_upcoming(payload: ExplanationQuestion, response: Response, appid: int = Path(gt=0, le=4294967295),
                 user: User = Depends(personal_score_user), db: Session = Depends(get_db)):
    from app.services.prelaunch_score_service import explain_upcoming
    response.headers['Cache-Control'] = 'no-store'
    return explain_upcoming(db,user,appid,payload.question)


@router.get("/game/{game_id}/explanation", response_model=GameExplanation)
def explain_personal_game(
    game_id: int, response: Response,
    user: User = Depends(personal_score_user), db: Session = Depends(get_db),
):
    from app.services.game_explanation_service import explain_game
    response.headers["Cache-Control"] = "no-store"
    return explain_game(db, user, game_id)


@router.post("/game/{game_id}/explanation", response_model=GameExplanation)
def ask_about_personal_game(
    game_id: int, payload: ExplanationQuestion, response: Response,
    user: User = Depends(personal_score_user), db: Session = Depends(get_db),
):
    from app.services.game_explanation_service import explain_game
    response.headers["Cache-Control"] = "no-store"
    return explain_game(db, user, game_id, payload.question)


@router.get("/discovery", response_model=DiscoveryResponse)
def discover_games(
    response: Response,
    mode: str = Query(default="affinity", pattern="^(affinity|critics|friends)$"),
    limit: int = Query(default=8, ge=1, le=24),
    friend_id: int | None = Query(default=None, gt=0),
    user: User = Depends(personal_score_user),
    db: Session = Depends(get_db),
):
    from app.services.gametrack_score_service import discovery
    response.headers["Cache-Control"] = "no-store"
    return discovery(db, user, mode, limit, friend_id)


@router.get("/game/{game_id}/score", response_model=GameTrackScore)
def personal_game_score(
    game_id: int,
    response: Response,
    user: User = Depends(personal_score_user),
    db: Session = Depends(get_db),
):
    from app.services.gametrack_score_service import game_score
    response.headers["Cache-Control"] = "no-store"
    return game_score(db, user, game_id)


@router.get("", response_model=RecommendationResponse)
def get_recommendations(
    limit: int = Query(default=10, ge=1, le=50),
    strategy: str = Query(
        default="auto",
        pattern="^(auto|contenido|colaborativo|hibrido|popularidad|ia_local)$",
        description=(
            "'auto' elige según el historial del usuario. Forzar una estrategia "
            "permite comparar los enfoques entre sí."
        ),
    ),
    discovery: str = Query(
        default="balanced",
        pattern="^(balanced|familiar|explore)$",
        description="Equilibrio entre afinidad y variedad de la selección; no cambia la escala del índice.",
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    """Juegos recomendados para el usuario autenticado.

    La respuesta incluye el aporte de cada estrategia y el motivo en lenguaje
    natural, para que la recomendación sea auditable y no una caja negra.
    """
    recommendations = recommend_for_user(db, user, limit=limit, strategy=strategy, discovery=discovery)

    history_size = (
        db.scalar(select(func.count(Rating.id)).where(Rating.user_id == user.id)) or 0
    )
    games = {
        game.id: game
        for game in db.scalars(
            select(Game).where(Game.id.in_([r.game_id for r in recommendations]))
        )
    }
    sources = {r.source.value for r in recommendations}
    effective_strategy = next(
        (name for name in ("ia_local", "hibrido", "colaborativo", "contenido", "popularidad") if name in sources),
        "popularidad",
    )
    if not recommendations:
        profile_hint = "No quedan juegos disponibles para recomendar con los datos actuales."
    elif effective_strategy == "ia_local":
        profile_hint = "Un modelo entrenado en esta PC cruza patrones de valoraciones con tus notas actuales. No usa una API de IA externa."
    elif effective_strategy == "hibrido":
        profile_hint = (
            "Combinamos tus gustos con patrones de valoración entre juegos. "
            "La señal colaborativa pesa más cuando tiene suficiente evidencia."
        )
    elif effective_strategy == "colaborativo":
        profile_hint = (
            "Usamos patrones de valoración compartidos entre juegos. "
            "Los candidatos con poca evidencia se apoyan en la valoración general."
        )
    elif effective_strategy == "contenido":
        profile_hint = (
            "Usamos tus valoraciones y preferencias para comparar géneros y etiquetas. "
            "Valorar juegos que te gustaron y otros que no ayuda a definir tu perfil."
            if history_size else
            "Partimos de los géneros que elegiste. Valorá algunos juegos para personalizar tu perfil."
        )
    else:
        profile_hint = (
            "La selección usa la valoración general y la cantidad de reseñas. "
            "Elegí géneros o valorá juegos para sumar tus gustos al modo automático."
        )
    if strategy != "auto" and strategy != effective_strategy and recommendations:
        profile_hint = "La estrategia solicitada no tiene evidencia suficiente; usamos una alternativa. " + profile_hint

    return RecommendationResponse(
        strategy=strategy,
        effective_strategy=effective_strategy,
        discovery=discovery,
        profile_hint=profile_hint,
        local_model=get_engine(db).local_model_status,
        history_size=history_size,
        cold_start=history_size < settings.REC_COLD_START_THRESHOLD,
        items=[
            RecommendationOut(
                game=GameSummary.model_validate(games[r.game_id]),
                score=r.score,
                source=r.source,
                reason=r.reason,
                components=r.components,
                signals=r.signals,
            )
            for r in recommendations
            if r.game_id in games
        ],
    )
