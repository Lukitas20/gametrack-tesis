"""Progreso real, autorización, datos incompletos y cambios de privacidad."""
from copy import deepcopy
import time
import pytest
from app.models import SteamProfileCache
from app.services import steam_achievement_service as service
from app.services import steam_profile_service as profiles
from tests.test_steam_profile import client, db, player, source, SID  # noqa: F401


@pytest.fixture
def achievement_source(monkeypatch):
    data = {
        "playerstats": {"steamID": SID, "success": True, "achievements": [
            {"apiname": "FIRST", "achieved": 1, "unlocktime": 1700000000},
            {"apiname": "SECOND", "achieved": 0, "unlocktime": 0},
            {"apiname": "SECRET", "achieved": 0, "unlocktime": 0}]},
        "game": {"gameName": "Half-Life 2", "availableGameStats": {"achievements": [
            {"name": "FIRST", "displayName": "Primer paso", "description": "Empezar", "hidden": 0,
             "icon": "https://cdn.steamstatic.com/first.png", "icongray": "https://cdn.steamstatic.com/gray.png"},
            {"name": "SECOND", "displayName": "Segundo paso", "hidden": 0},
            {"name": "SECRET", "displayName": "Spoiler", "description": "Spoiler final", "hidden": 1}]}},
    }
    def request(path, params):
        assert params["appid"] == 220
        if "GetPlayerAchievements" in path:
            assert params["steamid"] == SID
            return "ok", {"playerstats": deepcopy(data["playerstats"])}
        return "ok", {"game": deepcopy(data["game"])}
    monkeypatch.setattr(profiles, "_request", request)
    return data


def test_real_totals_hidden_details_and_owner_only(client, db, player, source, achievement_source):
    _, headers = player
    assert client.get("/api/v1/steam/me/achievements/220").status_code == 401
    response = client.get("/api/v1/steam/me/achievements/220", headers=headers)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    progress = response.json()
    assert progress["unlocked"] == 1 and progress["total"] == 3 and progress["percentage"] == 33.3
    assert progress["items"][0]["unlocked_at"] == 1700000000
    secret = next(item for item in progress["items"] if item["hidden"])
    assert secret["name"] == "Logro oculto" and secret["description"] is None and secret["icon"] is None
    assert client.get("/api/v1/steam/me/achievements/999", headers=headers).status_code == 404
    assert client.get("/api/v1/steam/me/achievements/0", headers=headers).status_code == 422
    summary = client.get("/api/v1/steam/me/profile", headers=headers).json()["library"]
    assert summary["items"][0]["achievements"]["percentage"] == 33.3
    assert "achievement_progress" not in summary


def test_unverified_profile_not_allowed(client, db, source):
    from tests.test_steam_auth import register
    account = register(client)
    headers = {"Authorization": "Bearer " + account["access_token"]}
    assert client.get("/api/v1/steam/me/achievements/220", headers=headers).status_code == 403


def test_cache_refresh_outage_and_privacy(client, db, player, source, achievement_source, monkeypatch):
    user, headers = player
    url = "/api/v1/steam/me/achievements/220"
    assert client.get(url, headers=headers).json()["percentage"] == 33.3
    monkeypatch.setattr(service, "fetch_achievements", lambda *_: pytest.fail("La caché debe evitar otra consulta"))
    client.get(url, headers=headers); client.post(url + "/sync", headers=headers)
    cache = db.get(SteamProfileCache, user.id)
    def expire():
        library = deepcopy(cache.library)
        library["achievement_progress"]["220"]["checked_at"] = 0
        cache.library = library; db.commit()
    expire()
    monkeypatch.setattr(service, "fetch_achievements", lambda *_: {"status": "unavailable"})
    stale = client.post(url + "/sync", headers=headers).json()
    assert stale["status"] == "unavailable" and stale["percentage"] == 33.3 and stale["updated_at"]
    expire()
    monkeypatch.setattr(service, "fetch_achievements", lambda *_: {"status": "private"})
    private = client.post(url + "/sync", headers=headers).json()
    assert private["status"] == "private" and private["items"] == [] and private["percentage"] is None
    assert "updated_at" not in private


def test_profile_sync_preserves_only_current_games(client, db, player, source, achievement_source):
    user, headers = player
    client.get("/api/v1/steam/me/achievements/220", headers=headers)
    cache = db.get(SteamProfileCache, user.id); cache.checked_at = 0; db.commit()
    client.post("/api/v1/steam/me/sync", headers=headers)
    assert "220" in cache.library["achievement_progress"]
    source["library"]["items"] = [item for item in source["library"]["items"] if item["appid"] != 220]
    cache.checked_at = 0; db.commit()
    client.post("/api/v1/steam/me/sync", headers=headers)
    assert "220" not in cache.library["achievement_progress"]
    source["library"] = {"status": "private"}; cache.checked_at = 0; db.commit()
    client.post("/api/v1/steam/me/sync", headers=headers)
    assert "achievement_progress" not in cache.library


@pytest.mark.parametrize("change", ["missing", "duplicate", "wrong_owner", "bad_state", "no_schema", "empty_schema"])
def test_incomplete_responses_do_not_invent_progress(achievement_source, change):
    data = achievement_source
    if change == "missing": data["playerstats"]["achievements"].pop()
    if change == "duplicate": data["playerstats"]["achievements"].append(data["playerstats"]["achievements"][0])
    if change == "wrong_owner": data["playerstats"]["steamID"] = "76561198000000009"
    if change == "bad_state": data["playerstats"]["achievements"][0]["achieved"] = True
    if change == "no_schema": data["game"]["availableGameStats"]["achievements"] = None
    if change == "empty_schema": data["game"] = {}
    assert service.fetch_achievements(SID, 220) == {"status": "unavailable"}


@pytest.mark.parametrize("error,status", [("Profile is not public", "private"), ("Requested app has no stats", "unsupported"), ("Unknown error", "unavailable")])
def test_private_unsupported_and_errors_are_distinct(achievement_source, error, status):
    achievement_source["playerstats"].update(success=False, error=error)
    assert service.fetch_achievements(SID, 220) == {"status": status}


def test_zero_unlocked_is_real_not_missing(achievement_source):
    for item in achievement_source["playerstats"]["achievements"]: item["achieved"] = 0
    progress = service.fetch_achievements(SID, 220)
    assert progress["status"] == "ok" and progress["percentage"] == 0 and progress["total"] == 3
