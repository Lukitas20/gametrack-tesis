"""Autorización, compromiso grupal y modalidades compartidas del asistente."""

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints import quiz
from app.api.v1.endpoints.quiz import QuizRequest, run_quiz
from app.core.security import create_access_token
from app.ml.group_recommender import group_scores, is_group_playable
from app.ml.recommender import Recommendation, RecommenderEngine
from app.models import Friendship, Game, Genre, Rating, RecommendationSource, Tag, User, UserPreference
from tests.test_quiz_golden import client, db, sin_red  # noqa: F401


class GroupEngine:
    def __init__(self, games, scores, methods=None, mood_scores=None):
        self.game_ids = [game.id for game in games]
        self.rows = scores
        self.methods = methods or {}
        self.preferences = {}
        self.mood_scores = mood_scores or [0.5] * len(games)

    def suggest_by_mood(self, profile, limit, exclude=None):
        return [Recommendation(game_id, score, RecommendationSource.CONTENT, "fixture")
                for game_id, score in zip(self.game_ids, self.mood_scores)
                if game_id not in (exclude or set())][:limit]

    def personal_scores(self, user_id, preferred_genres):
        self.preferences[user_id] = preferred_genres
        return np.array(self.rows[user_id]), self.methods.get(user_id, "ia_local")


@pytest.fixture
def members(db: Session):
    host, friend = User(username="anfitrion"), User(username="invitado")
    db.add_all([host, friend])
    db.flush()
    db.add(Friendship(user_low_id=host.id, user_high_id=friend.id,
                      requester_id=host.id, status="accepted"))
    db.commit()
    return [host, friend]


def _games(db):
    return [db.scalar(select(Game).where(Game.slug == slug))
            for slug in ("it-takes-two", "overcooked-2")]


def _headers(user):
    return {"Authorization": "Bearer " + create_access_token({"sub": str(user.id)})}


def _payload(members, **kwargs):
    return {"mood": "relajarme", "company": "amigos", "friend_ids": [members[1].id], **kwargs}


def _install(monkeypatch, db, members, rows=None):
    engine = GroupEngine(_games(db), rows or {member.id: [0.7, 0.8] for member in members})
    monkeypatch.setattr(quiz, "get_engine", lambda session: engine)
    return engine


def test_grupo_requiere_sesion_y_solo_anonimo_sigue_funcionando(client, members):
    assert client.post("/api/v1/quiz/suggest", json=_payload(members)).status_code == 401
    solo = client.post("/api/v1/quiz/suggest", json={"mood": "historia", "company": "solo"})
    assert solo.status_code == 200
    assert solo.json()["group"] is None
    assert all(pick["group_fit"] is None for pick in solo.json()["picks"])


@pytest.mark.parametrize("relation,expected", [
    ("pending", 403), ("outsider", 403), ("missing", 403), ("self", 400), ("inactive", 403),
])
def test_ids_no_autorizados_no_llegan_al_motor(client, db, members, monkeypatch, relation, expected):
    target = members[1]
    friendship = db.scalar(select(Friendship))
    if relation == "pending":
        friendship.status = "pending"
    elif relation == "outsider":
        target = User(username="extraño")
        db.add(target)
    elif relation == "self":
        target = members[0]
    elif relation == "inactive":
        target.is_active = False
    db.commit()
    target_id = 999999 if relation == "missing" else target.id
    monkeypatch.setattr(quiz, "get_engine", lambda session: pytest.fail("No debe leer perfiles"))
    response = client.post("/api/v1/quiz/suggest", headers=_headers(members[0]),
                           json=_payload(members, friend_ids=[target_id]))
    assert response.status_code == expected


@pytest.mark.parametrize("fields", [
    {"friend_ids": [-1]}, {"friend_ids": [1, 2, 3, 4, 5]}, {"friend_ids": [2, 2]},
    {"company": "solo"}, {"company": None}, {"group_strategy": "oculto"},
])
def test_valida_grupo_antes_de_calcular(client, members, fields):
    response = client.post("/api/v1/quiz/suggest", headers=_headers(members[0]),
                           json=_payload(members, **fields))
    assert response.status_code == 422


def test_equilibrado_prefiere_acuerdo_y_promedio_permite_compromiso(db, members):
    games = _games(db)
    engine = GroupEngine(games, {members[0].id: [1.0, 0.6], members[1].id: [0.3, 0.6]})
    balanced = run_quiz(db, QuizRequest(**_payload(members)), engine, members=members)
    average = run_quiz(db, QuizRequest(**_payload(members, group_strategy="average")), engine, members=members)
    assert balanced.ranked[0].game_id == games[1].id
    assert average.ranked[0].game_id == games[0].id
    assert balanced.group_fits[games[0].id]["min_score"] < balanced.group_fits[games[1].id]["min_score"]


