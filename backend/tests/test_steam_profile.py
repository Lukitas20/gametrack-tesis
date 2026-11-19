"""Datos Steam privados por identidad, caché, cruces e invitaciones."""
from copy import deepcopy
import time
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select

from tests.test_steam_auth import client, db, register  # noqa: F401
from app.core.config import settings
from app.models import FriendInvite, Friendship, Game, Rating, SteamIdentity, SteamProfileCache, User, UserRole
from app.services import steam_profile_service as service

SID = "76561198000000001"
FRIEND = "76561198000000002"


@pytest.fixture
def player(client, db):
    account = register(client)
    user = db.get(User, account["user"]["id"])
    user.steam_id = SID
    db.add(SteamIdentity(steam_id=SID, user_id=user.id))
    db.commit(); db.expire_all()
    return user, {"Authorization": "Bearer " + account["access_token"]}


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setattr(service.steam_preferences_service, "fetch_game_genres", lambda _: {})
    result = {
        "library": {"status": "ok", "updated_at": int(time.time()), "items": [
            {"appid": 220, "name": "Half-Life 2", "minutes": 125, "recent_minutes": 10, "last_played": 1, "cover": ""},
            {"appid": 400, "name": "Portal", "minutes": 0, "recent_minutes": None, "last_played": None, "cover": ""},
            {"appid": 9999, "name": "Horas privadas", "minutes": None, "recent_minutes": None, "last_played": None, "cover": ""},
        ]},
        "friends": {"status": "ok", "updated_at": int(time.time()), "items": [
            {"steam_id": FRIEND, "name": "Amigo", "avatar": None, "friend_since": 1, "online": True, "playing": None},
            {"steam_id": "76561198000000003", "name": "Invitable", "avatar": None, "friend_since": 1, "online": None, "playing": None},
        ]},
    }
    monkeypatch.setattr(service, "fetch_library", lambda _: deepcopy(result["library"]))
    monkeypatch.setattr(service, "fetch_friends", lambda _: deepcopy(result["friends"]))
    return result


def test_requires_verified_identity_and_authentication(client, db):
    assert client.get("/api/v1/steam/me/profile").status_code == 401
    account = register(client)
    user = db.get(User, account["user"]["id"]); user.steam_id = SID; db.commit()
    headers = {"Authorization": "Bearer " + account["access_token"]}
    assert client.get("/api/v1/steam/me/profile", headers=headers).status_code == 403


