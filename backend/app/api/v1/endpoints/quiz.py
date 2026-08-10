"""Asistente "¿Qué jugamos hoy?".

A diferencia de las recomendaciones personalizadas (que leen el historial del
usuario), esto responde a una pregunta puntual — el ánimo de ahora mismo, no
el gusto general — así que arma el perfil de contenido al vuelo a partir de
las respuestas y no toca el historial. Reutiliza el motor real (embeddings +
popularidad) en vez de un cruce de etiquetas armado a mano, y si se pide un
aspecto prioritario, reordena por sentimiento real de reseñas (ABSA) en vez
de sólo por género.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.ml.analytics import aspect_scores_by_game
from app.ml.recommender import Recommendation, get_engine
from app.models import Aspect, Game
from app.schemas.game import GameSummary
from app.services import steam_service

router = APIRouter(prefix="/quiz", tags=["que-jugamos"])

# Cuántos candidatos trae el motor antes de filtrar: tiene que ser generoso
# para que, después de filtrar por compañía/duración y reordenar por
# aspecto, sigan quedando opciones reales entre las que elegir.
CANDIDATE_POOL = 60


class QuizRequest(BaseModel):
    genres: list[str] = Field(default_factory=list, description="Géneros del ánimo elegido")
    mood_tags: list[str] = Field(
        default_factory=list,
        description="Categorías que el ánimo exige (ej. jcj para 'quiero competir')",
    )
    max_playtime: int | None = Field(default=None, description="Horas máximas, o null=sin límite")
    company_tags: list[str] = Field(default_factory=list, description="un-jugador / cooperativo / multijugador")
    priority_aspect: Aspect | None = Field(default=None, description="Qué aspecto pesa más al ordenar")


class QuizPick(BaseModel):
    game: GameSummary
    reason: str
    aspect_evidence: str | None = None
    aspect_score: float | None = None


class QuizResponse(BaseModel):
    picks: list[QuizPick]
    relaxed: list[str]


def _filter(
    candidates: list[Recommendation],
    games: dict[int, Game],
    payload: QuizRequest,
    *,
    use_playtime: bool = True,
    use_company: bool = True,
    use_mood_tags: bool = True,
) -> list[Recommendation]:
    company = set(payload.company_tags)
    mood = set(payload.mood_tags)
    out = []
    for candidate in candidates:
        game = games.get(candidate.game_id)
        if game is None:
            continue
        slugs = {tag.slug for tag in game.tags}
        if use_playtime and payload.max_playtime is not None and game.playtime_hours is not None:
            if game.playtime_hours > payload.max_playtime:
                continue
        if use_company and company and not company & slugs:
            continue
        if use_mood_tags and mood and not mood & slugs:
            continue
        out.append(candidate)
    return out


def _rank_by_aspect(
    db: Session, candidates: list[Recommendation], aspect: Aspect | None
) -> tuple[list[Recommendation], dict[int, dict]]:
    if not aspect or not candidates:
        return candidates, {}
    scores = aspect_scores_by_game(db, [c.game_id for c in candidates], aspect)
    # Sin menciones de ese aspecto no se descarta al juego, se lo manda al
    # final: preferimos completar el trío con algo bueno-pero-sin-evidencia
    # antes que devolver menos de tres resultados.
    ranked = sorted(
        candidates,
        key=lambda c: (scores.get(c.game_id, {}).get("score", -1.0), c.score),
        reverse=True,
    )
    return ranked, scores


@router.post("/suggest", response_model=QuizResponse)
def suggest(payload: QuizRequest, db: Session = Depends(get_db)) -> QuizResponse:
    engine = get_engine(db)
    # Las etiquetas del ánimo también alimentan el perfil de contenido:
    # `Game.content_soup` incluye los slugs de etiquetas, así que "jcj" pesa
    # en la similitud, no sólo como filtro binario.
    candidates = engine.suggest_by_mood(
        payload.genres + payload.mood_tags, limit=CANDIDATE_POOL
    )
    if not candidates:
        candidates = engine.recommend(user_id=None, limit=CANDIDATE_POOL, strategy="popularidad")

    games = {
        game.id: game
        for game in db.scalars(
            select(Game).where(Game.id.in_([c.game_id for c in candidates]))
        )
    }

    relaxed: list[str] = []
    picks = _filter(candidates, games, payload)

    if len(picks) < 3:
        wider = _filter(candidates, games, payload, use_playtime=False)
        if len(wider) > len(picks):
            picks = wider
            relaxed.append("la duración")

    if len(picks) < 3:
        wider = _filter(candidates, games, payload, use_playtime=False, use_company=False)
        if len(wider) >= 3:
            picks = wider
            relaxed.append("con quién jugás")

    if len(picks) < 3:
        fallback = engine.recommend(user_id=None, limit=12, strategy="popularidad")
        picks = fallback
        relaxed.append("el ánimo")
        games.update(
            {
                game.id: game
                for game in db.scalars(
                    select(Game).where(Game.id.in_([c.game_id for c in picks]))
                )
            }
        )

    ranked, aspect_scores = _rank_by_aspect(db, picks, payload.priority_aspect)
    top = ranked[:3]

    # Enriquece/resincroniza los tres elegidos ahora, mientras el frontend ya
    # está mostrando el caldero: así, cuando alguien clickee un resultado, la
    # ficha ya está al día y no dispara un segundo refresco silencioso (con
    # el loader genérico, no el del caldero) al abrir /juego/:id. Sólo pasa
    # si hace falta — respeta el mismo TTL que el resto del catálogo.
    for candidate in top:
        game = games.get(candidate.game_id)
        if game is not None:
            steam_service.maybe_refresh(db, game)

    return QuizResponse(
        picks=[
            QuizPick(
                game=GameSummary.model_validate(games[c.game_id]),
                reason=c.reason,
                aspect_evidence=aspect_scores.get(c.game_id, {}).get("evidence"),
                aspect_score=aspect_scores.get(c.game_id, {}).get("score"),
            )
            for c in top
        ],
        relaxed=relaxed,
    )
