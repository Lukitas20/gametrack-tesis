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


def test_estudio_reconoce_mayusculas_y_cuenta_la_muestra_guardada(client, db, data):
    game = data['breve']
    game.developer = '  ESTUDIO TEST  '
    game.reviews_count = 999  # El contador desnormalizado no define la cobertura.
    game.background_image = 'https://example.com/cover.jpg'
    db.add(Review(user_id=data['player'].id, game_id=game.id, content='La historia es magnífica.'))
    db.commit()
    headers = auth(client, 'dev')
    client.post('/api/v1/analytics/process', headers=headers)
    db.add(Review(user_id=None, source='steam', game_id=game.id, content='El rendimiento es pésimo.'))
    db.commit()
    report = client.get('/api/v1/analytics/studio', headers=headers).json()
    assert len(report['juegos']) == 1
    assert report['resenas']['analizadas'] == 1
    assert report['resenas']['pendientes'] == 1
    assert report['juegos'][0]['cantidad_resenas'] == 2
    assert report['juegos'][0]['background_image'] == game.background_image


def test_procesar_resenas_del_estudio_no_modifica_otros_juegos(client, db, data):
    data['breve'].developer = 'ESTUDIO TEST'
    data['largo'].developer = 'Otro estudio'
    own = Review(user_id=data['player'].id, game_id=data['breve'].id, content='La historia es magnífica.')
    other = Review(user_id=data['player'].id, game_id=data['largo'].id, content='El rendimiento es pésimo.')
    db.add_all([own, other]); db.commit()
    response = client.post('/api/v1/analytics/process?studio=estudio%20test&limit=300', headers=auth(client,'dev'))
    assert response.json()['procesadas'] == 1
    db.refresh(own); db.refresh(other)
    assert own.is_analyzed and not other.is_analyzed


def test_estudios_y_asistente_respetan_rol_y_busqueda(client, db, data):
    data['breve'].developer = 'Estudio Test'
    data['largo'].developer = 'ESTUDIO TEST'
    db.commit()
    for path in ['/api/v1/analytics/studios']:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=auth(client,'jugadora')).status_code == 403
    headers = auth(client,'dev')
    studios = client.get('/api/v1/analytics/studios?search=TEST', headers=headers).json()
    assert len(studios) == 1 and studios[0]['games'] == 2
    payload = {'question':'¿Qué conviene mejorar?'}
    assert client.post('/api/v1/analytics/assistant', json=payload).status_code == 401
    assert client.post('/api/v1/analytics/assistant', headers=auth(client,'jugadora'),json=payload).status_code == 403
    report = client.post('/api/v1/analytics/assistant', headers=headers,json=payload).json()
    assert report['title'] == 'Todavía falta evidencia'
    assert report['evidence'] == []
    assert client.post('/api/v1/analytics/assistant', headers=headers,json={'question':'x'*601}).status_code == 422
    assert client.post('/api/v1/analytics/assistant', headers=headers,json={'question':'Ayuda','game_id':999999}).status_code == 404


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


# --- Recomendaciones --------------------------------------------------------


def test_recomendaciones_explican_estrategia_y_aportes(client: TestClient, data: dict) -> None:
    response = client.get(
        "/api/v1/recommendations", headers=auth(client, "jugadora"),
        params={"strategy": "colaborativo", "discovery": "explore"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy"] == "colaborativo"
    assert payload["effective_strategy"] == "popularidad"
    assert payload["discovery"] == "explore"
    assert "no tiene evidencia suficiente" in payload["profile_hint"]
    assert payload["cold_start"] is True
    assert payload["items"]
    for item in payload["items"]:
        assert item["signals"]
        assert item["score"] == pytest.approx(sum(item["components"].values()), abs=1e-8)


def test_recomendaciones_validan_descubrimiento(client: TestClient, data: dict) -> None:
    headers = auth(client, "jugadora")
    assert client.get(
        "/api/v1/recommendations", headers=headers, params={"discovery": "inventado"}
    ).status_code == 422
    assert client.get("/api/v1/recommendations", headers=headers).json()["discovery"] == "balanced"


def test_cambiar_valoracion_actualiza_el_perfil_recomendado(client: TestClient, data: dict) -> None:
    headers = auth(client, "jugadora")
    payload = {"game_id": data["largo"].id, "score": 5}
    assert client.post("/api/v1/ratings", headers=headers, json=payload).status_code == 201
    params = {"strategy": "contenido", "discovery": "familiar", "limit": 1}
    positive = client.get("/api/v1/recommendations", headers=headers, params=params).json()
    assert positive["items"][0]["game"]["id"] == data["incognito"].id

    payload["score"] = 1
    assert client.post("/api/v1/ratings", headers=headers, json=payload).status_code == 201
    negative = client.get("/api/v1/recommendations", headers=headers, params=params).json()
    assert negative["history_size"] == 1
    assert negative["items"][0]["game"]["id"] == data["breve"].id
    assert "valoraste bien" not in negative["items"][0]["reason"]


# --- Frontend ---------------------------------------------------------------


def test_la_raiz_sirve_el_frontend(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "GameTrack" in response.text
    assert "/js/app.js" in response.text


def test_informe_separa_votos_locales_del_indice_importado(client, db, data):
    game = data['breve']
    game.developer = 'Estudio Test'
    game.avg_rating = 4.9
    game.ratings_count = 900000
    db.add(Rating(user_id=data['player'].id, game_id=game.id, score=3.5))
    db.commit()
    headers = auth(client, 'dev')
    report = client.get(f'/api/v1/analytics/games/{game.id}', headers=headers)
    assert report.status_code == 200
    local = report.json()['juego']
    assert local['cantidad_ratings_local'] == 1
    assert local['rating_local_promedio'] == 3.5
    assert local['cantidad_ratings'] == 900000
    row = client.get('/api/v1/analytics/studio', headers=headers).json()['juegos'][0]
    assert row['cantidad_ratings_local'] == 1
    assert row['rating_local_promedio'] == 3.5
    empty = client.get(f"/api/v1/analytics/games/{data['largo'].id}", headers=headers).json()['juego']
    assert empty['cantidad_ratings_local'] == 0
    assert empty['rating_local_promedio'] is None
