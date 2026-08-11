"""Asistente "¿Qué jugamos hoy?".

A diferencia de las recomendaciones personalizadas (que leen el historial del
usuario), esto responde a una pregunta puntual — el ánimo de ahora mismo, no
el gusto general — así que arma un perfil de contenido al vuelo a partir de
las respuestas.

El diseño separa dos mecanismos que antes se mezclaban:

- **Filtros duros** (compañía, requisito del ánimo, duración): un juego los
  cumple o no aparece. Si la escasez obliga a relajar alguno, se declara en
  ``relaxed`` — nunca en silencio.
- **Ranking blando** (perfil del ánimo + calidad + aspecto prioritario): el
  perfil es un vector ponderado sobre el vocabulario del catálogo (ver
  ``app.ml.quiz_vocab``) comparado por coseno contra la matriz TF-IDF de
  etiquetas votadas; el aspecto prioritario suma un boost por el sentimiento
  real de las reseñas (ABSA) hacia ese aspecto, con la cita textual que lo
  justifica.

La duración distingue compromiso de sesión: la mediana de horas de los
reseñadores mide cuánto lleva TERMINAR un juego finito, así que filtra a los
finitos; un juego-servicio (CS2: partidas de 40 minutos, 164 h de mediana
acumulada) queda exento — su mediana alta significa lo contrario de "no es
para una tarde".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.ml import quiz_vocab
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

# Peso del sentimiento del aspecto prioritario en el puntaje final. El
# aspecto SUMA sobre la afinidad, no la reemplaza (antes se reordenaba
# lexicográficamente y el ABSA pisaba todo lo demás): con score ABSA en
# [-1, 1], un juego con reseñas negativas del aspecto baja, uno sin
# evidencia queda neutro, y la afinidad con el ánimo sigue contando.
ASPECT_BOOST = 0.35


class QuizRequest(BaseModel):
    # Contrato nuevo: el frontend manda la CLAVE de cada respuesta y el
    # backend resuelve qué significa (vocabulario versionado y testeable).
    mood: str | None = Field(default=None, description="historia | desafio | relajarme | competir")
    company: str | None = Field(default=None, description="solo | amigos | en-linea")
    max_playtime: int | None = Field(default=None, description="Horas máximas, o null=sin límite")
    priority_aspect: Aspect | None = Field(default=None, description="Qué aspecto pesa más al ordenar")

    # Contrato viejo (frontend legado): sigue funcionando si `mood`/`company`
    # no vienen. Se traduce al mecanismo nuevo con peso uniforme.
    genres: list[str] = Field(default_factory=list, description="(legado) géneros del ánimo")
    mood_tags: list[str] = Field(default_factory=list, description="(legado) requisito duro del ánimo")
    company_tags: list[str] = Field(default_factory=list, description="(legado) etiquetas de compañía")


class QuizPick(BaseModel):
    game: GameSummary
    reason: str
    aspect_evidence: str | None = None
    aspect_score: float | None = None


class QuizResponse(BaseModel):
    picks: list[QuizPick]
    relaxed: list[str]


def _resolve_vocabulary(
    payload: QuizRequest,
) -> tuple[dict[str, float], frozenset[str], frozenset[str]]:
    """(perfil blando, requisito duro del ánimo, filtro de compañía)."""
    if payload.mood and payload.mood in quiz_vocab.MOOD_PROFILES:
        profile = quiz_vocab.MOOD_PROFILES[payload.mood]
        mood_required = quiz_vocab.MOOD_REQUIREMENTS[payload.mood]
    else:
        profile = {slug: 1.0 for slug in payload.genres + payload.mood_tags}
        mood_required = frozenset(payload.mood_tags)

    if payload.company and payload.company in quiz_vocab.COMPANY_FILTERS:
        company = quiz_vocab.COMPANY_FILTERS[payload.company]
    else:
        company = frozenset(payload.company_tags)
    return profile, mood_required, company


def _filter(
    candidates: list[Recommendation],
    games: dict[int, Game],
    mood_required: frozenset[str],
    company: frozenset[str],
    max_playtime: int | None,
    *,
    use_playtime: bool = True,
    use_company: bool = True,
    use_mood_tags: bool = True,
) -> list[Recommendation]:
    out = []
    for candidate in candidates:
        game = games.get(candidate.game_id)
        if game is None:
            continue
        slugs = {tag.slug for tag in game.tags}
        if use_playtime and not quiz_vocab.passes_time_budget(
            game.median_review_hours, slugs, max_playtime
        ):
            continue
        if use_company and company and not company & slugs:
            continue
        if use_mood_tags and mood_required and not mood_required & slugs:
            continue
        out.append(candidate)
    return out


def _rank_with_aspect(
    db: Session, candidates: list[Recommendation], aspect: Aspect | None
) -> tuple[list[Recommendation], dict[int, dict]]:
    """Reordena sumando el sentimiento ABSA del aspecto al puntaje del motor.

    Boost aditivo, no reordenamiento lexicográfico: la afinidad con el ánimo
    sigue mandando y el aspecto la ajusta. Un juego sin evidencia del
    aspecto queda neutro (0); uno con reseñas NEGATIVAS de ese aspecto baja
    — que es la diferencia práctica con la versión anterior, donde "sin
    evidencia" iba último y "evidencia mala" podía quedar arriba.
    """
    if not aspect or not candidates:
        return candidates, {}
    scores = aspect_scores_by_game(db, [c.game_id for c in candidates], aspect)
    ranked = sorted(
        candidates,
        key=lambda c: c.score + ASPECT_BOOST * scores.get(c.game_id, {}).get("score", 0.0),
        reverse=True,
    )
    return ranked, scores


@router.post("/suggest", response_model=QuizResponse)
def suggest(payload: QuizRequest, db: Session = Depends(get_db)) -> QuizResponse:
    engine = get_engine(db)
    profile, mood_required, company = _resolve_vocabulary(payload)

    relaxed: list[str] = []
    candidates = engine.suggest_by_mood(profile, limit=CANDIDATE_POOL)
    if not candidates:
        # El perfil no matcheó ningún término del corpus: se cae a
        # popularidad pura y SE DECLARA. La degradación silenciosa de acá
        # fue un bug real (y el golden test que lo cazó sigue vigilando).
        candidates = engine.recommend(user_id=None, limit=CANDIDATE_POOL, strategy="popularidad")
        relaxed.append("el ánimo")

    games = {
        game.id: game
        for game in db.scalars(
            select(Game).where(Game.id.in_([c.game_id for c in candidates]))
        )
    }

    picks = _filter(candidates, games, mood_required, company, payload.max_playtime)

    if len(picks) < 3:
        wider = _filter(
            candidates, games, mood_required, company, payload.max_playtime,
            use_playtime=False,
        )
        if len(wider) > len(picks):
            picks = wider
            relaxed.append("la duración")

    if len(picks) < 3:
        wider = _filter(
            candidates, games, mood_required, company, payload.max_playtime,
            use_playtime=False, use_company=False,
        )
        if len(wider) >= 3:
            picks = wider
            relaxed.append("con quién jugás")

    if len(picks) < 3 and "el ánimo" not in relaxed:
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

    ranked, aspect_scores = _rank_with_aspect(db, picks, payload.priority_aspect)
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
