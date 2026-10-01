import pytest
from datetime import datetime, timezone
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
            steam_synced_at=datetime.now(timezone.utc), background_image="https://example.test/cover.jpg",
            steam_total_reviews=10000, steam_positive_reviews=9500, steamspy_owners=100000,
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


def test_gustos_y_metacritic_influyen_sin_alterar_afinidad(sample):
    db, user, friend, _, games, a, b = sample
    prefs(db, user, a); prefs(db, friend, b)
    assert service.game_score(db, user, games[0].id).value > service.game_score(db, user, games[3].id).value
    assert service.game_score(db, friend, games[3].id).value > service.game_score(db, friend, games[0].id).value
    assert service.game_score(db, user, games[0].id).evidence == "inicial"
    before = service.game_score(db, user, games[0].id).value
    affinity = service.game_score(db, user, games[0].id).affinity
    games[0].metacritic = 10; db.commit()
    after = service.game_score(db, user, games[0].id)
    assert after.value < before and after.affinity == affinity
    assert after.metascore == 10


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


def test_no_rellena_con_desconocidos_o_datos_internos(sample):
    db, user, _, _, games, genre, _ = sample
    prefs(db, user, genre)
    # Dos reseñas perfectas y una nota crítica alta no prueban alcance.
    games[0].steam_total_reviews = games[0].steam_positive_reviews = 2
    games[0].steamspy_owners = 0
    games[0].ratings_count = 9999999
    games[1].steam_synced_at = None
    games[2].steam_total_reviews = games[2].steam_positive_reviews = 800
    games[2].steamspy_owners = 0
    db.commit(); invalidate_engine()
    assert service.discovery(db, user).items == []


def test_critica_mala_excluida_y_sin_meta_exige_mas_respaldo(sample):
    db, user, _, _, games, genre, _ = sample
    prefs(db, user, genre)
    games[0].metacritic = 40
    games[2].steam_total_reviews = 1500
    games[2].steam_positive_reviews = 1400
    games[2].steamspy_owners = 0
    db.commit(); invalidate_engine()
    assert {i.game.id for i in service.discovery(db, user).items} == {games[1].id}
    games[2].steam_total_reviews = 6000
    games[2].steam_positive_reviews = 5700
    db.commit()
    assert games[2].id in {i.game.id for i in service.discovery(db, user).items}
    score = service.game_score(db, user, games[2].id)
    assert score.metascore is None and "metacritic" not in score.components
    assert any("Sin Metascore" in reason for reason in score.reasons)


def test_preferencias_descartan_generos_ajenos_aunque_meta_sea_alto(sample):
    db, user, _, _, games, genre, _ = sample
    prefs(db, user, genre)
    assert games[3].id not in {i.game.id for i in service.discovery(db, user).items}
    assert games[3].id not in {i.game.id for i in service.discovery(db, user, "critics").items}


def test_historial_positivo_define_generos_sin_preferencias(sample):
    db, user, _, _, games, *_ = sample
    db.add(Rating(user_id=user.id, game_id=games[0].id, score=5)); db.commit(); invalidate_engine()
    ids = {i.game.id for i in service.discovery(db, user).items}
    assert games[0].id not in ids and games[3].id not in ids
    assert games[1].id in ids


def test_componentes_explican_mismo_score_en_ficha_y_seleccion(sample):
    db, user, _, _, games, genre, _ = sample
    prefs(db, user, genre)
    for item in service.discovery(db, user).items:
        score = item.gametrack_score
        assert sum(score.weights.values()) == pytest.approx(1)
        assert score.value == round(sum(score.weights[k]*score.components[k] for k in score.weights)*100)
        assert service.game_score(db, user, item.game.id) == score


def test_wilson_y_fuentes_invalidas_no_inflan_reputacion(sample):
    _, _, _, _, games, *_ = sample
    game = games[0]
    game.steam_total_reviews = game.steam_positive_reviews = 2
    small = service.public_evidence(game)
    game.steam_total_reviews = game.steam_positive_reviews = 10000
    assert service.public_evidence(game)["community"] > small["community"]
    game.steam_positive_reviews = 20000  # fuente inconsistente
    game.steamspy_positive, game.steamspy_negative = 2000, 100
    public = service.public_evidence(game)
    assert public["total"] == 2100 and public["source"] == "Steam vía SteamSpy"


def test_ficha_incompleta_no_aparece_aunque_sea_popular(sample):
    db, user, _, _, games, *_ = sample
    games[0].background_image = None; db.commit()
    assert games[0].id not in {i.game.id for i in service.discovery(db, user).items}


def test_mas_gustos_no_diluyen_la_afinidad_sin_historial(sample):
    db, user, _, _, games, a, b = sample
    prefs(db, user, a)
    before = service.game_score(db, user, games[0].id).affinity
    prefs(db, user, b)
    assert service.game_score(db, user, games[0].id).affinity >= before
    result = service.discovery(db, user)
    assert {item.game.id for item in result.items} == {game.id for game in games}
    assert all(item.gametrack_score.evidence == "inicial" for item in result.items)


def test_perfil_inicial_con_varios_gustos_completa_ocho_sin_rellenar(sample):
    db, user, _, _, games, a, b = sample
    other = [Genre(slug=f"otro-{i}", name=f"Otro {i}") for i in range(20)]
    rare = Genre(slug="violencia", name="Violencia")
    db.add_all([*other, rare]); db.flush()
    games[3].genres.append(rare)
    candidates = [Game(slug=f"popular-{i}", name=f"Popular {i}", description="Prueba",
        genres=[a, *other], steam_app_id=200+i, metacritic=85,
        steam_synced_at=datetime.now(timezone.utc), background_image="https://example.test/cover.jpg",
        steam_total_reviews=10000, steam_positive_reviews=9500, steamspy_owners=100000)
        for i in range(8)]
    unknown = Game(slug="sin-respaldo", name="Sin respaldo", description="Prueba", genres=[a],
        background_image="https://example.test/cover.jpg", metacritic=99,
        steam_total_reviews=2, steam_positive_reviews=2)
    db.add_all([*candidates, unknown]); db.commit()
    for genre in (a, b, rare):
        prefs(db, user, genre)
    result = service.discovery(db, user, limit=8)
    assert len(result.items) == 8
    assert len({item.game.id for item in result.items}) == 8
    assert unknown.id not in {item.game.id for item in result.items}
    assert all(item.gametrack_score.affinity >= 45 for item in result.items)
    assert all(service.reputable(db.get(Game, item.game.id), service.public_evidence(db.get(Game, item.game.id)))
               for item in result.items)


def test_terminos_desconocidos_no_inventan_preferencias(sample):
    db, user, _, _, _, *_ = sample
    genre = Genre(slug="sin-juegos", name="Sin juegos")
    db.add(genre); db.commit(); prefs(db, user, genre)
    assert not service.score_context(db, user)["personal"]
    assert not service.discovery(db, user).items
