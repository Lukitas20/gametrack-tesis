"""Pruebas de la integración con Steam.

No se toca la red: se sustituyen las funciones que llaman a Steam por
respuestas fijas con la forma real de su API.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db.base import Base
from app.db.database import get_db
from app.main import app
from app.models import Game, Review, User, UserRole
from app.services import steam_service

PASSWORD = "demo1234"

# Forma real de la respuesta de store.steampowered.com/api/appdetails.
HALF_LIFE = {
    "type": "game",
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


@pytest.fixture
def developer(db: Session) -> User:
    created = User(
        username="desarrolladora",
        hashed_password=hash_password(PASSWORD),
        role=UserRole.DEVELOPER,
        studio="Estudio Test",
    )
    db.add(created)
    db.commit()
    return created


def auth(client: TestClient, username: str = "jugadora") -> dict:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(autouse=True)
def no_review_network(monkeypatch):
    """Por defecto, importar un juego no trae reseñas de Steam: los tests que
    sí quieren probar esa parte pisan este mock explícitamente."""
    monkeypatch.setattr(steam_service, "get_app_reviews", lambda *a, **k: [])


# Forma real de la respuesta de store.steampowered.com/appreviews/{appid}.
RAW_REVIEWS = {
    "success": 1,
    "reviews": [
        {
            "recommendationid": "1",
            "author": {"steamid": "1", "playtime_at_review": 600},
            "review": "La historia es magnifica pero el rendimiento es un desastre.",
            "voted_up": False,
            "votes_up": 12,
        },
        {
            "recommendationid": "2",
            "author": {"steamid": "2", "playtime_at_review": 1200},
            "review": "Un juego excelente, lo recomiendo sin dudar.",
            "voted_up": True,
            "votes_up": 3,
        },
    ],
}


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


# --- Reseñas reales ---------------------------------------------------------


def test_importar_trae_las_resenas_reales_y_las_analiza(
    client: TestClient, db: Session, user: User, monkeypatch
) -> None:
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: HALF_LIFE)
    monkeypatch.setattr(steam_service, "get_app_reviews", lambda *a, **k: RAW_REVIEWS["reviews"])
    monkeypatch.setattr(steam_service, "get_player_summaries_batch", lambda ids: {})

    game_id = client.post("/api/v1/steam/import/220", headers=auth(client)).json()["id"]

    reviews = client.get(f"/api/v1/games/{game_id}/reviews").json()
    assert len(reviews) == 2
    assert all(r["source"] == "steam" for r in reviews)
    assert all(r["user_id"] is None for r in reviews)
    assert all(r["is_analyzed"] for r in reviews)
    # La reseña negativa sobre el rendimiento debe traer al menos un aspecto.
    negative = next(r for r in reviews if r["is_recommended"] is False)
    assert negative["aspects"]


def test_importar_resenas_usa_el_nombre_de_perfil_de_steam(
    client: TestClient, db: Session, user: User, monkeypatch
) -> None:
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: HALF_LIFE)
    monkeypatch.setattr(steam_service, "get_app_reviews", lambda *a, **k: RAW_REVIEWS["reviews"])
    monkeypatch.setattr(
        steam_service,
        "get_player_summaries_batch",
        lambda ids: {"1": {"personaname": "gaben"}},
    )

    game_id = client.post("/api/v1/steam/import/220", headers=auth(client)).json()["id"]
    reviews = client.get(f"/api/v1/games/{game_id}/reviews").json()

    names = {r["author_name"] for r in reviews}
    assert "gaben" in names
    assert "Jugador de Steam" in names


def test_sin_resenas_reales_no_rompe_la_importacion(
    client: TestClient, db: Session, user: User, monkeypatch
) -> None:
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: HALF_LIFE)
    # ``no_review_network`` ya deja get_app_reviews devolviendo [].

    response = client.post("/api/v1/steam/import/220", headers=auth(client))
    assert response.status_code == 201
    assert response.json()["reviews_count"] == 0


# --- Fichas pendientes: Game.is_enriched ------------------------------------


def test_juego_sin_steam_esta_enriquecido() -> None:
    assert Game(name="Local", slug="local").is_enriched is True


def test_ficha_pendiente_no_esta_enriquecida() -> None:
    assert Game(steam_app_id=620, slug="portal-2", name="Portal 2").is_enriched is False


def test_juego_ya_sincronizado_esta_enriquecido() -> None:
    game = Game(
        steam_app_id=620,
        slug="portal-2",
        name="Portal 2",
        steam_synced_at=datetime.now(timezone.utc),
    )
    assert game.is_enriched is True


# --- Fichas pendientes: refresh_game -----------------------------------------


def test_refresh_enriquece_una_ficha_pendiente(db: Session, monkeypatch) -> None:
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: HALF_LIFE)
    monkeypatch.setattr(steam_service, "get_app_reviews", lambda *a, **k: RAW_REVIEWS["reviews"])
    monkeypatch.setattr(steam_service, "get_player_summaries_batch", lambda ids: {})

    game = Game(steam_app_id=220, slug="half-life-2", name="Half-Life 2")
    db.add(game)
    db.commit()
    assert game.is_enriched is False

    result = steam_service.refresh_game(db, game)

    assert result is True
    assert game.is_enriched is True
    assert game.description == "Gordon Freeman vuelve a City 17."
    assert {g.name for g in game.genres} == {"Acción", "Aventura"}
    assert game.reviews_count == 2


def test_refresh_borra_una_ficha_pendiente_que_no_es_un_juego(db: Session, monkeypatch) -> None:
    """GetAppList/SteamSpy mezclan DLC, bandas sonoras y software con
    juegos: si al pedir la ficha Steam dice que no es un juego, no tiene
    sentido dejarla pendiente para siempre."""
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: None)

    game = Game(steam_app_id=99999, slug="no-es-un-juego", name="DLC Cualquiera")
    db.add(game)
    db.commit()
    game_id = game.id

    result = steam_service.refresh_game(db, game)

    assert result is False
    assert db.get(Game, game_id) is None


def test_refresh_no_borra_un_juego_ya_enriquecido_si_steam_falla(
    db: Session, monkeypatch
) -> None:
    """A diferencia de una ficha pendiente, un juego que ya tenía ficha y
    reseñas reales no se borra si Steam falla puntualmente: perder esa
    evidencia sería peor que dejarla desactualizada."""
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: HALF_LIFE)
    game = Game(steam_app_id=220, slug="half-life-2", name="Half-Life 2")
    db.add(game)
    db.commit()
    steam_service.refresh_game(db, game)
    assert game.is_enriched is True
    game_id = game.id

    monkeypatch.setattr(steam_service, "get_app_details", lambda _: None)
    result = steam_service.refresh_game(db, game)

    assert result is True
    assert db.get(Game, game_id) is not None


def test_refresh_no_duplica_resenas_ya_importadas(db: Session, monkeypatch) -> None:
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: HALF_LIFE)
    monkeypatch.setattr(steam_service, "get_app_reviews", lambda *a, **k: RAW_REVIEWS["reviews"])
    monkeypatch.setattr(steam_service, "get_player_summaries_batch", lambda ids: {})

    game = Game(steam_app_id=220, slug="half-life-2", name="Half-Life 2")
    db.add(game)
    db.commit()

    steam_service.refresh_game(db, game)
    first_count = db.scalar(select(func.count(Review.id)))

    # refresh_game no depende del TTL (eso lo decide maybe_refresh): se
    # puede llamar de nuevo directamente para probar que no duplica.
    steam_service.refresh_game(db, game)
    second_count = db.scalar(select(func.count(Review.id)))

    assert first_count == 2
    assert second_count == first_count


# --- Fichas pendientes: maybe_refresh (perezoso, con TTL) --------------------


def test_maybe_refresh_ignora_juegos_que_no_son_de_steam(db: Session, monkeypatch) -> None:
    called = False

    def fake_get_app_details(_appid):
        nonlocal called
        called = True
        return HALF_LIFE

    monkeypatch.setattr(steam_service, "get_app_details", fake_get_app_details)

    game = Game(slug="local", name="Juego Local")
    db.add(game)
    db.commit()

    assert steam_service.maybe_refresh(db, game) is True
    assert called is False


def test_maybe_refresh_enriquece_una_ficha_pendiente_sin_esperar_el_ttl(
    db: Session, monkeypatch
) -> None:
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: HALF_LIFE)

    game = Game(steam_app_id=220, slug="half-life-2", name="Half-Life 2")
    db.add(game)
    db.commit()

    assert steam_service.maybe_refresh(db, game) is True
    assert game.is_enriched is True


def test_maybe_refresh_no_pega_a_steam_si_esta_fresco(db: Session, monkeypatch) -> None:
    called = False

    def fake_get_app_details(_appid):
        nonlocal called
        called = True
        return HALF_LIFE

    monkeypatch.setattr(steam_service, "get_app_details", fake_get_app_details)

    game = Game(
        steam_app_id=220,
        slug="half-life-2",
        name="Half-Life 2",
        steam_synced_at=datetime.now(timezone.utc),
    )
    db.add(game)
    db.commit()

    assert steam_service.maybe_refresh(db, game) is True
    assert called is False


def test_maybe_refresh_vuelve_a_pegar_a_steam_pasado_el_ttl(db: Session, monkeypatch) -> None:
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: HALF_LIFE)
    monkeypatch.setattr(steam_service.settings, "STEAM_SYNC_TTL_MINUTES", 60)

    stale = datetime.now(timezone.utc) - timedelta(minutes=61)
    game = Game(
        steam_app_id=220, slug="half-life-2", name="Half-Life 2", steam_synced_at=stale
    )
    db.add(game)
    db.commit()

    assert steam_service.maybe_refresh(db, game) is True
    # SQLite devuelve el datetime sin tzinfo al releerlo; se normaliza antes
    # de comparar, igual que hace maybe_refresh internamente.
    refreshed = game.steam_synced_at
    if refreshed.tzinfo is None:
        refreshed = refreshed.replace(tzinfo=timezone.utc)
    assert refreshed > stale


def test_maybe_refresh_no_rompe_si_steam_explota_de_forma_inesperada(
    db: Session, monkeypatch
) -> None:
    def boom(_appid):
        raise RuntimeError("fallo inesperado, no un simple problema de red")

    monkeypatch.setattr(steam_service, "get_app_details", boom)

    game = Game(steam_app_id=220, slug="half-life-2", name="Half-Life 2")
    db.add(game)
    db.commit()

    # No debe propagar la excepción: el refresco es una mejora, no la
    # respuesta en sí. El juego se sirve como estaba.
    assert steam_service.maybe_refresh(db, game) is True
    assert game.is_enriched is False


# --- Fichas pendientes: efecto en los endpoints ------------------------------


def test_abrir_una_ficha_pendiente_que_no_es_un_juego_da_404(
    client: TestClient, db: Session, monkeypatch
) -> None:
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: None)

    game = Game(steam_app_id=99999, slug="no-es-un-juego", name="DLC Cualquiera")
    db.add(game)
    db.commit()
    game_id = game.id

    response = client.get(f"/api/v1/games/{game_id}")

    assert response.status_code == 404
    assert db.get(Game, game_id) is None


def test_abrir_una_ficha_pendiente_la_enriquece_en_el_momento(
    client: TestClient, db: Session, monkeypatch
) -> None:
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: HALF_LIFE)

    game = Game(steam_app_id=220, slug="half-life-2", name="Half-Life 2")
    db.add(game)
    db.commit()
    game_id = game.id

    response = client.get(f"/api/v1/games/{game_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["is_enriched"] is True
    assert body["description"] == "Gordon Freeman vuelve a City 17."


def test_analitica_de_una_ficha_pendiente_que_no_es_un_juego_da_404(
    client: TestClient, db: Session, developer: User, monkeypatch
) -> None:
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: None)

    game = Game(steam_app_id=99999, slug="no-es-un-juego", name="DLC Cualquiera")
    db.add(game)
    db.commit()
    game_id = game.id

    response = client.get(
        f"/api/v1/analytics/games/{game_id}", headers=auth(client, "desarrolladora")
    )

    assert response.status_code == 404
    assert db.get(Game, game_id) is None


# --- Fichas pendientes: get_app_list (índice vía SteamSpy) ------------------


def _steamspy_page(start: int, count: int) -> dict:
    return {
        str(i): {"appid": i, "name": f"Juego {i}"} for i in range(start, start + count)
    }


def test_get_app_list_pagina_hasta_una_pagina_incompleta(monkeypatch) -> None:
    pages = {0: _steamspy_page(0, 1000), 1: _steamspy_page(1000, 300)}
    calls: list[int] = []

    def fake_fetch(page):
        calls.append(page)
        return pages.get(page)

    monkeypatch.setattr(steam_service, "_fetch_steamspy_page", fake_fetch)

    apps = steam_service.get_app_list(delay=0)

    assert calls == [0, 1]  # se detiene solo al ver una página con <1000
    assert len(apps) == 1300


def test_get_app_list_se_corta_si_steamspy_deja_de_responder(monkeypatch) -> None:
    """SteamSpy no es de Steam: si empieza a fallar (o responde "Too many
    connections" y se agotan los reintentos de _fetch_steamspy_page), hay
    que quedarse con lo que se pudo juntar en vez de perder todo."""

    def fake_fetch(page):
        return _steamspy_page(0, 1000) if page == 0 else None

    monkeypatch.setattr(steam_service, "_fetch_steamspy_page", fake_fetch)

    apps = steam_service.get_app_list(delay=0)

    assert len(apps) == 1000


def test_get_app_list_respeta_max_pages(monkeypatch) -> None:
    calls: list[int] = []

    def fake_fetch(page):
        calls.append(page)
        return _steamspy_page(page * 1000, 1000)

    monkeypatch.setattr(steam_service, "_fetch_steamspy_page", fake_fetch)

    apps = steam_service.get_app_list(delay=0, max_pages=2)

    assert calls == [0, 1]
    assert len(apps) == 2000
