"""Regresiones de restricciones, cobertura y explicaciones del asistente.

No estiman relevancia real: prueban garantías observables del producto con
catálogo controlado, incluyendo el antiguo corte arbitrario de 60 juegos.
"""

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints import quiz
from app.api.v1.endpoints.quiz import QuizRequest, _rank_with_aspect, run_quiz
from app.ml import quiz_vocab
from app.ml.analytics import aspect_scores_by_game, label_from_score
from app.ml.recommender import Recommendation
from app.models import Aspect, Game, RecommendationSource, Review, ReviewAspect, Tag
from app.services import steam_service
from tests.test_quiz_golden import client, db, sin_red, suggest  # noqa: F401


class OrderedEngine:
    """Puntajes controlados para aislar filtrado y selección del modelo."""

    def __init__(self, scored: list[tuple[int, float]], *, no_mood: bool = False):
        self.game_ids = [game_id for game_id, _ in scored]
        self.candidates = [Recommendation(game_id, score, RecommendationSource.CONTENT, "fixture")
                           for game_id, score in scored]
        self.no_mood = no_mood

    def suggest_by_mood(self, profile, limit, exclude=None):
        if self.no_mood:
            return []
        return [c for c in self.candidates if c.game_id not in (exclude or set())][:limit]

    def recommend(self, user_id, limit, strategy, discovery="balanced"):
        assert discovery == "familiar"  # el fallback no aplica MMR al catálogo completo
        return self.candidates[:limit]


def test_modo_estricto_devuelve_menos_sin_saltar_restricciones(client: TestClient) -> None:
    body = suggest(client, {"mood": "competir", "company": "amigos",
                            "max_playtime": 15, "allow_relaxation": False})
    assert [pick["game"]["slug"] for pick in body["picks"]] == ["apex-legends"]
    assert body["exact_matches"] == 1
    assert body["evaluated_candidates"] == 17
    assert body["relaxed"] == []
    assert body["picks"][0]["relaxed_criteria"] == []


def test_popularidad_preserva_compania_y_tiempo_antes_de_relajar(client: TestClient, db: Session) -> None:
    body = suggest(client, {"genres": ["desconocido"], "company": "solo", "max_playtime": 15})
    assert len(body["picks"]) == 3
    assert body["relaxed"] == ["el ánimo"]
    for pick in body["picks"]:
        game = db.get(Game, pick["game"]["id"])
        tags = {tag.slug for tag in game.tags}
        assert tags & quiz_vocab.COMPANY_FILTERS["solo"]
        assert quiz_vocab.passes_time_budget(game.median_review_hours, tags, 15)
        assert pick["relaxed_criteria"] == ["el ánimo"]
        assert "Afinidad con tu ánimo" not in pick["matched_criteria"]


def test_fallback_declara_todas_las_restricciones_incompatibles(db: Session) -> None:
    witcher = db.scalar(select(Game).where(Game.slug == "the-witcher-3-wild-hunt"))
    engine = OrderedEngine([(witcher.id, 1.0)])
    payload = QuizRequest(mood="competir", company="amigos", max_playtime=15)
    outcome = run_quiz(db, payload, engine)
    assert len(outcome.ranked) == 1
    assert outcome.relaxed == ["la duración", "con quién jugás", "el ánimo"]
    assert outcome.violations[witcher.id] == outcome.relaxed
    assert outcome.exact_matches == 0


def test_candidatos_fuera_del_top_60_evitan_relajacion_innecesaria(db: Session) -> None:
    coop = db.scalar(select(Tag).where(Tag.slug == "cooperativo"))
    distractors = [Game(slug=f"distractor-{index}", name=f"Distractor {index}", tags=[coop])
                   for index in range(61)]
    db.add_all(distractors)
    db.flush()
    exact = list(db.scalars(select(Game).where(Game.slug.in_(
        ["unpacking", "a-short-hike", "celeste"]
    ))))
    engine = OrderedEngine([(game.id, 1.0) for game in distractors]
                           + [(game.id, 0.3) for game in exact])
    outcome = run_quiz(db, QuizRequest(mood="relajarme", company="solo"), engine)
    assert {candidate.game_id for candidate in outcome.ranked[:3]} == {game.id for game in exact}
    assert outcome.relaxed == []
    assert outcome.exact_matches == 3
    assert outcome.evaluated_candidates == 64


