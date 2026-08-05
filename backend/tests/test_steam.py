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
from app.models import Game, SteamCatalogEntry, SteamCatalogTarget, User, UserRole
from app.services import steam_service
from scripts import sync_steam_catalog

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


def auth(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/auth/login", json={"username": "jugadora", "password": PASSWORD}
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


# --- Catalogo completo y enriquecimiento progresivo -----------------------


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = __import__("httpx").Request("GET", "https://steam.test")
            response = __import__("httpx").Response(self.status_code, request=request)
            raise __import__("httpx").HTTPStatusError(
                "error", request=request, response=response
            )


class FakeClient:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.last_params = None

    def get(self, _url, *, params=None, **_kwargs):
        self.last_params = params
        return self.response


def test_catalogo_oficial_requiere_api_key(monkeypatch) -> None:
    monkeypatch.setattr(steam_service.settings, "STEAM_API_KEY", "")
    with pytest.raises(steam_service.SteamCatalogError, match="STEAM_API_KEY"):
        steam_service.get_store_catalog_page()


def test_ranking_popular_no_acepta_una_respuesta_vacia(monkeypatch) -> None:
    monkeypatch.setattr(steam_service, "_featured_appids", lambda: [])
    monkeypatch.setattr(
        steam_service, "_search_appids_page", lambda start, count: []
    )

    with pytest.raises(steam_service.SteamCatalogError, match="no respondió"):
        steam_service.get_top_seller_appids(limit=10, delay=0)


def test_catalogo_oficial_parsea_paginacion(monkeypatch) -> None:
    fake = FakeClient(
        FakeResponse(
            {
                "response": {
                    "apps": [
                        {"appid": 220, "name": "Half-Life 2", "last_modified": 123},
                        {"appid": 400, "name": "Portal", "last_modified": 456},
                    ],
                    "last_appid": 400,
                    "have_more_results": True,
                }
            }
        )
    )
    monkeypatch.setattr(steam_service.settings, "STEAM_API_KEY", "secreta")
    monkeypatch.setattr(steam_service, "_http_client", lambda: fake)

    page = steam_service.get_store_catalog_page(max_results=2)

    assert [item["appid"] for item in page["apps"]] == [220, 400]
    assert page["last_appid"] == 400
    assert page["have_more_results"] is True
    # La clave viaja al servidor de Steam, nunca al frontend.
    assert fake.last_params["key"] == "secreta"
    assert '"include_games": true' in fake.last_params["input_json"]


def test_indice_crea_fichas_basicas_pendientes(db: Session) -> None:
    stats = steam_service.upsert_store_catalog_page(
        db,
        [
            {"appid": 220, "name": "Half-Life 2", "last_modified": 123},
            {"appid": 400, "name": "Portal", "last_modified": 456},
        ],
    )

    assert stats["created"] == 2
    half_life = steam_service.get_game_by_steam_app_id(db, 220)
    assert half_life is not None
    assert half_life.metadata_status == "pending"
    assert db.get(SteamCatalogEntry, 220).last_modified == 123


def test_indice_vincula_un_juego_existente_por_nombre(db: Session) -> None:
    existing = Game(
        slug="half-life-2",
        name="Half-Life 2",
        description="Ya tenia una ficha local.",
    )
    db.add(existing)
    db.commit()

    stats = steam_service.upsert_store_catalog_page(
        db, [{"appid": 220, "name": "Half-Life 2", "last_modified": 123}]
    )

    assert stats["linked"] == 1
    assert db.query(Game).count() == 1
    assert existing.steam_app_id == 220
    assert existing.metadata_status == "complete"


def test_plan_popular_se_guarda_y_se_reanuda(
    db: Session, monkeypatch
) -> None:
    steam_service.upsert_store_catalog_page(
        db,
        [
            {"appid": 220, "name": "Half-Life 2", "last_modified": 123},
            {"appid": 400, "name": "Portal", "last_modified": 456},
        ],
    )
    monkeypatch.setattr(
        steam_service, "get_top_seller_appids", lambda limit, delay: [400, 220]
    )

    plan_key, targets = sync_steam_catalog.ensure_popular_plan(
        db, limit=2, selection_delay=0
    )

    assert plan_key == "popular-2"
    assert [(target.rank, target.steam_app_id) for target in targets] == [
        (1, 400),
        (2, 220),
    ]
    assert db.query(SteamCatalogTarget).count() == 2

    monkeypatch.setattr(
        steam_service,
        "get_top_seller_appids",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("un plan existente no debe consultar Steam")
        ),
    )
    _, resumed = sync_steam_catalog.ensure_popular_plan(
        db, limit=2, selection_delay=0
    )
    assert [target.steam_app_id for target in resumed] == [400, 220]


def test_abrir_ficha_pendiente_responde_sin_esperar_a_steam(
    client: TestClient, db: Session, monkeypatch
) -> None:
    steam_service.upsert_store_catalog_page(
        db, [{"appid": 220, "name": "Half-Life 2", "last_modified": 123}]
    )
    game = steam_service.get_game_by_steam_app_id(db, 220)
    def should_not_call_steam(_):
        raise AssertionError("GET /games/{id} no debe consultar Steam")

    monkeypatch.setattr(steam_service, "get_app_details", should_not_call_steam)

    response = client.get(f"/api/v1/games/{game.id}")

    assert response.status_code == 200, response.text
    assert response.json()["metadata_status"] == "pending"
    assert response.json()["description"] is None
    assert db.query(Game).count() == 1


def test_enriquecimiento_diferido_completa_ficha_y_resenas(
    client: TestClient, db: Session, monkeypatch
) -> None:
    steam_service.upsert_store_catalog_page(
        db, [{"appid": 220, "name": "Half-Life 2", "last_modified": 123}]
    )
    game = steam_service.get_game_by_steam_app_id(db, 220)
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: HALF_LIFE)
    monkeypatch.setattr(
        steam_service, "get_app_reviews", lambda *a, **k: RAW_REVIEWS["reviews"]
    )
    monkeypatch.setattr(steam_service, "get_player_summaries_batch", lambda ids: {})

    queued = client.post(f"/api/v1/steam/enrich/{game.id}")

    assert queued.status_code == 202, queued.text
    db.expire_all()
    progress = client.get(f"/api/v1/steam/enrich/{game.id}/status")
    detail = client.get(f"/api/v1/games/{game.id}")
    reviews = client.get(f"/api/v1/games/{game.id}/reviews")

    assert progress.json()["status"] == "complete"
    assert progress.json()["reviews_imported"] == 2
    assert detail.json()["metadata_status"] == "complete"
    assert detail.json()["description"] == "Gordon Freeman vuelve a City 17."
    assert len(reviews.json()) == 2

    repeated = client.post(f"/api/v1/steam/enrich/{game.id}")
    db.expire_all()
    repeated_reviews = client.get(f"/api/v1/games/{game.id}/reviews")
    assert repeated.json()["status"] == "complete"
    assert len(repeated_reviews.json()) == 2
