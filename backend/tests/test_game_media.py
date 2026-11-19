"""Una ficha abierta obtiene capturas sin importar reseñas ni esperar al worker."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.games import router
from app.db.database import Base, get_db
from app.models import Game, Review, SteamCatalogEntry
from app.schemas.game import GameDetail
from app.services import game_media_service, steam_service


IMAGE = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/220/ss_real.jpg"


@pytest.fixture
def media_page(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        game = Game(name="Half-Life 2", slug="half-life-2", steam_app_id=220,
                    description="Descripción guardada", steam_synced_at=datetime.now(timezone.utc))
        db.add(game)
        db.commit()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        monkeypatch.setattr(steam_service, "import_reviews", lambda *a, **k: pytest.fail("La galería no importa reseñas"))
        with TestClient(app) as client:
            yield db, game, client
    engine.dispose()


def test_media_completa_ficha_enriquecida_y_reutiliza_cache(media_page, monkeypatch):
    db, game, client = media_page
    synced = game.steam_synced_at
    calls = []

    def details(appid, *, timeout):
        calls.append((appid, timeout))
        return {"type": "game", "name": "Nombre remoto", "screenshots": [{"path_full": IMAGE}]}

    monkeypatch.setattr(steam_service, "get_app_details", details)
    assert client.get(f"/games/{game.id}").json()["screenshots"] == []
    assert calls == []  # La ficha sigue respondiendo sin depender de Steam.
    response = client.get(f"/games/{game.id}/media")
    assert response.status_code == 200
    assert response.json() == {"screenshots": [IMAGE], "status": "available"}
    assert client.get(f"/games/{game.id}/media").json() == response.json()
    assert client.get(f"/games/{game.id}").json()["screenshots"] == [IMAGE]
    assert len(calls) == 1
    assert calls[0][0] == 220 and calls[0][1].read == 4
    assert calls[0][1].connect == 2 and calls[0][1].pool == 1
    db.refresh(game)
    assert game.name == "Half-Life 2" and game.description == "Descripción guardada"
    assert game.steam_synced_at == synced
    assert db.scalar(select(func.count(Review.id))) == 0
    assert db.get(SteamCatalogEntry, 220).status == "pending"


def test_respuesta_valida_sin_capturas_es_cache_negativo(media_page, monkeypatch):
    _, game, client = media_page
    calls = []

    def details(appid, **kwargs):
        calls.append(appid)
        return {"type": "game", "name": "Juego sin capturas", "screenshots": []}

    monkeypatch.setattr(steam_service, "get_app_details", details)
    for _ in range(2):
        assert client.get(f"/games/{game.id}/media").json() == {"screenshots": [], "status": "empty"}
    assert calls == [220]
    assert game.screenshots == []


def test_timeout_no_marca_sin_capturas_y_respeta_backoff_persistente(media_page, monkeypatch):
    db, game, client = media_page
    calls = []

    class Client:
        def get(self, url, *, params, timeout):
            calls.append((params["appids"], timeout))
            raise httpx.ReadTimeout("Tiempo de espera")

    monkeypatch.setattr(steam_service, "_http_client", lambda: Client())
    assert client.get(f"/games/{game.id}/media").json()["status"] == "unavailable"
    entry = db.get(SteamCatalogEntry, 220)
    retry_at = entry.next_attempt_at
    assert retry_at > datetime.now(timezone.utc).replace(tzinfo=None)
    assert entry.attempts == 1 and entry.status == "unavailable"
    assert game.screenshots is None
    assert client.get(f"/games/{game.id}/media").json()["status"] == "pending"
    assert len(calls) == 1 and entry.next_attempt_at == retry_at
    # Al vencer el backoff se puede recuperar sin cambiar la ficha entera.
    entry.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    monkeypatch.setattr(steam_service, "get_app_details", lambda *a, **k: {"type": "game", "screenshots": [{"path_full": IMAGE}]})
    assert client.get(f"/games/{game.id}/media").json()["screenshots"] == [IMAGE]


@pytest.mark.parametrize("payload", [None, {"type": "game", "screenshots": "malformed"}])
def test_respuesta_inaccesible_o_malformada_no_se_cachea_como_vacia(media_page, monkeypatch, payload):
    db, game, client = media_page
    monkeypatch.setattr(steam_service, "get_app_details", lambda *a, **k: payload)
    assert client.get(f"/games/{game.id}/media").json()["status"] == "unavailable"
    assert game.screenshots is None
    assert db.get(SteamCatalogEntry, 220).attempts == 1


def test_consulta_concurrente_del_mismo_juego_no_espera_ni_duplica_red(media_page, monkeypatch):
    _, game, client = media_page
    monkeypatch.setattr(steam_service, "get_app_details", lambda *a, **k: pytest.fail("Otra consulta ya está en curso"))
    lock = game_media_service._MEDIA_LOCKS[220 % len(game_media_service._MEDIA_LOCKS)]
    with lock:
        assert client.get(f"/games/{game.id}/media").json() == {"screenshots": [], "status": "pending"}


def test_presupuesto_concurrente_no_encola_mas_red(media_page, monkeypatch):
    _, game, client = media_page
    monkeypatch.setattr(steam_service, "get_app_details", lambda *a, **k: pytest.fail("El presupuesto de red está ocupado"))
    slots = game_media_service._MEDIA_SLOTS
    with slots, slots:
        assert client.get(f"/games/{game.id}/media").json() == {"screenshots": [], "status": "pending"}


def test_limite_de_steam_respeta_retry_after_sin_cachear_vacio(media_page, monkeypatch):
    db, game, client = media_page
    requested = []

    class Client:
        def get(self, url, **kwargs):
            requested.append(1)
            return httpx.Response(429, headers={"Retry-After": "1800"})

    started = datetime.now(timezone.utc).replace(tzinfo=None)
    monkeypatch.setattr(steam_service, "_http_client", lambda: Client())
    assert client.get(f"/games/{game.id}/media").json()["status"] == "unavailable"
    assert db.get(SteamCatalogEntry, 220).next_attempt_at >= started + timedelta(seconds=1800)
    assert game.screenshots is None
    assert client.get(f"/games/{game.id}/media").json()["status"] == "pending"
    assert requested == [1]


def test_capturas_antiguas_invalidas_se_filtran_y_se_pueden_recuperar(media_page, monkeypatch):
    _, game, client = media_page
    game.screenshots = ["https://steamstatic.com.attacker.example/ss.jpg", "javascript:alert(1)"]
    assert GameDetail.model_validate(game).screenshots == []
    monkeypatch.setattr(steam_service, "get_app_details", lambda *a, **k: {"type": "game", "screenshots": [{"path_full": IMAGE}]})
    assert client.get(f"/games/{game.id}/media").json() == {"screenshots": [IMAGE], "status": "available"}


def test_juego_sin_steam_y_juego_inexistente_no_llaman_api(media_page, monkeypatch):
    db, game, client = media_page
    game.steam_app_id = None
    db.commit()
    monkeypatch.setattr(steam_service, "get_app_details", lambda *a, **k: pytest.fail("No hay un AppID de Steam"))
    assert client.get(f"/games/{game.id}/media").json() == {"screenshots": [], "status": "empty"}
    assert client.get("/games/999999/media").status_code == 404