def test_library_hours_ratings_and_only_verified_friends(client, db, player, source):
    user, headers = player
    game = Game(name="Half-Life 2", slug="half-life-2", steam_app_id=220)
    db.add(game); db.flush()
    rating = Rating(user_id=user.id, game_id=game.id, score=4.5, hours_played=7)
    other = User(username="amigo", steam_id=FRIEND, role=UserRole.PLAYER)
    unverified = User(username="manual", steam_id="76561198000000003", role=UserRole.PLAYER)
    db.add_all([rating, other, unverified]); db.flush()
    db.add(SteamIdentity(user_id=other.id, steam_id=FRIEND)); db.commit()
    response = client.get("/api/v1/steam/me/profile", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json(); library = data["library"]
    assert len(library["items"]) == 3
    assert library["total_hours"] == 2.1 and library["played_count"] == 1
    assert library["hours_complete"] is False
    assert library["rated_count"] == 1
    assert library["items"][0]["rating"] == 4.5
    assert db.get(Rating, rating.id).hours_played == 7  # no sobreescribe horas manuales
    assert len(db.scalars(select(Rating)).all()) == 1  # no inventa valoraciones
    assert data["friends"]["items"][0]["account"]["username"] == "amigo"
    assert data["friends"]["items"][1]["account"] is None
    assert not db.scalars(select(Friendship)).all()  # consultar no crea amistades


def test_ratings_are_scoped_to_current_user(client, db, player, source):
    user, headers = player
    other = User(username="otro", role=UserRole.PLAYER)
    game = Game(name="HL", slug="hl", steam_app_id=220)
    db.add_all([other, game]); db.flush()
    db.add(Rating(user_id=other.id, game_id=game.id, score=5)); db.commit()
    assert client.get("/api/v1/steam/me/profile", headers=headers).json()["library"]["rated_count"] == 0


def test_cache_rate_limit_and_privacy_changes(client, db, player, source, monkeypatch):
    user, headers = player
    client.get("/api/v1/steam/me/profile", headers=headers)
    counter = Mock(return_value=source["library"])
    monkeypatch.setattr(service, "fetch_library", counter)
    client.get("/api/v1/steam/me/profile", headers=headers)
    client.post("/api/v1/steam/me/sync", headers=headers)
    counter.assert_not_called()
    cache = db.get(SteamProfileCache, user.id); cache.checked_at = 0; db.commit()
    counter.return_value = {"status": "unavailable"}
    failed = client.post("/api/v1/steam/me/sync", headers=headers).json()["library"]
    assert failed["status"] == "unavailable" and len(failed["items"]) == 3 and failed["updated_at"]
    cache.checked_at = 0; db.commit(); counter.return_value = {"status": "private"}
    private = client.post("/api/v1/steam/me/sync", headers=headers).json()["library"]
    assert private["status"] == "private" and private["items"] == []
    assert "updated_at" not in private
    assert db.get(SteamProfileCache, user.id).library == {"status": "private"}


@pytest.mark.parametrize("body,status,count", [
    ({"response": {}}, "private", 0),
    ({"response": {"game_count": 0}}, "ok", 0),
    ({"response": {"game_count": 1}}, "unavailable", 0),
    ({"response": {"game_count": 2, "games": [{"appid": 220}]}}, "unavailable", 0),
    ({"response": {"game_count": 1, "games": [{"appid": 220, "playtime_forever": 0}]}}, "ok", 1),
    ({"response": []}, "unavailable", 0),
])
def test_library_distinguishes_private_empty_and_malformed(monkeypatch, body, status, count):
    monkeypatch.setattr(service, "_request", lambda *_: ("ok", body))
    result = service.fetch_library(SID)
    assert result["status"] == status and len(result.get("items", [])) == count


def test_friend_profiles_are_batched_without_truncation(monkeypatch):
    ids = [str(76561198100000000 + n) for n in range(120)]
    batches = []
    def fetch(path, params):
        if "GetFriendList" in path:
            return "ok", {"friendslist": {"friends": [{"steamid": sid} for sid in ids]}}
        batch = params["steamids"].split(","); batches.append(len(batch))
        return "ok", {"response": {"players": [{"steamid": sid, "personaname": sid} for sid in batch]}}
    monkeypatch.setattr(service, "_request", fetch)
    data = service.fetch_friends(SID)
    assert len(data["items"]) == 120 and batches == [100, 20]
    assert data["partial_profiles"] is False


def test_missing_profiles_do_not_hide_friends(monkeypatch):
    def fetch(path, params):
        if "GetFriendList" in path:
            return "ok", {"friendslist": {"friends": [{"steamid": FRIEND}]}}
        return "unavailable", None
    monkeypatch.setattr(service, "_request", fetch)
    result = service.fetch_friends(SID)
    assert result["partial_profiles"] and len(result["items"]) == 1


def test_api_key_missing_private_http_and_network_errors(monkeypatch):
    monkeypatch.setattr(settings, "STEAM_API_KEY", "")
    assert service.fetch_library(SID)["status"] == "not_configured"
    monkeypatch.setattr(settings, "STEAM_API_KEY", "test-secret")
    fake = Mock()
    monkeypatch.setattr(service.steam_service, "_http_client", lambda: fake)
    fake.get.return_value = httpx.Response(401)
    assert service.fetch_friends(SID)["status"] == "private"
    fake.get.side_effect = httpx.ConnectError("key=test-secret")
    result = service.fetch_library(SID)
    assert result == {"status": "unavailable"} and "test-secret" not in str(result)


def test_invite_lifecycle_does_not_auto_add_and_is_idempotent(client, db):
    inviter = register(client, "inviter")
    headers = {"Authorization": "Bearer " + inviter["access_token"]}
    response = client.post("/api/v1/friends/invites", headers=headers)
    assert response.status_code == 200
    assert response.json()["public"] is False
    url = response.json()["url"]; token = url.rsplit("/", 1)[1]
    assert client.post("/api/v1/friends/invites", headers=headers).json()["url"] == url
    invitation = client.get("/api/v1/friends/invite/" + token).json()
    assert invitation["username"] == "inviter"
    assert "steam_id" not in invitation
    assert client.post(f"/api/v1/friends/invite/{token}/join").status_code == 401
    assert client.post(f"/api/v1/friends/invite/{token}/join", headers=headers).status_code == 400
    guest = register(client, "guest"); guest_headers = {"Authorization": "Bearer " + guest["access_token"]}
    assert not db.scalars(select(Friendship)).all()
    assert client.post(f"/api/v1/friends/invite/{token}/join", headers=guest_headers).json()["state"] == "pending"
    assert client.post(f"/api/v1/friends/invite/{token}/join", headers=guest_headers).json()["state"] == "pending"
    friendship = db.scalar(select(Friendship))
    assert friendship.requester_id == guest["user"]["id"] and friendship.status == "pending"
    assert client.post(f"/api/v1/friends/requests/{friendship.id}/accept", headers=headers).status_code == 200
    assert client.post(f"/api/v1/friends/invite/{token}/join", headers=guest_headers).json()["state"] == "accepted"


def test_invite_expiry_rotation_and_inactive_user(client, db):
    account = register(client); headers = {"Authorization": "Bearer " + account["access_token"]}
    token = client.post("/api/v1/friends/invites", headers=headers).json()["url"].rsplit("/", 1)[1]
    invite = db.get(FriendInvite, token); invite.expires_at = 1; db.commit()
    assert client.get("/api/v1/friends/invite/" + token).status_code == 404
    token2 = client.post("/api/v1/friends/invites", headers=headers).json()["url"].rsplit("/", 1)[1]
    assert token != token2
    user = db.get(User, account["user"]["id"]); user.is_active = False; db.commit()
    assert client.get("/api/v1/friends/invite/" + token2).status_code == 404


def test_known_friend_relationship_changes_without_refetch(client, db, player, source):
    user, headers = player
    other = User(username="amigo", steam_id=FRIEND, role=UserRole.PLAYER)
    db.add(other); db.flush(); db.add(SteamIdentity(user_id=other.id, steam_id=FRIEND)); db.commit()
    client.get("/api/v1/steam/me/profile", headers=headers)
    low, high = sorted((user.id, other.id))
    db.add(Friendship(user_low_id=low, user_high_id=high, requester_id=other.id)); db.commit()
    friend = client.get("/api/v1/steam/me/profile", headers=headers).json()["friends"]["items"][0]
    assert friend["relationship"]["state"] == "incoming"
    client.post(f"/api/v1/friends/requests/{friend['relationship']['request_id']}/accept", headers=headers)
    friend = client.get("/api/v1/steam/me/profile", headers=headers).json()["friends"]["items"][0]
    assert friend["relationship"]["state"] == "accepted"
