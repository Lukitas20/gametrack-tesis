"""Asistente "¿Qué jugamos hoy?".

Responde al ánimo de ahora mismo con un perfil de contenido armado a partir
de las respuestas. La consulta individual admite visitas anónimas. Al incluir
amigos aceptados, combina ese contexto con la afinidad de cada participante
para buscar una opción compartida; la modalidad grupal nunca se relaja.

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

La mediana de horas de los reseñadores es una señal orientativa de compromiso,
no una medida de duración de campaña ni de sesión. Las explicaciones señalan
esa limitación y la falta de datos. Los juegos con señales de partidas o
servicio quedan exentos del umbral de horas acumuladas.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_optional_current_user
from app.db.database import get_db
from app.ml import quiz_vocab
from app.ml.analytics import aspect_scores_by_game
from app.ml.group_recommender import GROUP_NOTICE, GROUP_PLAY_MODES, GroupStrategy, group_scores, is_group_playable
from app.ml.recommender import Recommendation, RecommenderEngine, get_engine
from app.models import Aspect, Game, User
from app.schemas.game import GameSummary
from app.services import steam_service
from app.services.friendship_service import resolve_group_members

router = APIRouter(prefix="/quiz", tags=["que-jugamos"])

# Se evalúa todo el universo del motor antes de relajar. Los lotes acotan los
# parámetros SQL y evitan una consulta por juego al leer tags y géneros.
QUERY_BATCH = 500
# Penalización máxima por redundancia; sólo puede mover alternativas cercanas
# dentro del mismo nivel de cumplimiento, nunca saltarse una restricción.
DIVERSITY_PENALTY = 0.06

# Peso del sentimiento del aspecto prioritario en el puntaje final. El
# aspecto SUMA sobre la afinidad, no la reemplaza (antes se reordenaba
# lexicográficamente y el ABSA pisaba todo lo demás): con score ABSA en
# [-1, 1], un juego con reseñas negativas del aspecto baja, uno sin
# evidencia queda neutro, y la afinidad con el ánimo sigue contando.
ASPECT_BOOST = 0.35
# Una sola mención no debe pesar como decenas. La constante es heurística:
# se debe calibrar con la evaluación anotada, no interpretarla como confianza.
ASPECT_PRIOR_MENTIONS = 5


class QuizRequest(BaseModel):
    # Contrato nuevo: el frontend manda la CLAVE de cada respuesta y el
    # backend resuelve qué significa (vocabulario versionado y testeable).
    mood: Literal["historia", "desafio", "relajarme", "competir"] | None = None
    company: Literal["solo", "amigos", "en-linea"] | None = None
    max_playtime: int | None = Field(default=None, gt=0, le=10000, description="Umbral orientativo de horas registradas, o null=sin límite")
    priority_aspect: Aspect | None = Field(default=None, description="Qué aspecto pesa más al ordenar")
    allow_relaxation: bool = Field(default=True, description="Completar con alternativas si hay menos de tres coincidencias")
    exclude_game_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=200)
    friend_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=4)
    group_strategy: GroupStrategy = "balanced"

    # Contrato viejo (frontend legado): sigue funcionando si `mood`/`company`
    # no vienen. Se traduce al mecanismo nuevo con peso uniforme.
    genres: list[str] = Field(default_factory=list, description="(legado) géneros del ánimo")
    mood_tags: list[str] = Field(default_factory=list, description="(legado) requisito duro del ánimo")
    company_tags: list[str] = Field(default_factory=list, description="(legado) etiquetas de compañía")

    @field_validator("exclude_game_ids")
    @classmethod
    def unique_exclusions(cls, values: list[int]) -> list[int]:
        return list(dict.fromkeys(values))


    @model_validator(mode="after")
    def valid_group(self) -> QuizRequest:
        if len(self.friend_ids) != len(set(self.friend_ids)):
            raise ValueError("Elegí a cada amigo una sola vez")
        if self.friend_ids and self.company not in {"amigos", "en-linea"}:
            raise ValueError("Para incluir amigos elegí cooperativo o multijugador en línea")
        return self


class GroupMember(BaseModel):
    id: int
    username: str


class GroupParticipant(GroupMember):
    score: float = Field(ge=0, le=1, description="Índice de afinidad estimada, no probabilidad")
    basis: str


class GroupFit(BaseModel):
    score: float = Field(ge=0, le=1, description="Afinidad agregada según la estrategia grupal")
    mean_score: float = Field(ge=0, le=1)
    min_score: float = Field(ge=0, le=1)
    participants: list[GroupParticipant]


class QuizGroup(BaseModel):
    members: list[GroupMember]
    strategy: GroupStrategy
    notice: str = GROUP_NOTICE


class QuizPick(BaseModel):
    game: GameSummary
    reason: str
    aspect_evidence: str | None = None
    aspect_score: float | None = None
    aspect_mentions: int = 0
    matched_tags: list[str] = Field(default_factory=list)
    matched_criteria: list[str] = Field(default_factory=list)
    relaxed_criteria: list[str] = Field(default_factory=list)
    time_note: str | None = None
    group_fit: GroupFit | None = None


class QuizResponse(BaseModel):
    picks: list[QuizPick]
    relaxed: list[str]
    exact_matches: int = 0
    evaluated_candidates: int = 0
    group: QuizGroup | None = None


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

    if payload.friend_ids:
        company = GROUP_PLAY_MODES[payload.company]
    elif payload.company and payload.company in quiz_vocab.COMPANY_FILTERS:
        company = quiz_vocab.COMPANY_FILTERS[payload.company]
    else:
        company = frozenset(payload.company_tags)
    return profile, mood_required, company


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
    scores: dict[int, dict] = {}
    ids = [c.game_id for c in candidates]
    for start in range(0, len(ids), QUERY_BATCH):
        scores.update(aspect_scores_by_game(db, ids[start:start + QUERY_BATCH], aspect))
    ranked = sorted(
        candidates,
        key=lambda c: _adjusted_score(c, scores),
        reverse=True,
    )
    return ranked, scores


def _adjusted_score(candidate: Recommendation, scores: dict[int, dict]) -> float:
    evidence = scores.get(candidate.game_id, {})
    mentions = evidence.get("mentions", 0)
    support = mentions / (mentions + ASPECT_PRIOR_MENTIONS)
    return candidate.score + ASPECT_BOOST * support * evidence.get("score", 0.0)


def _diversify_top(
    candidates: list[Recommendation], games: dict[int, Game],
    scores: dict[int, dict], previous: list[Recommendation],
) -> list[Recommendation]:
    """Diversidad acotada entre candidatos del mismo nivel de restricciones.

    Jaccard de rasgos semánticos, sin categorías de plataforma. Sólo se
    reordenan los lugares que aún faltan del top 3: coste O(3 * candidatos).
    El primer resultado conserva el máximo puntaje. No es un % de confianza.
    """
    features = {
        c.game_id: {f"genre:{genre.slug}" for genre in games[c.game_id].genres}
        | {f"tag:{tag.slug}" for tag in games[c.game_id].tags if tag.kind == "community"}
        for c in [*previous, *candidates]
    }
    selected = list(previous)
    remaining = list(candidates)
    ordered: list[Recommendation] = []
    while remaining and len(selected) < 3:
        def utility(candidate: Recommendation) -> tuple[float, float, int]:
            terms = features[candidate.game_id]
            redundancy = max(
                (len(terms & features[other.game_id]) / len(terms | features[other.game_id])
                 if terms | features[other.game_id] else 0.0 for other in selected),
                default=0.0,
            )
            score = _adjusted_score(candidate, scores)
            return score - DIVERSITY_PENALTY * redundancy, score, -candidate.game_id

        winner = max(remaining, key=utility)
        ordered.append(winner)
        selected.append(winner)
        remaining.remove(winner)
    return ordered + remaining


def _violations(
    game: Game, payload: QuizRequest, mood_required: frozenset[str],
    company: frozenset[str], mood_fallback: bool,
) -> list[str]:
    tags = {tag.slug for tag in game.tags}
    result = []
    # Sin mediana el juego sigue disponible como alternativa, pero no se
    # presenta como una coincidencia verificada ni pasa el modo estricto.
    unknown_time = (
        payload.max_playtime is not None
        and game.median_review_hours is None
        and not quiz_vocab.is_session_based(tags)
    )
    if unknown_time or not quiz_vocab.passes_time_budget(game.median_review_hours, tags, payload.max_playtime):
        result.append("la duración")
    if company and not company & tags:
        result.append("con quién jugás")
    if mood_fallback or (mood_required and not mood_required & tags):
        result.append("el ánimo")
    return result


def _explanation(
    game: Game, payload: QuizRequest, profile: dict[str, float],
    company: frozenset[str], violations: list[str],
) -> dict:
    """Razones comprobables en los datos; no promete completar un juego."""
    tags = {tag.slug for tag in game.tags}
    names = {item.slug: item.name for item in [*game.genres, *game.tags]}
    matching = sorted(set(names) & set(profile), key=lambda slug: (-profile[slug], slug))
    matched_tags = [names[slug] for slug in matching[:3]]
    criteria: list[str] = []
    if matching and "el ánimo" not in violations:
        criteria.append("Afinidad con tu ánimo")
    if company and company & tags:
        criteria.append({"solo": "Tiene modo individual", "amigos": "Tiene cooperativo", "en-linea": "Tiene multijugador"}.get(payload.company, "Coincide con la compañía elegida"))

    time_note = None
    if payload.max_playtime is not None:
        if quiz_vocab.is_session_based(tags):
            time_note = "Juego de partidas o servicio: sus horas acumuladas no indican cuánto dura una sesión."
        elif game.median_review_hours is None:
            time_note = "Sin datos suficientes de horas: no pudimos verificar tu preferencia de tiempo."
        else:
            hours = f"{game.median_review_hours:g}"
            time_note = f"Mediana de {hours} h registradas por reseñadores (orientativo); no es duración de campaña ni de sesión."
            if "la duración" not in violations:
                criteria.append("Horas registradas dentro del umbral")

    if "el ánimo" in violations:
        reason = "Alternativa por valoración y popularidad; no pudimos mantener el ánimo elegido."
    elif matched_tags:
        reason = "Afinidad con " + ", ".join(matched_tags) + "."
    else:
        reason = "Alternativa por valoración y popularidad, con poca evidencia sobre el ánimo elegido."
    if payload.friend_ids:
        if payload.group_strategy == "balanced":
            reason += " Cruce grupal equilibrado: pesa el acuerdo y se reducen opciones con desacuerdo."
        else:
            reason += " Cruce grupal por afinidad promedio de los participantes."
        criteria.append("Modalidad compatible con la compañía elegida")
    return dict(reason=reason, matched_tags=matched_tags, matched_criteria=criteria,
                relaxed_criteria=violations, time_note=time_note)


@dataclass
class QuizOutcome:
    """Resultado del asistente ANTES de recortarlo a tres y serializarlo.

    Existe para que el arnés de evaluación pueda leer el ranking completo (a
    la profundidad del pool) sin reimplementar el filtrado ni el
    reordenamiento por aspecto: la tabla comparativa de la tesis mide el
    mismo código que responde el endpoint, no una copia paralela. Ver
    ``scripts/eval_arnes.py`` y el test de paridad en
    ``tests/test_evaluacion.py``.
    """

    ranked: list[Recommendation]
    games: dict[int, Game]
    relaxed: list[str] = field(default_factory=list)
    aspect_scores: dict[int, dict] = field(default_factory=dict)
    violations: dict[int, list[str]] = field(default_factory=dict)
    exact_matches: int = 0
    evaluated_candidates: int = 0
    group_fits: dict[int, dict] = field(default_factory=dict)


def run_quiz(
    db: Session, payload: QuizRequest, engine: RecommenderEngine | None = None,
    *, members: list[User] | None = None,
) -> QuizOutcome:
    """Todo el asistente salvo el efecto de red y la serialización HTTP.

    ``engine`` permite inyectar un motor configurado distinto (una variante
    del arnés). Sin él usa el motor cacheado de producción, que es lo que
    hace el endpoint.
    """
    if payload.friend_ids and not members:
        raise ValueError("La recomendación grupal requiere participantes autorizados")
    engine = engine or get_engine(db)
    profile, mood_required, company = _resolve_vocabulary(payload)

    limit = len(engine.game_ids)
    excluded = set(payload.exclude_game_ids)
    candidates = engine.suggest_by_mood(profile, limit=limit, exclude=excluded) if limit else []
    mood_fallback = not candidates and bool(set(engine.game_ids) - excluded)
    if mood_fallback and payload.allow_relaxation:
        candidates = engine.recommend(user_id=None, limit=limit, strategy="popularidad", discovery="familiar")
        candidates = [c for c in candidates if c.game_id not in excluded]

    games: dict[int, Game] = {}
    ids = [c.game_id for c in candidates]
    for start in range(0, len(ids), QUERY_BATCH):
        games.update({game.id: game for game in db.scalars(
            select(Game).where(Game.id.in_(ids[start:start + QUERY_BATCH]))
            .options(selectinload(Game.tags), selectinload(Game.genres))
        )})
    candidates = [c for c in candidates if c.game_id in games]
    fits = {}
    if members:
        # Esta restricción nunca participa de la relajación: ninguna afinidad
        # convierte un juego individual en uno que se pueda compartir.
        candidates = [c for c in candidates if is_group_playable(games[c.game_id], payload.company)]
        fits = group_scores(db, engine, members, payload.group_strategy)
        candidates = [replace(c, score=0.4 * c.score + 0.6 * fits[c.game_id]["score"])
                      for c in candidates]
    violations = {c.game_id: _violations(games[c.game_id], payload, mood_required, company, mood_fallback)
                  for c in candidates}

    # Los niveles se ordenan por las restricciones que realmente incumple
    # cada juego. Una alternativa nunca desplaza una coincidencia exacta;
    # tampoco se descarta tiempo/compañía al caer a popularidad.
    tiers: dict[tuple[bool, bool, bool], list[Recommendation]] = {}
    for candidate in candidates:
        failed = violations[candidate.game_id]
        key = ("el ánimo" in failed, "con quién jugás" in failed, "la duración" in failed)
        if not payload.allow_relaxation and any(key):
            continue
        tiers.setdefault(key, []).append(candidate)

    exact_matches = len(tiers.get((False, False, False), []))
    ranked: list[Recommendation] = []
    aspect_scores: dict[int, dict] = {}
    for key in sorted(tiers):
        group, scores = _rank_with_aspect(db, tiers[key], payload.priority_aspect)
        aspect_scores.update(scores)
        ranked.extend(_diversify_top(group, games, scores, ranked[:3]))
        if len(ranked) >= 3:
            break

    relaxed = [criterion for criterion in ("la duración", "con quién jugás", "el ánimo")
               if any(criterion in violations[c.game_id] for c in ranked[:3])]
    return QuizOutcome(ranked=ranked, games=games, relaxed=relaxed, aspect_scores=aspect_scores,
                       violations=violations, exact_matches=exact_matches,
                       evaluated_candidates=len(candidates), group_fits=fits)


@router.post("/suggest", response_model=QuizResponse)
def suggest(
    payload: QuizRequest, db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_current_user),
) -> QuizResponse:
    members = None
    if payload.friend_ids:
        if user is None:
            raise HTTPException(status_code=401, detail="Iniciá sesión para recomendar con amigos",
                                headers={"WWW-Authenticate": "Bearer"})
        members = resolve_group_members(db, user, payload.friend_ids)
    outcome = run_quiz(db, payload, members=members)
    profile, _, company = _resolve_vocabulary(payload)

    # Prioriza las tres fichas elegidas para el worker, sirviendo los datos
    # locales sin esperar a Steam. La comprobación de cambios también cubre
    # otras implementaciones del refresco y clasificaciones concurrentes.
    changed = False
    for candidate in outcome.ranked[:3]:
        game = outcome.games.get(candidate.game_id)
        if game is not None:
            before = (game.steam_synced_at, game.median_review_hours,
                      {tag.slug for tag in game.tags}, {genre.slug for genre in game.genres})
            exists = steam_service.maybe_refresh(db, game)
            if not exists:
                changed = True
            else:
                after = (game.steam_synced_at, game.median_review_hours,
                         {tag.slug for tag in game.tags}, {genre.slug for genre in game.genres})
                changed = changed or before != after

    # Recalcular si cambió la modalidad o la elegibilidad de una ficha. El
    # mantenimiento conserva todas sus interacciones; no borra el juego.
    if payload.friend_ids:
        # Comprobar también antes de devolver afinidades que nadie haya
        # revocado una amistad durante la consulta.
        members = resolve_group_members(db, user, payload.friend_ids)
    if changed:
        outcome = run_quiz(db, payload, members=members)
    games, aspect_scores = outcome.games, outcome.aspect_scores
    top = outcome.ranked[:3]

    return QuizResponse(
        picks=[
            QuizPick(
                game=GameSummary.model_validate(games[c.game_id]),
                **_explanation(games[c.game_id], payload, profile, company,
                               outcome.violations[c.game_id]),
                aspect_evidence=aspect_scores.get(c.game_id, {}).get("evidence"),
                aspect_score=aspect_scores.get(c.game_id, {}).get("score"),
                aspect_mentions=aspect_scores.get(c.game_id, {}).get("mentions", 0),
                group_fit=outcome.group_fits.get(c.game_id),
            )
            for c in top
        ],
        relaxed=outcome.relaxed,
        exact_matches=outcome.exact_matches,
        evaluated_candidates=outcome.evaluated_candidates,
        group=QuizGroup(members=[GroupMember(id=member.id, username=member.username) for member in members],
                        strategy=payload.group_strategy) if members else None,
    )