def test_diversidad_solo_mueve_alternativas_cercanas(db: Session) -> None:
    original = db.scalar(select(Game).where(Game.slug == "unpacking"))
    different = db.scalar(select(Game).where(Game.slug == "celeste"))
    distant = db.scalar(select(Game).where(Game.slug == "the-witcher-3-wild-hunt"))
    copies = [Game(slug=f"clone-{index}", name=f"Clone {index}", tags=original.tags,
                   genres=original.genres) for index in range(2)]
    db.add_all(copies)
    db.flush()
    engine = OrderedEngine([(original.id, 1.0), (copies[0].id, 0.995),
                            (copies[1].id, 0.99), (different.id, 0.98), (distant.id, 0.4)])
    outcome = run_quiz(db, QuizRequest(mood="relajarme", company="solo"), engine)
    ids = [candidate.game_id for candidate in outcome.ranked[:3]]
    assert ids[0] == original.id
    assert different.id in ids
    assert distant.id not in ids


def test_otras_opciones_excluye_las_ya_mostradas(client: TestClient) -> None:
    first = suggest(client, {"mood": "historia", "company": "solo"})
    excluded = [pick["game"]["id"] for pick in first["picks"]]
    second = suggest(client, {"mood": "historia", "company": "solo", "exclude_game_ids": excluded})
    assert len(second["picks"]) == 3
    assert not set(excluded) & {pick["game"]["id"] for pick in second["picks"]}


def test_exclusiones_tambien_aplican_al_fallback(db: Session) -> None:
    games = list(db.scalars(select(Game).limit(4)))
    engine = OrderedEngine([(game.id, 1.0) for game in games], no_mood=True)
    outcome = run_quiz(db, QuizRequest(genres=["desconocido"], exclude_game_ids=[games[0].id]), engine)
    assert games[0].id not in {candidate.game_id for candidate in outcome.ranked}
    assert len(outcome.ranked) == 3


def test_sin_candidatos_no_repite_los_excluidos(db: Session) -> None:
    game = db.scalar(select(Game))
    engine = OrderedEngine([(game.id, 1.0)])
    outcome = run_quiz(db, QuizRequest(mood="historia", exclude_game_ids=[game.id]), engine)
    assert outcome.ranked == []
    assert outcome.relaxed == []


def test_explicaciones_citan_rasgos_y_no_prometen_duracion(client: TestClient) -> None:
    body = suggest(client, {"mood": "relajarme", "company": "solo", "max_playtime": 15})
    for pick in body["picks"]:
        assert pick["matched_tags"]
        assert all(name in pick["reason"] for name in pick["matched_tags"])
        assert "Tiene modo individual" in pick["matched_criteria"]
        assert "registradas por reseñadores" in pick["time_note"]
        assert "no es duración" in pick["time_note"]


@pytest.mark.parametrize("payload", [
    {"mood": "desconocido"}, {"company": "desconocido"}, {"max_playtime": 0},
    {"max_playtime": -10}, {"exclude_game_ids": [-1]}, {"exclude_game_ids": list(range(1, 202))},
])
def test_rechaza_parametros_invalidos(client: TestClient, payload: dict) -> None:
    assert client.post("/api/v1/quiz/suggest", json=payload).status_code == 422


def test_exclusiones_se_deduplican() -> None:
    assert QuizRequest(exclude_game_ids=[2, 1, 2]).exclude_game_ids == [2, 1]


@pytest.mark.parametrize("strict", [False, True])
def test_sin_horas_conocidas_solo_se_ofrece_como_alternativa(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, strict: bool,
) -> None:
    unknown = db.scalar(select(Game).where(Game.slug == "unpacking"))
    known = db.scalar(select(Game).where(Game.slug == "a-short-hike"))
    unknown.median_review_hours = None
    db.flush()
    engine = OrderedEngine([(unknown.id, 1.0), (known.id, 0.5)])
    monkeypatch.setattr(quiz, "get_engine", lambda session: engine)

    body = suggest(client, {"mood": "relajarme", "company": "solo", "max_playtime": 15,
                            "allow_relaxation": not strict})
    assert body["exact_matches"] == 1
    assert body["picks"][0]["game"]["id"] == known.id
    assert body["picks"][0]["relaxed_criteria"] == []
    if strict:
        assert len(body["picks"]) == 1
        assert body["relaxed"] == []
    else:
        alternative = body["picks"][1]
        assert alternative["game"]["id"] == unknown.id
        assert alternative["relaxed_criteria"] == ["la duración"]
        assert "no pudimos verificar" in alternative["time_note"]
        assert "Horas registradas dentro del umbral" not in alternative["matched_criteria"]


