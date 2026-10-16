"""Contrato de acceso Steam sin red, y regresiones de registro/login."""
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.test_api import db  # noqa: F401, fixture aislada en memoria
from app.main import app
from app.db.database import get_db
from app.core.config import settings
from app.models import SteamAuthFlow, SteamIdentity, User
from app.api.v1.endpoints import steam_auth as steam

SID = "76561198000000001"


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://localhost:8000")
    monkeypatch.setattr(settings, "STEAM_API_KEY", "")
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app, base_url="http://localhost:8000", follow_redirects=False) as client:
        yield client
    app.dependency_overrides.clear()


def assertion(client, link_headers=None):
    if link_headers:
        response = client.post("/api/v1/auth/steam/link", headers=link_headers)
        assert response.status_code == 200
        location = response.json()["url"]
    else:
        response = client.get("/api/v1/auth/steam/start")
        assert response.status_code == 303
        location = response.headers["location"]
    assert location.startswith(steam.ENDPOINT + "?")
    assert "HttpOnly" in response.headers["set-cookie"]
    args = parse_qs(urlsplit(location).query)
    callback = args["openid.return_to"][0]
    state = parse_qs(urlsplit(callback).query)["state"][0]
    return {
        "state": state, "openid.ns": steam.NAMESPACE, "openid.mode": "id_res",
        "openid.op_endpoint": steam.ENDPOINT, "openid.claimed_id": f"https://steamcommunity.com/openid/id/{SID}",
        "openid.identity": f"https://steamcommunity.com/openid/id/{SID}", "openid.return_to": callback,
        "openid.response_nonce": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + "unique",
        "openid.signed": "op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle",
        "openid.assoc_handle": "handle", "openid.sig": "signature",
    }


def callback(client, params, valid=True):
    response = httpx.Response(200, text=f"ns:{steam.NAMESPACE}\nis_valid:{str(valid).lower()}\n", request=httpx.Request("POST", steam.ENDPOINT))
    with patch.object(httpx.Client, "post", return_value=response) as post:
        result = client.get("/api/v1/auth/steam/callback", params=params)
    return result, post


def register(client, name="jugador"):
    response = client.post("/api/v1/auth/register", json={"username": name, "email": name + "@example.com", "password": "una-frase-larga-123"})
    assert response.status_code == 201, response.text
    return response.json()


def test_register_login_duplicate_and_invalid_password(client):
    user = register(client)
    assert client.post("/api/v1/auth/register", json={"username": "jugador", "password": "una-frase-larga-123"}).status_code == 400
    assert client.post("/api/v1/auth/login", json={"username": "jugador", "password": "wrong"}).status_code == 401
    result = client.post("/api/v1/auth/login", json={"username": "jugador", "password": "una-frase-larga-123"})
    assert result.json()["user"]["id"] == user["user"]["id"]
    assert client.post("/api/v1/auth/register", json={"username": "otro", "password": "🔑" * 20}).status_code == 422


def test_new_steam_user_real_verification_and_one_time_session(client, db):
    params = assertion(client)
    result, post = callback(client, params)
    assert result.headers["location"] == "http://localhost:8000/#/steam-complete", result.headers
    assert "access_token" not in result.headers["location"]
    assert post.call_args.args[0] == steam.ENDPOINT
    assert post.call_args.kwargs["data"]["openid.mode"] == "check_authentication"
    session = client.post("/api/v1/auth/steam/session")
    assert session.status_code == 200, session.text
    user = session.json()["user"]
    assert user["steam_id"] == SID and user["steam_verified"] is True
    assert user["onboarding_step"] == "choose"
    assert db.get(User, user["id"]).hashed_password is None
    assert client.post("/api/v1/auth/steam/session").status_code == 401
    # Repeat login resolves to the same account, never duplicates it.
    callback(client, assertion(client))
    assert client.post("/api/v1/auth/steam/session").json()["user"]["id"] == user["id"]


def test_steam_remembers_configured_experience_on_next_login(client):
    callback(client, assertion(client))
    session = client.post("/api/v1/auth/steam/session").json()
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    configured = client.put("/api/v1/auth/me/experience", headers=headers, json={
        "role": "desarrollador", "studio": "Mi estudio", "complete_onboarding": True,
    })
    assert configured.status_code == 200, configured.text
    callback(client, assertion(client))
    user = client.post("/api/v1/auth/steam/session").json()["user"]
    assert user["role"] == "desarrollador" and user["studio"] == "Mi estudio"
    assert user["onboarding_step"] is None and user["developer_enabled"] is True


