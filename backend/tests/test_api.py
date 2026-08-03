"""Pruebas de la API sobre una base en memoria.

Se sustituye la dependencia ``get_db`` por una sesión propia, así los tests no
tocan la base de desarrollo ni dependen de que el seed se haya ejecutado.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.database import get_db
from app.main import app
from app.ml.recommender import invalidate_engine
from app.models import Game, Genre, Rating, Review, Tag, User, UserRole
from app.core.security import hash_password

PASSWORD = "demo1234"


@pytest.fixture
def db() -> Session:
    # StaticPool mantiene la misma conexión en memoria entre la app y el test.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    invalidate_engine()


@pytest.fixture
def client(db: Session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def data(db: Session) -> dict:
    accion = Genre(slug="accion", name="Acción")
    indie = Genre(slug="indie", name="Indie")
    corto = Tag(slug="corto", name="Corto")
    coop = Tag(slug="cooperativo", name="Cooperativo")
    db.add_all([accion, indie, corto, coop])

    breve = Game(slug="breve", name="Juego Breve", playtime_hours=8, avg_rating=4.5, ratings_count=10)
    breve.genres = [indie]
    breve.tags = [corto, coop]

    largo = Game(slug="largo", name="Juego Largo", playtime_hours=90, avg_rating=4.0, ratings_count=20)
    largo.genres = [accion]
    largo.tags = [coop]

    # Sin duración conocida: no debe descartarse al filtrar por duración máxima.
    incognito = Game(slug="incognito", name="Duración Desconocida", playtime_hours=None)
    incognito.genres = [accion]

    player = User(
        username="jugadora", hashed_password=hash_password(PASSWORD), role=UserRole.PLAYER
    )
    developer = User(
        username="dev",
        hashed_password=hash_password(PASSWORD),
        role=UserRole.DEVELOPER,
        studio="Estudio Test",
    )

    db.add_all([breve, largo, incognito, player, developer])
    db.commit()
    return {"breve": breve, "largo": largo, "incognito": incognito, "player": player, "dev": developer}


def auth(client: TestClient, username: str) -> dict:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --- Catálogo ---------------------------------------------------------------


def test_filtro_por_etiqueta(client: TestClient, data: dict) -> None:
    response = client.get("/api/v1/games", params={"tag": "corto"})
    assert [item["name"] for item in response.json()["items"]] == ["Juego Breve"]


def test_filtro_por_duracion_incluye_los_de_duracion_desconocida(
    client: TestClient, data: dict
) -> None:
    names = {
        item["name"] for item in client.get("/api/v1/games", params={"max_playtime": 20}).json()["items"]
    }
    assert names == {"Juego Breve", "Duración Desconocida"}


def test_tags_descarta_las_poco_usadas(client: TestClient, data: dict) -> None:
    # "cooperativo" está en dos juegos; "corto" sólo en uno.
    slugs = {tag["slug"] for tag in client.get("/api/v1/tags", params={"min_games": 2}).json()}
    assert slugs == {"cooperativo"}


def test_busqueda_por_desarrollador_y_nombre(client: TestClient, data: dict) -> None:
    assert client.get("/api/v1/games", params={"search": "breve"}).json()["total"] == 1
    assert client.get("/api/v1/games", params={"search": "nada"}).json()["total"] == 0


# --- Listas -----------------------------------------------------------------


def test_listas_del_sistema_se_crean_al_consultarlas(client: TestClient, data: dict) -> None:
    headers = auth(client, "jugadora")
    lists = client.get("/api/v1/me/lists", headers=headers).json()
    assert {item["list_type"] for item in lists} == {"favoritos", "jugando", "pendientes"}


def test_agregar_y_quitar_de_una_lista(client: TestClient, data: dict) -> None:
    headers = auth(client, "jugadora")
    favorites = next(
        item for item in client.get("/api/v1/me/lists", headers=headers).json()
        if item["list_type"] == "favoritos"
    )
    game_id = data["breve"].id

    added = client.post(
        f"/api/v1/me/lists/{favorites['id']}/items", headers=headers, json={"game_id": game_id}
    )
    assert [item["game_id"] for item in added.json()["items"]] == [game_id]

    assert client.get(f"/api/v1/me/lists/containing/{game_id}", headers=headers).json() == [
        favorites["id"]
    ]

    removed = client.delete(
        f"/api/v1/me/lists/{favorites['id']}/items/{game_id}", headers=headers
    )
    assert removed.json()["items"] == []


def test_agregar_dos_veces_no_duplica(client: TestClient, data: dict) -> None:
    headers = auth(client, "jugadora")
    lists = client.get("/api/v1/me/lists", headers=headers).json()
    list_id = lists[0]["id"]
    payload = {"game_id": data["largo"].id}

    client.post(f"/api/v1/me/lists/{list_id}/items", headers=headers, json=payload)
    second = client.post(f"/api/v1/me/lists/{list_id}/items", headers=headers, json=payload)
    assert len(second.json()["items"]) == 1


def test_no_se_puede_tocar_la_lista_de_otro(client: TestClient, data: dict) -> None:
    owner = auth(client, "jugadora")
    list_id = client.get("/api/v1/me/lists", headers=owner).json()[0]["id"]

    intruder = auth(client, "dev")
    response = client.post(
        f"/api/v1/me/lists/{list_id}/items", headers=intruder, json={"game_id": data["breve"].id}
    )
    assert response.status_code == 404


def test_lista_con_nombre_repetido_es_rechazada(client: TestClient, data: dict) -> None:
    headers = auth(client, "jugadora")
    assert client.post("/api/v1/me/lists", headers=headers, json={"name": "Retro"}).status_code == 201
    assert client.post("/api/v1/me/lists", headers=headers, json={"name": "retro"}).status_code == 400


def test_agregar_un_juego_inexistente_da_404(client: TestClient, data: dict) -> None:
    headers = auth(client, "jugadora")
    list_id = client.get("/api/v1/me/lists", headers=headers).json()[0]["id"]
    response = client.post(
        f"/api/v1/me/lists/{list_id}/items", headers=headers, json={"game_id": 9999}
    )
    assert response.status_code == 404


# --- Roles y analítica ------------------------------------------------------


def test_analitica_es_solo_para_desarrolladores(client: TestClient, data: dict) -> None:
    assert client.get("/api/v1/analytics/overview").status_code == 401
    assert (
        client.get("/api/v1/analytics/overview", headers=auth(client, "jugadora")).status_code == 403
    )
    assert client.get("/api/v1/analytics/overview", headers=auth(client, "dev")).status_code == 200


def test_estudio_devuelve_el_reparto_por_juego(client: TestClient, db: Session, data: dict) -> None:
    """El frontend necesita el reparto completo, no sólo el neto."""
    data["breve"].developer = "Estudio Test"
    db.add(
        Review(
            user_id=data["player"].id,
            game_id=data["breve"].id,
            content="Corre impecable, ni un solo tirón.",
        )
    )
    db.commit()

    headers = auth(client, "dev")
    client.post("/api/v1/analytics/process", headers=headers)
    studio = client.get("/api/v1/analytics/studio", headers=headers).json()

    row = studio["juegos"][0]
    assert row["nombre"] == "Juego Breve"
    assert sum(row["distribucion"].values()) == row["resenas_analizadas"] == 1
    assert row["distribucion"]["positivo"] == 1


# --- Análisis de texto suelto ----------------------------------------------


def test_analizar_texto_no_requiere_sesion(client: TestClient, data: dict) -> None:
    response = client.post(
        "/api/v1/reviews/analyze",
        json={"content": "La historia es magnífica pero el rendimiento es un desastre."},
    )
    assert response.status_code == 200
    payload = response.json()
    aspects = {item["aspect"]: item["sentiment"] for item in payload["aspects"]}
    assert aspects["historia"] == "positivo"
    assert aspects["optimizacion"] == "negativo"


def test_analizar_texto_vacio_es_rechazado(client: TestClient, data: dict) -> None:
    assert client.post("/api/v1/reviews/analyze", json={"content": ""}).status_code == 422


# --- Frontend ---------------------------------------------------------------


def test_la_raiz_sirve_el_frontend(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "GameTrack" in response.text
    assert "/js/app.js" in response.text
