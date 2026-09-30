import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.api.deps import get_current_user
from app.api.v1.endpoints.recommendations import router
from app.db.database import Base, get_db
from app.ml.recommender import invalidate_engine
from app.models import Friendship, Game, Genre, Rating, SteamIdentity, SteamProfileCache, Tag, User, UserPreference
from app.services import gametrack_score_service as service


@pytest.fixture
def sample(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        a, b = Genre(slug="rol", name="Rol"), Genre(slug="estrategia", name="Estrategia")
        coop = Tag(slug="cooperativo-en-linea", name="Cooperativo en línea")
        host, friend, outsider = [User(username=name) for name in ("host", "friend", "outsider")]
        db.add_all([a, b, coop, host, friend, outsider]); db.flush()
        games = [Game(slug=f"game-{i}", name=f"Juego {i}", description="Prueba", genres=[a if i < 3 else b],
            tags=[coop] if i in (0, 1, 3) else [], steam_app_id=100+i, metacritic=[90, 75, None, 99][i],
            avg_rating=4, ratings_count=50, popularity_score=3.8) for i in range(4)]
        db.add_all(games); db.flush()
        db.add(Friendship(user_low_id=host.id, user_high_id=friend.id, requester_id=host.id, status="accepted"))
        db.commit(); invalidate_engine()
        monkeypatch.setattr(service.steam_profile_service, "fetch_library", lambda _: pytest.fail("Red no autorizada en la prueba"))
        yield db, host, friend, outsider, games, a, b
    invalidate_engine(); engine.dispose()


def prefs(db, user, genre):
    db.add(UserPreference(user_id=user.id, genre_id=genre.id)); db.commit(); db.expire(user, ["preferences"]); invalidate_engine()


def verified(db, friend):
    friend.steam_id = "76561198000000001"
    db.add(SteamIdentity(user_id=friend.id, steam_id=friend.steam_id)); db.commit(); db.expire(friend)


def test_no_historial_no_inventa_score(sample):
    db, user, _, _, games, *_ = sample
    result = service.discovery(db, user)
    assert not result.personal_data
    assert all(item.gametrack_score.value is None for item in result.items)
    assert service.game_score(db, user, games[0].id).evidence == "sin_datos"


def test_preferencias_cambian_afinidad_sin_confundirla_con_metacritic(sample):
    db, user, friend, _, games, a, b = sample
    prefs(db, user, a); prefs(db, friend, b)
    assert service.game_score(db, user, games[0].id).value > service.game_score(db, user, games[3].id).value
    assert service.game_score(db, friend, games[3].id).value > service.game_score(db, friend, games[0].id).value
    assert service.game_score(db, user, games[0].id).evidence == "inicial"
    before = service.game_score(db, user, games[0].id).value
    games[0].metacritic = 10; db.commit()
    assert service.game_score(db, user, games[0].id).value == before


def test_critica_no_inventa_notas_y_score_consistente(sample):
    db, user, _, _, games, genre, _ = sample
    prefs(db, user, genre)
    affinity = {item.game.id: item.gametrack_score.value for item in service.discovery(db, user).items}
    critical = service.discovery(db, user, "critics")
    assert games[2].id not in {item.game.id for item in critical.items}
    assert all(item.gametrack_score.value == affinity[item.game.id] for item in critical.items)


def test_valoracion_propia_y_exclusion_de_ya_jugados(sample):
    db, user, _, _, games, *_ = sample
    db.add_all([Rating(user_id=user.id, game_id=games[0].id, score=5), Rating(user_id=user.id, game_id=games[3].id, score=1)])
    db.commit(); invalidate_engine()
    assert service.game_score(db, user, games[0].id).value == 100
    assert service.game_score(db, user, games[3].id).value == 0
    assert service.game_score(db, user, games[0].id).evidence == "valoracion_propia"
    ids = {item.game.id for item in service.discovery(db, user).items}
    assert games[0].id not in ids and games[3].id not in ids


def test_rechaza_no_amigos_antes_de_leer_perfiles(sample, monkeypatch):
    db, user, _, outsider, *_ = sample
    monkeypatch.setattr(service, "get_engine", lambda _: pytest.fail("No debe calcular antes de autorizar"))
    with pytest.raises(HTTPException) as error:
        service.discovery(db, user, "friends", friend_id=outsider.id)
    assert error.value.status_code == 403


def test_biblioteca_publica_solo_multijugador_y_no_filtra_horas(sample, monkeypatch):
    db, user, friend, _, games, *_ = sample
    verified(db, friend)
    monkeypatch.setattr(service.steam_profile_service, "fetch_library", lambda _: {"status":"ok", "items":[{"appid":g.steam_app_id,"minutes":999} for g in games]})
    result = service.discovery(db, user, "friends", friend_id=friend.id)
    assert {item.game.id for item in result.items} == {games[i].id for i in (0, 1, 3)}
    assert all(item.friend_owns and item.multiplayer for item in result.items)
    assert "minutes" not in result.model_dump_json()


@pytest.mark.parametrize("status", ["private", "unavailable", "not_configured"])
def test_biblioteca_no_disponible_no_usa_cache_privada(sample, monkeypatch, status):
    db, user, friend, _, games, *_ = sample
    verified(db, friend)
    db.add(SteamProfileCache(user_id=friend.id, steam_id=friend.steam_id, checked_at=0,
        library={"status":"ok", "items":[{"appid":games[0].steam_app_id}]}, friends={}))
    db.add(Rating(user_id=friend.id, game_id=games[1].id, score=4.5)); db.commit(); invalidate_engine()
    monkeypatch.setattr(service.steam_profile_service, "fetch_library", lambda _: {"status":status})
    result = service.discovery(db, user, "friends", friend_id=friend.id)
    assert [item.game.id for item in result.items] == [games[1].id]
    assert result.items[0].friend_rating == 4.5
    assert not result.items[0].friend_owns


def test_steam_sin_verificar_no_consulta_red_y_no_confunde_rating_con_propiedad(sample):
    db, user, friend, _, games, *_ = sample
    friend.steam_id = "76561198000000001"
    db.add(Rating(user_id=friend.id, game_id=games[0].id, score=5)); db.commit(); invalidate_engine()
    result = service.discovery(db, user, "friends", friend_id=friend.id)
    assert result.library_status == "not_linked"
    assert not result.items[0].friend_owns


def test_api_autenticacion_parametros_y_score(sample):
    db, user, _, _, games, *_ = sample
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        assert client.get("/recommendations/discovery").status_code == 401
        app.dependency_overrides[get_current_user] = lambda: user
        assert client.get("/recommendations/discovery?mode=other").status_code == 422
        assert client.get("/recommendations/discovery?limit=999").status_code == 422
        assert client.get("/recommendations/discovery?mode=friends").status_code == 422
        response = client.get(f"/recommendations/game/{games[0].id}/score")
        assert response.status_code == 200 and response.json()["value"] is None
        assert client.get("/recommendations/game/999999/score").status_code == 404
