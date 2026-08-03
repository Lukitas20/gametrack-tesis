"""Pruebas de la integración con Steam.

No se toca la red: se sustituyen las funciones que llaman a Steam por
respuestas fijas con la forma real de su API.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db.base import Base
from app.db.database import get_db
from app.main import app
from app.models import Game, User, UserRole
from app.services import steam_service

PASSWORD = "demo1234"

# Forma real de la respuesta de store.steampowered.com/api/appdetails.
HALF_LIFE = {
    "name": "Half-Life 2",
    "short_description": "Gordon Freeman vuelve a City 17.",
    "detailed_description": "<h1>HTML que no queremos</h1><p>Texto largo.</p>",
    "release_date": {"date": "16 Nov, 2004"},
    "developers": ["Valve"],
    "publishers": ["Valve"],
    "platforms": {"windows": True, "mac": True, "linux": False},
    "header_image": "https://cdn.steam.com/hl2/header.jpg",
    "metacritic": {"score": 96},
    "genres": [{"description": "Action"}, {"description": "Adventure"}],
    "categories": [{"description": "Un jugador"}, {"description": "Logros de Steam"}],
    "price_overview": {"final": 999},
}


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def client(db: Session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def user(db: Session) -> User:
    created = User(
        username="jugadora", hashed_password=hash_password(PASSWORD), role=UserRole.PLAYER
    )
    db.add(created)
    db.commit()
    return created


def auth(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/auth/login", json={"username": "jugadora", "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --- Traducción de la ficha ------------------------------------------------


def test_parse_traduce_los_campos_al_modelo() -> None:
    parsed = steam_service.parse_steam_game(HALF_LIFE)

    assert parsed["name"] == "Half-Life 2"
    assert parsed["slug"] == "half-life-2"
    assert parsed["developer"] == "Valve"
    assert parsed["metacritic"] == 96
    assert parsed["released"].year == 2004
    assert parsed["background_image"].endswith("header.jpg")


def test_parse_usa_la_descripcion_corta_y_no_el_html() -> None:
    # La descripción larga trae marcado que ensuciaría la vista y el corpus
    # TF-IDF del recomendador.
    parsed = steam_service.parse_steam_game(HALF_LIFE)
    assert parsed["description"] == "Gordon Freeman vuelve a City 17."
    assert "<h1>" not in (parsed["description"] or "")


def test_parse_solo_incluye_las_plataformas_activas() -> None:
    assert steam_service.parse_steam_game(HALF_LIFE)["platforms"] == ["PC", "Mac"]


def test_parse_traduce_los_generos_al_espanol() -> None:
    assert steam_service.parse_steam_game(HALF_LIFE)["genres"] == ["Acción", "Aventura"]


@pytest.mark.parametrize(
    ("raw", "expected_year"),
    [("16 Nov, 2004", 2004), ("Nov 16, 2004", 2004), ("2004", 2004), ("", None), ("proximamente", None)],
)
def test_parse_tolera_los_formatos_de_fecha_de_steam(raw: str, expected_year: int | None) -> None:
    data = {**HALF_LIFE, "release_date": {"date": raw}}
    released = steam_service.parse_steam_game(data)["released"]
    assert (released.year if released else None) == expected_year


def test_parse_no_falla_con_una_ficha_incompleta() -> None:
    parsed = steam_service.parse_steam_game({"name": "Mínimo"})
    assert parsed["name"] == "Mínimo"
    assert parsed["platforms"] == []
    assert parsed["genres"] == []
    assert parsed["released"] is None


# --- Importación -----------------------------------------------------------


def test_importar_crea_el_juego_con_generos_y_etiquetas(
    client: TestClient, db: Session, user: User, monkeypatch
) -> None:
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: HALF_LIFE)

    response = client.post("/api/v1/steam/import/220", headers=auth(client))
    assert response.status_code == 201, response.text

    payload = response.json()
    assert payload["name"] == "Half-Life 2"
    assert {genre["name"] for genre in payload["genres"]} == {"Acción", "Aventura"}
    assert {tag["name"] for tag in payload["tags"]} == {"Un jugador", "Logros de Steam"}


def test_importar_dos_veces_no_duplica(
    client: TestClient, db: Session, user: User, monkeypatch
) -> None:
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: HALF_LIFE)
    headers = auth(client)

    first = client.post("/api/v1/steam/import/220", headers=headers).json()
    second = client.post("/api/v1/steam/import/220", headers=headers).json()

    assert first["id"] == second["id"]
    assert db.query(Game).count() == 1


def test_importar_un_appid_inexistente_da_404(
    client: TestClient, user: User, monkeypatch
) -> None:
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: None)
    response = client.post("/api/v1/steam/import/999999999", headers=auth(client))
    assert response.status_code == 404


def test_importar_requiere_sesion(client: TestClient) -> None:
    assert client.post("/api/v1/steam/import/220").status_code == 401


def test_un_slug_ya_usado_no_rompe_la_importacion(
    client: TestClient, db: Session, user: User, monkeypatch
) -> None:
    """El mismo juego pudo entrar antes desde el dataset local o RAWG."""
    db.add(Game(slug="half-life-2", name="Half-Life 2"))
    db.commit()
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: HALF_LIFE)

    response = client.post("/api/v1/steam/import/220", headers=auth(client))
    assert response.status_code == 201
    assert response.json()["slug"] == "half-life-2-220"


# --- Vinculación de cuenta -------------------------------------------------


def test_vincular_una_cuenta_de_steam(
    client: TestClient, user: User, monkeypatch
) -> None:
    monkeypatch.setattr(
        steam_service,
        "get_player_summary",
        lambda _: {"personaname": "gaben", "avatarfull": "https://cdn.steam.com/a.jpg"},
    )

    response = client.post(
        "/api/v1/steam/link", headers=auth(client), json={"steam_id": "76561197960287930"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["steam_id"] == "76561197960287930"
    assert payload["steam_username"] == "gaben"


def test_no_se_puede_vincular_una_cuenta_ya_tomada(
    client: TestClient, db: Session, user: User, monkeypatch
) -> None:
    db.add(
        User(
            username="otro",
            hashed_password=hash_password(PASSWORD),
            role=UserRole.PLAYER,
            steam_id="76561197960287930",
        )
    )
    db.commit()
    monkeypatch.setattr(steam_service, "get_player_summary", lambda _: None)

    response = client.post(
        "/api/v1/steam/link", headers=auth(client), json={"steam_id": "76561197960287930"}
    )
    assert response.status_code == 409


def test_un_steam_id_mal_formado_es_rechazado(client: TestClient, user: User) -> None:
    response = client.post(
        "/api/v1/steam/link", headers=auth(client), json={"steam_id": "no-soy-un-id"}
    )
    assert response.status_code == 422


def test_biblioteca_sin_clave_devuelve_vacio(
    client: TestClient, user: User, monkeypatch
) -> None:
    """Sin STEAM_API_KEY la integración degrada, no falla."""
    monkeypatch.setattr(steam_service.settings, "STEAM_API_KEY", "")
    response = client.get("/api/v1/steam/owned/76561197960287930", headers=auth(client))
    assert response.status_code == 200
    assert response.json() == []