def test_modo_estricto_no_recurre_a_popularidad_sin_senal_de_animo(db: Session) -> None:
    game = db.scalar(select(Game))
    engine = OrderedEngine([(game.id, 1.0)], no_mood=True)
    outcome = run_quiz(db, QuizRequest(genres=["desconocido"], allow_relaxation=False), engine)
    assert outcome.ranked == []
    assert outcome.exact_matches == 0
    assert outcome.relaxed == []


def _review_aspect(db: Session, game: Game, score: float, evidence: str | None) -> None:
    review = Review(game_id=game.id, content=evidence or "Reseña sin fragmento guardado",
                    source="steam", is_analyzed=True)
    db.add(review)
    db.flush()
    db.add(ReviewAspect(review_id=review.id, game_id=game.id, aspect=Aspect.STORY,
                        sentiment=label_from_score(score), score=score, evidence=evidence))
    db.flush()


@pytest.mark.parametrize("polarity", [-1, 1])
def test_evidencia_representa_el_agregado_y_no_el_extremo(db: Session, polarity: int) -> None:
    game = Game(slug="evidence-game", name="Evidence game")
    db.add(game)
    db.flush()
    _review_aspect(db, game, 0.9 * polarity, "Opinión extrema")
    _review_aspect(db, game, 0.3 * polarity, "Opinión representativa")
    _review_aspect(db, game, -0.3 * polarity, "Opinión contraria")

    result = aspect_scores_by_game(db, [game.id], Aspect.STORY)[game.id]
    assert result["score"] == pytest.approx(0.3 * polarity)
    assert result["mentions"] == 3
    assert result["evidence"] == "Opinión representativa"


@pytest.mark.parametrize("rows", [
    [(-0.9, None), (-0.6, "   "), (0.3, "Elogio aislado")],
    [(0.9, None), (0.6, "   "), (-0.3, "Crítica aislada")],
    [(-0.9, "Muy malo"), (0.9, "Muy bueno")],
])
def test_sin_cita_de_la_polaridad_agregada_no_inventa_respaldo(
    db: Session, rows: list[tuple[float, str | None]],
) -> None:
    game = Game(slug="no-evidence-game", name="No evidence game")
    db.add(game)
    db.flush()
    for score, evidence in rows:
        _review_aspect(db, game, score, evidence)
    result = aspect_scores_by_game(db, [game.id], Aspect.STORY)[game.id]
    assert result["mentions"] == len(rows)
    assert result["evidence"] is None


def test_absa_pondera_apoyo_y_conserva_la_afinidad_del_motor(db: Session) -> None:
    names = ["una-mencion", "varias-menciones", "afinidad-alta", "sin-evidencia", "negativo"]
    games = [Game(slug=name, name=name) for name in names]
    db.add_all(games)
    db.flush()
    _review_aspect(db, games[0], 0.9, "Me gustó la historia")
    for _ in range(20):
        _review_aspect(db, games[1], 0.9, "Me gustó la historia")
        _review_aspect(db, games[4], -0.9, "No me gustó la historia")
    candidates = [Recommendation(game.id, 0.6 if index == 2 else 0.5,
                                  RecommendationSource.CONTENT, "fixture")
                  for index, game in enumerate(games)]
    ranked, _ = _rank_with_aspect(db, candidates, Aspect.STORY)
    assert [candidate.game_id for candidate in ranked] == [
        games[index].id for index in [1, 2, 0, 3, 4]
    ]


@pytest.mark.parametrize("change", ["delete", "company", "hours"])
def test_refresh_revalida_los_resultados_y_no_serializa_juegos_borrados(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    solo = db.scalar(select(Tag).where(Tag.slug == "un-jugador"))
    games = [Game(slug=f"refresh-{index}", name=f"Refresh {index}", tags=[solo],
                  median_review_hours=5) for index in range(4)]
    db.add_all(games)
    db.flush()
    ids = [game.id for game in games]
    engine = OrderedEngine([(game_id, 1 - index * 0.1) for index, game_id in enumerate(ids)])
    monkeypatch.setattr(quiz, "get_engine", lambda session: engine)
    refreshed = []

    def refresh(session, game):
        refreshed.append(game.id)
        if game.id != ids[0]:
            return True
        if change == "delete":
            session.delete(game)
        elif change == "company":
            game.tags = []
        else:
            game.median_review_hours = 100
        session.commit()
        return change != "delete"

    monkeypatch.setattr(steam_service, "maybe_refresh", refresh)
    body = suggest(client, {"mood": "historia", "company": "solo", "max_playtime": 15,
                            "allow_relaxation": False})
    assert [pick["game"]["id"] for pick in body["picks"]] == ids[1:]
    assert refreshed == ids[:3]  # una sola ronda acotada de red
    assert body["exact_matches"] == 3
    assert body["relaxed"] == []