@pytest.mark.parametrize("key,value", [
    ("openid.op_endpoint", "https://attacker.example/login"),
    ("openid.return_to", "https://attacker.example/"),
    ("openid.claimed_id", "https://attacker.example/openid/id/76561198000000001"),
    ("openid.identity", "https://steamcommunity.com/openid/id/76561198000000002"),
    ("openid.signed", "identity"), ("openid.ns", "wrong"),
    ("openid.response_nonce", "2000-01-01T00:00:00Zold"),
])
def test_rejects_tampered_assertions_without_network(client, key, value):
    params = assertion(client); params[key] = value
    result, post = callback(client, params)
    assert "steam_error=invalid" in result.headers["location"]
    post.assert_not_called()
    assert client.post("/api/v1/auth/steam/session").status_code == 401


def test_missing_cookie_and_invalid_state(client):
    params = assertion(client)
    client.cookies.clear()
    result, post = callback(client, params)
    assert "steam_error=expired" in result.headers["location"]
    post.assert_not_called()


def test_callback_replay_and_failed_signature(client):
    params = assertion(client)
    state = client.cookies.get(steam.STATE_COOKIE)
    result, post = callback(client, params, valid=False)
    assert "steam_error=invalid" in result.headers["location"]
    client.cookies.set(steam.STATE_COOKIE, state, path=steam.cookie_path())
    result, post = callback(client, params)
    assert "steam_error=expired" in result.headers["location"]
    post.assert_not_called()


def test_cancel_and_expired_flow(client, db):
    params = assertion(client); params["openid.mode"] = "cancel"
    result, post = callback(client, params)
    assert "steam_error=cancelled" in result.headers["location"]
    post.assert_not_called()
    params = assertion(client)
    flow = db.scalar(select(SteamAuthFlow)); flow.expires_at = int(time.time()) - 1; db.commit()
    result, post = callback(client, params)
    assert "steam_error=expired" in result.headers["location"]
    post.assert_not_called()


def test_steam_failure_is_recoverable(client):
    params = assertion(client)
    with patch.object(httpx.Client, "post", side_effect=httpx.ConnectError("unavailable")):
        result = client.get("/api/v1/auth/steam/callback", params=params)
    assert "steam_error=unavailable" in result.headers["location"]


def test_transient_connect_failure_retries_before_verification(client):
    params = assertion(client)
    response = httpx.Response(200, text=f"ns:{steam.NAMESPACE}\nis_valid:true\n",
                              request=httpx.Request("POST", steam.ENDPOINT))
    with patch.object(httpx.Client, "post", side_effect=[httpx.ConnectError("offline"), response]) as post:
        result = client.get("/api/v1/auth/steam/callback", params=params)
    assert result.headers["location"].endswith("/#/steam-complete")
    assert post.call_count == 2
    assert client.post("/api/v1/auth/steam/session").status_code == 200


@pytest.mark.parametrize("error,attempts", [(httpx.ConnectTimeout, 2), (httpx.ReadTimeout, 1),
                                          (httpx.WriteTimeout, 1)])
def test_timeout_requires_fresh_login_without_replaying_assertion(client, error, attempts):
    params = assertion(client)
    state = client.cookies.get(steam.STATE_COOKIE)
    with patch.object(httpx.Client, "post", side_effect=error("slow")) as post:
        result = client.get("/api/v1/auth/steam/callback", params=params)
    assert result.headers["location"].endswith("steam_error=timeout")
    assert post.call_count == attempts
    assert client.post("/api/v1/auth/steam/session").status_code == 401
    client.cookies.set(steam.STATE_COOKIE, state, path=steam.cookie_path())
    replay, post = callback(client, params)
    assert replay.headers["location"].endswith("steam_error=expired")
    post.assert_not_called()
    # Un nuevo clic crea un estado distinto y permite ingresar normalmente.
    client.cookies.clear()  # Retirar la cookie sin dominio inyectada para probar la repetición.
    fresh = assertion(client)
    assert fresh["state"] != params["state"]
    success, post = callback(client, fresh)
    assert success.headers["location"].endswith("/#/steam-complete")
    post.assert_called_once()
    assert client.post("/api/v1/auth/steam/session").status_code == 200


