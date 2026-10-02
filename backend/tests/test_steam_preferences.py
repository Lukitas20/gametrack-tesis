"""Gustos automáticos desde Steam y prioridad de las elecciones manuales."""
from copy import deepcopy
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select

from tests.test_steam_profile import client, db, player, source  # noqa: F401
from app.models import Game, Genre, Rating, SteamProfileCache, UserPreference
from app.services import steam_preferences_service as tastes, steam_profile_service as profiles


@pytest.fixture
def catalog(db, source):
    action = Genre(slug="accion", name="Acción")
    rpg = Genre(slug="rpg", name="RPG")
    free = Genre(slug="free-to-play", name="Free to play")
    db.add_all([action, rpg, free]); db.flush()
    game = Game(name="Half-Life 2", slug="hl2", steam_app_id=220, genres=[action, free])
    unplayed = Game(name="Portal", slug="portal", steam_app_id=400, genres=[rpg])
    db.add_all([game, unplayed]); db.commit()
    return action, rpg


def test_verified_user_gets_preferences_without_manual_onboarding(client, db, player, source, catalog):
    user, headers = player
    action, rpg = catalog
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["preferences_source"] == "steam"
    assert [genre["id"] for genre in response.json()["genres"]] == [action.id]
    assert not db.scalars(select(Rating)).all()  # horas no se convierten en notas
    assert user.preferences[0].weight == 1.0
    # La sugerencia también funciona con una biblioteca ya cacheada.
    user.preferences = []; user.preferences_source = None; db.commit()
    assert client.get("/api/v1/steam/me/profile", headers=headers).json()["preferences"]["genres"][0]["id"] == action.id


def test_manual_edits_survive_sync_and_can_return_to_steam(client, db, player, source, catalog):
    user, headers = player
    action, rpg = catalog
    client.get("/api/v1/auth/me", headers=headers)
    # Guardar una selección compartida con la inferida no inserta duplicados.
    for selected in ([action.id, rpg.id], [action.id, rpg.id], [rpg.id]):
        result = client.put("/api/v1/auth/me/preferences", headers=headers, json={"genre_ids": selected})
        assert result.status_code == 200, result.text
        assert result.json()["preferences_source"] == "manual"
    cache = db.get(SteamProfileCache, user.id); cache.checked_at = 0; db.commit()
    client.post("/api/v1/steam/me/sync", headers=headers)
    assert [genre.id for genre in user.genres] == [rpg.id]
    result = client.post("/api/v1/auth/me/preferences/steam", headers=headers)
    assert result.status_code == 200 and result.json()["preferences_source"] == "steam"
    assert [genre["id"] for genre in result.json()["genres"]] == [action.id]


def test_automatic_preferences_update_when_playing_different_games(client, db, player, source, catalog):
    user, headers = player
    action, rpg = catalog
    client.get("/api/v1/auth/me", headers=headers)
    source["library"]["items"][0]["minutes"] = 1
    source["library"]["items"][1]["minutes"] = 6000
    db.get(SteamProfileCache, user.id).checked_at = 0; db.commit()
    client.post("/api/v1/steam/me/sync", headers=headers)
    assert user.preferences_source == "steam" and [genre.id for genre in user.genres] == [rpg.id]


@pytest.mark.parametrize("status", ["private", "not_configured", "unavailable"])
def test_unavailable_library_does_not_invent_or_erase_preferences(client, db, player, source, catalog, status):
    user, headers = player
    source["library"] = {"status": status}
    assert client.get("/api/v1/auth/me", headers=headers).json()["genres"] == []
    result = client.post("/api/v1/auth/me/preferences/steam", headers=headers)
    assert result.status_code == 409
    assert user.preferences_source is None and not user.preferences


def test_steam_preference_action_requires_verified_identity(client, db):
    from tests.test_steam_auth import register
    assert client.post("/api/v1/auth/me/preferences/steam").status_code == 401
    account = register(client)
    headers = {"Authorization": "Bearer " + account["access_token"]}
    assert client.post("/api/v1/auth/me/preferences/steam", headers=headers).status_code == 403


def test_new_steam_login_includes_inferred_genres(client, db, source, catalog):
    from tests.test_steam_auth import assertion, callback
    result, _ = callback(client, assertion(client))
    assert "steam-complete" in result.headers["location"]
    session = client.post("/api/v1/auth/steam/session")
    assert session.status_code == 200, session.text
    assert session.json()["user"]["preferences_source"] == "steam"
    assert [genre["id"] for genre in session.json()["user"]["genres"]] == [catalog[0].id]


def test_unknown_games_fetch_genres_with_bounded_requests_and_retry(client, db, player, source, monkeypatch):
    user, headers = player
    source["library"]["items"] = [{"appid": appid, "name": f"Game {appid}", "minutes": 10000 - appid,
                                   "recent_minutes": 0, "last_played": 0, "cover": ""} for appid in range(1, 26)]
    fetch = Mock(return_value={})
    monkeypatch.setattr(tastes, "fetch_game_genres", fetch)
    client.get("/api/v1/auth/me", headers=headers)
    assert fetch.call_args.args[0] == [1, 2, 3, 4, 5]
    client.get("/api/v1/auth/me", headers=headers)
    assert fetch.call_count == 1  # no martillar Steam si fallan sus fichas
    fetch.return_value = {1: ["RPG", "Free to play"], 2: ["Acción"]}
    result = client.post("/api/v1/auth/me/preferences/steam", headers=headers)
    assert result.status_code == 200
    assert {genre["name"] for genre in result.json()["genres"]} == {"RPG", "Acción"}
    assert not db.scalars(select(Rating)).all()


def test_only_most_played_games_and_at_most_four_genres_are_selected(db, player, monkeypatch):
    user, headers = player
    entries = []
    for index in range(25):
        genre = Genre(slug=f"genre-{index}", name=f"Genre {index}")
        db.add(Game(name=f"Game {index}", slug=f"game-{index}", steam_app_id=index + 1, genres=[genre]))
        entries.append({"appid": index + 1, "minutes": 1000 - index})
    db.commit()
    monkeypatch.setattr(tastes, "fetch_game_genres", lambda _: pytest.fail("No debería consultar Steam"))
    assert tastes.assign_preferences(db, user, {"status": "ok", "items": entries})
    db.commit()
    assert len(user.preferences) == 4
    assert {genre.slug for genre in user.genres} == {"genre-0", "genre-1", "genre-2", "genre-3"}


def test_public_metadata_ignores_software_and_masks_network_errors(monkeypatch):
    fake = Mock()
    fake.get.return_value = httpx.Response(200, json={"1": {"success": True, "data": {"type": "software", "genres": [{"description": "Utilities"}]}}}, request=httpx.Request("GET", "https://store.steampowered.com/"))
    monkeypatch.setattr(tastes.steam_service, "_http_client", lambda: fake)
    assert tastes.fetch_game_genres([1]) == {1: []}
    assert "key" not in fake.get.call_args.kwargs["params"]
    fake.get.side_effect = httpx.ConnectError("sensitive-error")
    assert tastes.fetch_game_genres([1]) == {}