def test_rechazo_explicito_reduce_opcion_y_permite_rejugar(db, members):
    games = _games(db)
    db.add_all([Rating(user_id=members[0].id, game_id=games[0].id, score=5),
                Rating(user_id=members[1].id, game_id=games[0].id, score=2)])
    db.flush()
    engine = GroupEngine(games, {members[0].id: [1.0, 0.5], members[1].id: [0.25, 0.5]})
    outcome = run_quiz(db, QuizRequest(**_payload(members)), engine, members=members)
    assert outcome.ranked[0].game_id == games[1].id
    assert games[0].id in {candidate.game_id for candidate in outcome.ranked}
    assert outcome.group_fits[games[0].id]["score"] < outcome.group_fits[games[0].id]["mean_score"]


@pytest.mark.parametrize("relax", [True, False])
def test_juego_individual_nunca_se_filtra_al_grupo(db, members, relax):
    solo = db.scalar(select(Game).where(Game.slug == "unpacking"))
    coop = _games(db)[0]
    engine = GroupEngine([solo, coop], {member.id: [1.0, 0.1] for member in members}, mood_scores=[1.0, 0.1])
    outcome = run_quiz(db, QuizRequest(**_payload(members, allow_relaxation=relax)), engine, members=members)
    assert [candidate.game_id for candidate in outcome.ranked] == [coop.id]
    assert "con quién jugás" not in outcome.relaxed


def test_sin_modalidad_compartida_devuelve_vacio(db, members):
    solo = db.scalar(select(Game).where(Game.slug == "unpacking"))
    engine = GroupEngine([solo], {member.id: [1.0] for member in members})
    outcome = run_quiz(db, QuizRequest(**_payload(members)), engine, members=members)
    assert outcome.ranked == []


def test_online_exige_senal_online_y_amigos_admite_multijugador(db, members):
    generic = Game(slug="generic-multi", name="Multijugador local",
                   tags=[db.scalar(select(Tag).where(Tag.slug == "multijugador"))])
    db.add(generic)
    db.flush()
    engine = GroupEngine([generic], {member.id: [1.0] for member in members})
    online = run_quiz(db, QuizRequest(**_payload(members, company="en-linea")), engine, members=members)
    assert online.ranked == []
    friends = run_quiz(db, QuizRequest(**_payload(members)), engine, members=members)
    assert [candidate.game_id for candidate in friends.ranked] == [generic.id]


def test_excluir_no_se_pierde_en_grupo(db, members):
    games = _games(db)
    engine = GroupEngine(games, {member.id: [0.9, 0.7] for member in members})
    outcome = run_quiz(db, QuizRequest(**_payload(members, exclude_game_ids=[games[0].id])), engine, members=members)
    assert [candidate.game_id for candidate in outcome.ranked] == [games[1].id]


def test_sin_historial_no_inventa_gustos_y_transfiere_preferencias(db, members):
    genre = db.scalar(select(Genre).where(Genre.slug == "casual"))
    db.add(UserPreference(user_id=members[0].id, genre_id=genre.id))
    db.flush()
    engine = GroupEngine(_games(db), {members[0].id: [0.2, 0.8], members[1].id: [1.0, 0.0]},
                         {members[0].id: "contenido", members[1].id: "popularidad"})
    fits = group_scores(db, engine, members, "balanced")
    assert engine.preferences[members[0].id] == ["casual"]
    for fit in fits.values():
        assert fit["participants"][1]["score"] == 0.5
        assert fit["participants"][1]["basis"] == "sin_datos"
    assert fits[engine.game_ids[1]]["score"] > fits[engine.game_ids[0]]["score"]


def test_motor_real_con_preferencias_nuevas_cruza_con_contenido(db, members):
    genre = db.scalar(select(Genre).where(Genre.slug == "casual"))
    db.add(UserPreference(user_id=members[0].id, genre_id=genre.id))
    db.flush()
    fits = group_scores(db, RecommenderEngine(db), members, "balanced")
    assert fits
    assert all(fit["participants"][0]["basis"] == "contenido" for fit in fits.values())
    assert all(fit["participants"][1]["basis"] == "sin_datos" for fit in fits.values())


def test_respuesta_contiene_solo_participantes_y_avisa_capacidad(client, db, members, monkeypatch):
    _install(monkeypatch, db, members)
    response = client.post("/api/v1/quiz/suggest", headers=_headers(members[0]), json=_payload(members))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["group"]["members"] == [{"id": member.id, "username": member.username} for member in members]
    assert "no está verificada" in body["group"]["notice"]
    assert "no son probabilidades" in body["group"]["notice"]
    for pick in body["picks"]:
        assert len(pick["group_fit"]["participants"]) == 2
        for member in pick["group_fit"]["participants"]:
            assert set(member) == {"id", "username", "score", "basis"}