def test_http_error_does_not_repeat_verification(client):
    params = assertion(client)
    response = httpx.Response(503, request=httpx.Request("POST", steam.ENDPOINT))
    with patch.object(httpx.Client, "post", return_value=response) as post:
        result = client.get("/api/v1/auth/steam/callback", params=params)
    assert result.headers["location"].endswith("steam_error=unavailable")
    post.assert_called_once()


def test_legacy_id_cannot_take_over_account_and_can_be_verified(client, db):
    account = register(client)
    user = db.get(User, account["user"]["id"]); user.steam_id = SID; db.commit()
    result, _ = callback(client, assertion(client))
    assert "steam_error=conflict" in result.headers["location"]
    headers = {"Authorization": "Bearer " + account["access_token"]}
    assert client.post("/api/v1/steam/link", headers=headers, json={"steam_id": SID}).status_code == 400
    result, _ = callback(client, assertion(client, headers))
    assert "steam-complete" in result.headers["location"]
    session = client.post("/api/v1/auth/steam/session").json()
    assert session["user"]["id"] == user.id
    assert session["user"]["steam_verified"] is True


def test_cannot_link_another_users_identity(client):
    callback(client, assertion(client)); client.post("/api/v1/auth/steam/session")
    account = register(client)
    result, _ = callback(client, assertion(client, {"Authorization": "Bearer " + account["access_token"]}))
    assert "steam_error=linked" in result.headers["location"]


def test_inactive_account_cannot_login(client, db):
    callback(client, assertion(client)); data = client.post("/api/v1/auth/steam/session").json()
    user = db.get(User, data["user"]["id"]); user.is_active = False; db.commit()
    result, _ = callback(client, assertion(client))
    assert "steam_error=inactive" in result.headers["location"]


def test_exchange_rejects_cross_origin_and_expiration(client, db):
    callback(client, assertion(client))
    assert client.post("/api/v1/auth/steam/session", headers={"Origin": "https://attacker.example"}).status_code == 403
    flow = db.scalar(select(SteamAuthFlow).where(SteamAuthFlow.purpose == "exchange"))
    flow.expires_at = int(time.time()) - 1; db.commit()
    assert client.post("/api/v1/auth/steam/session").status_code == 401


def test_start_canonical_origin_and_link_requires_auth(client):
    assert client.get("http://127.0.0.1:8000/api/v1/auth/steam/start").headers["location"] == "http://localhost:8000/api/v1/auth/steam/start"
    assert client.post("/api/v1/auth/steam/link").status_code == 401


def test_steam_name_is_copied_and_account_stays_the_same(db, monkeypatch):
    monkeypatch.setattr(settings, "STEAM_API_KEY", "test-key")
    profile = {"steamid": SID, "personaname": "  Luké 🎮  ", "avatarfull": "https://avatars.steamstatic.com/example.jpg"}
    monkeypatch.setattr(steam, "get_player_summary", lambda _: profile)
    user = steam.resolve_user(db, SID, None)
    user_id = user.id
    assert user.username == user.steam_username == user.full_name == "Luké 🎮"
    assert user.steam_verified and user.hashed_password is None
    profile["personaname"] = "Otro nombre de Steam"
    again = steam.resolve_user(db, SID, None)
    assert again.id == user_id and again.username == "Luké 🎮"
    assert again.steam_username == "Otro nombre de Steam"


@pytest.mark.parametrize("nickname", ["Nombre compartido", "A", "🎮", "Nombre" * 25])
def test_steam_names_have_unique_bounded_usernames(db, monkeypatch, nickname):
    monkeypatch.setattr(settings, "STEAM_API_KEY", "test-key")
    monkeypatch.setattr(steam, "get_player_summary", lambda sid: {"steamid": sid, "personaname": nickname})
    first = steam.resolve_user(db, SID, None)
    second = steam.resolve_user(db, "76561198000000002", None)
    assert first.username != second.username
    assert 3 <= len(first.username) <= 50 and 3 <= len(second.username) <= 50
    assert first.steam_username == second.steam_username == nickname[:100]
    assert SID not in first.username


def test_new_steam_account_without_profile_uses_friendly_fallback(db, monkeypatch):
    monkeypatch.setattr(settings, "STEAM_API_KEY", "")
    user = steam.resolve_user(db, SID, None)
    assert user.username == "jugador_steam" and user.steam_verified
    monkeypatch.setattr(settings, "STEAM_API_KEY", "test-key")
    monkeypatch.setattr(steam, "get_player_summary", lambda _: {"personaname": "Mi nombre"})
    assert steam.resolve_user(db, SID, None).username == "Mi nombre"


