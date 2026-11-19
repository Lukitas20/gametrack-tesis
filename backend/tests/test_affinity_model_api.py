"""Contrato público del modelo: pruebas aisladas, sin entrenamiento ni datos operativos."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import recommendations as endpoint
from app.core.security import create_access_token
from app.db.database import Base, get_db
from app.ml.recommender import Recommendation
from app.models import Game, User
from app.models.enums import RecommendationSource, UserRole


@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        player = User(username="api-personal-affinity", role=UserRole.PLAYER)
        developer = User(username="dev.demo", role=UserRole.DEVELOPER)
        games = [Game(name=f"Catálogo de prueba {index}", slug=f"affinity-api-{index}") for index in range(3)]
        db.add_all([player, developer, *games]); db.commit()
        status = dict(status="ready", source="steam_public_review_api", model_version="test-isolated",
                      training_observations=12, training_examples=5, history_examples=3,
                      heads={"explicit": True, "hours": False, "internal": "not-public"},
                      dataset_fingerprint="f" * 64, used=True, message="Modelo de prueba aislado.",
                      model_path="not-public", authors=["not-public"], coefficients=[1, 2, 3])
        monkeypatch.setattr("app.services.steam_affinity_service.load_affinity_model", lambda: (object(), status))
        monkeypatch.setattr(endpoint, "get_engine", lambda _: SimpleNamespace(local_model_status={"status": "missing"}))
        application = FastAPI(); application.include_router(endpoint.router)
        application.dependency_overrides[get_db] = lambda: db
        headers = lambda user: {"Authorization": "Bearer " + create_access_token({"sub": str(user.id)})}
        with TestClient(application) as client:
            yield SimpleNamespace(client=client, db=db, games=games, status=status,
                                  player=headers(player), developer=headers(developer))
    engine.dispose()


def test_estado_privado_de_solo_lectura_y_sin_datos_del_artefacto(api):
    assert api.client.get("/recommendations/affinity-model").status_code == 401
    assert api.client.get("/recommendations/affinity-model", headers={"Authorization": "Bearer invalid"}).status_code == 401
    for headers in (api.player, api.developer):
        response = api.client.get("/recommendations/affinity-model", headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["status"] == "ready" and body["used"] is False and body["used_items"] == 0
        assert body["training_observations"] == 12
        assert body["heads"] == {"explicit": True, "hours": False}
        assert not {"model_path", "authors", "coefficients"} & body.keys()
        assert "no calcula una recomendación personal" in body["message"]
    assert api.client.post("/recommendations/affinity-model", headers=api.developer, json={}).status_code == 405


def _recommendations(api, monkeypatch, weights):
    context = dict(has_history=True, history_size=3, affinity_weights=weights, affinity_status=api.status)
    monkeypatch.setattr("app.services.gametrack_score_service.score_context", lambda *args: context)
    items = [Recommendation(game_id=game.id, score=.7, source=RecommendationSource.CONTENT,
                            reason="Comparación aislada", components={"contenido": .7}) for game in api.games[:2]]
    monkeypatch.setattr(endpoint, "recommend_for_user", lambda *args, **kwargs: items)
    return context


def test_recomendaciones_declaran_solo_aportes_a_juegos_realmente_devueltos(api, monkeypatch):
    _recommendations(api, monkeypatch, {api.games[0].id: .2, api.games[2].id: .2})
    response = api.client.get("/recommendations", headers=api.player)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert len(body["items"]) == 2
    assert body["affinity_model"]["used"] is True and body["affinity_model"]["used_items"] == 1
    assert "opiniones reales de Steam también aporta a 1 juego" in body["profile_hint"]
    assert "authors" not in body["affinity_model"]


def test_estado_global_usado_no_basta_si_la_seleccion_no_tiene_aporte(api, monkeypatch):
    _recommendations(api, monkeypatch, {api.games[2].id: .2})
    response = api.client.get("/recommendations", headers=api.player)
    assert response.status_code == 200
    body = response.json()
    assert body["affinity_model"]["used"] is False and body["affinity_model"]["used_items"] == 0
    assert "ninguno de los juegos devueltos" in body["affinity_model"]["message"]
    assert "opiniones reales de Steam también aporta" not in body["profile_hint"]


def test_estrategia_explicita_conserva_motor_sin_afirmar_uso_del_modelo(api, monkeypatch):
    _recommendations(api, monkeypatch, {api.games[0].id: .2})
    monkeypatch.setattr("app.services.gametrack_score_service.score_context",
                        lambda *args: pytest.fail("Una estrategia explícita no necesita calcular el perfil del modelo"))
    response = api.client.get("/recommendations?strategy=contenido", headers=api.player)
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["affinity_model"]["status"] == "ready" and body["affinity_model"]["used"] is False
    assert "no participa en esta selección" in body["affinity_model"]["message"]
    assert "opiniones reales de Steam también aporta" not in body["profile_hint"]