def test_incluye_los_cinco_participantes_y_rechaza_grupos_parciales(client, db, members, monkeypatch):
    for index in range(3):
        friend = User(username=f"amigo-{index}")
        db.add(friend)
        db.flush()
        db.add(Friendship(user_low_id=members[0].id, user_high_id=friend.id,
                          requester_id=friend.id, status="accepted"))
        members.append(friend)
    db.commit()
    _install(monkeypatch, db, members)
    payload = _payload(members, friend_ids=[member.id for member in members[1:]])
    response = client.post("/api/v1/quiz/suggest", headers=_headers(members[0]), json=payload)
    assert response.status_code == 200, response.text
    assert len(response.json()["group"]["members"]) == 5
    assert all(len(pick["group_fit"]["participants"]) == 5 for pick in response.json()["picks"])
    db.scalar(select(Friendship).where(Friendship.user_high_id == members[-1].id)).status = "pending"
    db.commit()
    assert client.post("/api/v1/quiz/suggest", headers=_headers(members[0]), json=payload).status_code == 403


def test_amistad_revocada_no_reutiliza_resultado_anterior(client, db, members, monkeypatch):
    _install(monkeypatch, db, members)
    assert client.post("/api/v1/quiz/suggest", headers=_headers(members[0]), json=_payload(members)).status_code == 200
    db.delete(db.scalar(select(Friendship)))
    db.commit()
    assert client.post("/api/v1/quiz/suggest", headers=_headers(members[0]), json=_payload(members)).status_code == 403


def test_revocacion_durante_refresco_se_revalida(client, db, members, monkeypatch):
    _install(monkeypatch, db, members)
    def revoke(session, game):
        friendship = session.scalar(select(Friendship))
        if friendship:
            session.delete(friendship)
            session.commit()
        return True
    monkeypatch.setattr(quiz.steam_service, "maybe_refresh", revoke)
    response = client.post("/api/v1/quiz/suggest", headers=_headers(members[0]), json=_payload(members))
    assert response.status_code == 403


def test_refresco_que_quita_coop_no_lo_ofrece_al_grupo(client, db, members, monkeypatch):
    _install(monkeypatch, db, members)
    def remove_mode(session, game):
        game.tags = [db.scalar(select(Tag).where(Tag.slug == "un-jugador"))]
        session.flush()
        return True
    monkeypatch.setattr(quiz.steam_service, "maybe_refresh", remove_mode)
    response = client.post("/api/v1/quiz/suggest", headers=_headers(members[0]), json=_payload(members))
    assert response.status_code == 200
    assert response.json()["picks"] == []


@pytest.fixture
def mixed_modes(db):
    """Un catálogo donde texto/afinidad no sustituyen la modalidad real."""
    def tag(slug, name, kind="platform"):
        with db.no_autoflush:
            existing = db.scalar(select(Tag).where(Tag.slug == slug))
        if existing is not None:
            return existing
        item = Tag(slug=slug, name=name, kind=kind)
        db.add(item)
        return item
    games = [
        Game(slug="only-single", name="Individual", tags=[tag("un-jugador", "Un jugador")]),
        Game(slug="unknown-mode", name="Desconocido", description="A multiplayer adventure with online co-op"),
        Game(slug="local-coop", name="Cooperativo local", tags=[tag("local-co-op", "Local Co-Op", "community")]),
        Game(slug="online-coop", name="Cooperativo online", tags=[tag("cooperativo-en-linea", "Cooperativo en línea")]),
        Game(slug="generic-pvp", name="Multijugador sin conexión verificada", tags=[tag("multijugador", "Multijugador"), tag("pvp", "PvP", "community")]),
        Game(slug="local-versus", name="Pantalla compartida", tags=[tag("pantalla-partida-compartida", "Pantalla partida/compartida")]),
    ]
    db.add_all(games)
    db.commit()
    return games


@pytest.mark.parametrize("company,expected", [("amigos", {2, 3, 4, 5}), ("en-linea", {3})])
@pytest.mark.parametrize("with_friends", [False, True])
@pytest.mark.parametrize("relax", [False, True])
def test_compania_es_obligatoria_aun_sin_ids_y_con_catalogo_mixto(
    db, members, mixed_modes, company, expected, with_friends, relax,
):
    games = mixed_modes
    engine = GroupEngine(games, {member.id: [1., .99, .8, .7, .9, .6] for member in members})
    payload = QuizRequest(company=company, allow_relaxation=relax,
                          friend_ids=[members[1].id] if with_friends else [])
    outcome = run_quiz(db, payload, engine, members=members if with_friends else None)
    assert {pick.game_id for pick in outcome.ranked} == {games[i].id for i in expected}
    assert "con quién jugás" not in outcome.relaxed
    assert games[0].id not in outcome.violations and games[1].id not in outcome.violations