@pytest.mark.parametrize("cached_name", [None, "Mi Steam"])
def test_refresh_session_updates_existing_numeric_steam_account(client, db, monkeypatch, cached_name):
    from app.api.v1.endpoints import auth
    from app.services import steam_service
    user = User(username=f"steam_{SID}", steam_id=SID, steam_username=cached_name, full_name=None)
    db.add(user); db.flush()
    db.add(SteamIdentity(steam_id=SID, user_id=user.id)); db.commit()
    user_id = user.id
    token = auth._token_for(user).access_token
    monkeypatch.setattr(auth, "ensure_steam_preferences", lambda *_: None)
    monkeypatch.setattr(settings, "STEAM_API_KEY", "test-key")
    with patch.object(steam_service, "get_player_summary", return_value={"steamid": SID, "personaname": "Mi Steam"}) as summary:
        response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + token})
    assert summary.call_count == (0 if cached_name else 1)
    assert response.status_code == 200
    assert response.json()["username"] == response.json()["full_name"] == "Mi Steam"
    assert response.json()["id"] == user_id and response.json()["steam_verified"]
    assert db.get(SteamIdentity, SID).user_id == user_id


def test_linked_password_account_keeps_its_login_and_custom_name(db, monkeypatch):
    from app.services.steam_account_service import ensure_steam_account_name
    monkeypatch.setattr(settings, "STEAM_API_KEY", "test-key")
    monkeypatch.setattr(steam, "get_player_summary", lambda _: {"personaname": "Nombre Steam"})
    user = User(username="mi_usuario", hashed_password="existing-password-hash", full_name="Nombre elegido")
    db.add(user); db.commit()
    user = steam.resolve_user(db, SID, user.id)
    ensure_steam_account_name(db, user)
    assert user.username == "mi_usuario" and user.full_name == "Nombre elegido"
    assert user.steam_username == "Nombre Steam" and user.hashed_password == "existing-password-hash"


def test_failed_or_empty_profile_keeps_the_previous_steam_name(db, monkeypatch):
    monkeypatch.setattr(settings, "STEAM_API_KEY", "test-key")
    monkeypatch.setattr(steam, "get_player_summary", lambda _: {"personaname": "Nombre Steam"})
    user = steam.resolve_user(db, SID, None)
    for profile in (None, {"personaname": None}, {"personaname": "   "}):
        monkeypatch.setattr(steam, "get_player_summary", lambda _, result=profile: result)
        assert steam.resolve_user(db, SID, None).steam_username == "Nombre Steam"
    monkeypatch.setattr(steam, "get_player_summary", lambda _: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    assert steam.resolve_user(db, SID, None).username == user.username


def test_username_collision_after_availability_check_recovers(db, monkeypatch):
    from app.services.steam_account_service import assign_steam_username
    db.add(User(username="Jugador")); db.commit()
    original = db.scalar
    calls = 0
    def stale_lookup(*args, **kwargs):
        nonlocal calls
        calls += 1
        return None if calls == 1 else original(*args, **kwargs)
    monkeypatch.setattr(db, "scalar", stale_lookup)
    user = User(steam_id=SID)
    assign_steam_username(db, user, "Jugador")
    db.commit()
    assert user.username == "Jugador_2"
    assert db.scalar(select(User).where(User.username == "Jugador")) is not None


def test_username_reservation_rolls_back_with_the_auth_transaction(db):
    from app.services.steam_account_service import assign_steam_username
    user = User(steam_id=SID)
    assign_steam_username(db, user, "Nombre Steam")
    db.rollback()
    assert db.scalar(select(User).where(User.steam_id == SID)) is None


def test_username_reservation_does_not_swallow_other_pending_conflicts(db):
    from sqlalchemy.exc import IntegrityError
    from app.services.steam_account_service import assign_steam_username
    db.autoflush = False  # Igual que SessionLocal de la aplicación.
    first = User(username="Primero", steam_id=SID)
    second = User(username="jugador_steam")
    db.add_all([first, second]); db.commit()
    second.steam_id = SID
    with pytest.raises(IntegrityError):
        assign_steam_username(db, second, "Otro nombre")
    db.rollback()
    assert second.username == "jugador_steam" and second.steam_id is None
    assert first.steam_id == SID
