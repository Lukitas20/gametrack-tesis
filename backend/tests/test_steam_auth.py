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
    assert db.get(User, user["id"]).hashed_password is None
    assert client.post("/api/v1/auth/steam/session").status_code == 401
    # Repeat login resolves to the same account, never duplicates it.
    callback(client, assertion(client))
    assert client.post("/api/v1/auth/steam/session").json()["user"]["id"] == user["id"]


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