@pytest.mark.parametrize("company,expected", [("amigos", {2, 3, 4, 5}), ("en-linea", {3})])
@pytest.mark.parametrize("with_friends", [False, True])
def test_fallback_de_popularidad_no_rellena_con_individuales_o_modalidad_desconocida(
    db, members, mixed_modes, company, expected, with_friends,
):
    class FallbackEngine(GroupEngine):
        def suggest_by_mood(self, profile, limit, exclude=None):
            return []

        def recommend(self, user_id, limit, strategy, discovery):
            return GroupEngine.suggest_by_mood(self, {}, limit)
    games = mixed_modes
    engine = FallbackEngine(games, {member.id: [1.] * len(games) for member in members})
    request = QuizRequest(genres=["unknown"], company=company,
                          friend_ids=[members[1].id] if with_friends else [])
    outcome = run_quiz(db, request, engine, members=members if with_friends else None)
    assert {pick.game_id for pick in outcome.ranked} == {games[i].id for i in expected}
    assert outcome.relaxed == ["el ánimo"]


def test_compania_legada_tampoco_se_relaja(db, members, mixed_modes):
    engine = GroupEngine(mixed_modes, {member.id: [1.] * len(mixed_modes) for member in members})
    outcome = run_quiz(db, QuizRequest(company_tags=["cooperativo-en-linea"]), engine)
    assert [pick.game_id for pick in outcome.ranked] == [mixed_modes[3].id]
    assert outcome.relaxed == []


@pytest.mark.parametrize("company", ["amigos", "en-linea"])
def test_sin_modalidad_conocida_no_rellena_tres_resultados(db, members, mixed_modes, company):
    engine = GroupEngine(mixed_modes[:2], {member.id: [1., 1.] for member in members})
    outcome = run_quiz(db, QuizRequest(company=company), engine)
    assert outcome.ranked == []
    assert outcome.relaxed == []


@pytest.mark.parametrize("strategy", ["popularidad", "contenido", "ia_local"])
@pytest.mark.parametrize("company", ["amigos", "en-linea"])
def test_recomendacion_personal_filtra_modalidad_antes_del_limite(
    db, members, mixed_modes, strategy, company,
):
    from app.ml.recommender import recommend_for_user
    rows = recommend_for_user(db, members[0], limit=50, strategy=strategy, company=company)
    assert rows
    assert all(is_group_playable(db.get(Game, row.game_id), company) for row in rows)
    assert not {mixed_modes[i].id for i in (0, 1)} & {row.game_id for row in rows}


def test_respuesta_sin_amigo_no_atribuye_un_cruce_grupal_y_exige_online(
    client, db, members, mixed_modes, monkeypatch,
):
    engine = GroupEngine(mixed_modes, {member.id: [1.] * len(mixed_modes) for member in members})
    monkeypatch.setattr(quiz, "get_engine", lambda session: engine)
    response = client.post("/api/v1/quiz/suggest", json={"company": "en-linea"})
    assert response.status_code == 200
    body = response.json()
    assert body["group"] is None
    assert [pick["game"]["id"] for pick in body["picks"]] == [mixed_modes[3].id]
    assert "Tiene multijugador en línea" in body["picks"][0]["matched_criteria"]
    assert body["picks"][0]["group_fit"] is None


def test_endpoint_personal_valida_y_aplica_compania(client, db, members, mixed_modes):
    response = client.get('/api/v1/recommendations?company=en-linea&strategy=popularidad&limit=50',
                          headers=_headers(members[0]))
    assert response.status_code == 200, response.text
    rows = response.json()['items']
    assert rows
    assert all(is_group_playable(db.get(Game, row['game']['id']), 'en-linea') for row in rows)
    assert client.get('/api/v1/recommendations?company=unknown', headers=_headers(members[0])).status_code == 422


@pytest.mark.parametrize('with_friends', [False, True])
def test_amigos_incluye_juegos_competitivos_como_cs2(db, members, with_friends):
    cs2 = db.scalar(select(Game).where(Game.slug == 'counter-strike-2'))
    single = db.scalar(select(Game).where(Game.slug == 'unpacking'))
    engine = GroupEngine([single, cs2], {member.id: [1., .5] for member in members})
    payload = QuizRequest(company='amigos', friend_ids=[members[1].id] if with_friends else [])
    outcome = run_quiz(db, payload, engine, members=members if with_friends else None)
    assert [candidate.game_id for candidate in outcome.ranked] == [cs2.id]
