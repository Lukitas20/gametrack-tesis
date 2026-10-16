"""Registro y configuración con un único tipo de cuenta."""
import pytest

from tests.test_api import client, db, data, auth  # noqa: F401


def register(client, role="jugador", username="cuenta_nueva"):
    result = client.post("/api/v1/auth/register", json={
        "username": username, "password": "una-frase-larga-123", "role": role,
    })
    assert result.status_code == 201, result.text
    session = result.json()
    return session["user"], {"Authorization": f"Bearer {session['access_token']}"}


@pytest.mark.parametrize("role,step", [("jugador", "player"), ("desarrollador", "studio")])
def test_registration_remembers_chosen_experience(client, role, step):
    user, headers = register(client, role)
    assert user["role"] == role
    assert user["onboarding_step"] == step
    assert "developer_enabled" not in user
    assert client.get("/api/v1/auth/me", headers=headers).json()["onboarding_step"] == step


def test_unknown_role_and_anonymous_changes_are_rejected(client):
    assert client.post("/api/v1/auth/register", json={
        "username": "cuenta_nueva", "password": "una-frase-larga-123", "role": "admin",
    }).status_code == 422
    assert client.put("/api/v1/auth/me/experience", json={"role": "desarrollador"}).status_code == 401


def test_rejected_switch_keeps_library_preferences_and_ratings(client, data):
    user, headers = register(client)
    genre_id = data["breve"].genres[0].id
    game_id = data["breve"].id
    assert client.put("/api/v1/auth/me/preferences", headers=headers, json={"genre_ids": [genre_id]}).status_code == 200
    lists = client.get("/api/v1/me/lists", headers=headers).json()
    list_id = lists[0]["id"]
    assert client.post(f"/api/v1/me/lists/{list_id}/items", headers=headers, json={"game_id": game_id}).status_code == 200
    assert client.post("/api/v1/ratings", headers=headers, json={"game_id": game_id, "score": 4}).status_code == 201

    response = client.put("/api/v1/auth/me/experience", headers=headers, json={
        "role": "desarrollador", "studio": "Indie", "analysis_game_id": game_id,
    })
    assert response.status_code == 403
    finished = client.put("/api/v1/auth/me/experience", headers=headers, json={"role": "jugador", "complete_onboarding": True})
    assert finished.status_code == 200 and finished.json()["user"]["onboarding_step"] is None
    assert [g["id"] for g in finished.json()["user"]["genres"]] == [genre_id]
    assert client.get("/api/v1/me/ratings", headers=headers).json()[0]["score"] == 4
    saved = client.get("/api/v1/me/lists", headers=headers).json()
    assert next(item for item in saved if item["id"] == list_id)["items"][0]["game_id"] == game_id
    assert client.get("/api/v1/analytics/overview", headers=headers).status_code == 403


@pytest.mark.parametrize("payload,status", [
    ({"role": "desarrollador"}, 403),
    ({"role": "desarrollador", "studio": "  "}, 422),
    ({"role": "desarrollador", "studio": "x" * 121}, 422),
    ({"role": "desarrollador", "studio": "Indie", "analysis_game_id": 999999}, 403),
    ({"role": "desarrollador", "studio": "Indie", "analysis_game_id": -1}, 422),
    ({"role": "jugador", "developer_enabled": True}, 422),
    ({"role": "jugador", "studio": "Indie"}, 422),
    ({"role": "admin"}, 422),
])
def test_invalid_configuration_does_not_change_account(client, payload, status):
    _, headers = register(client)
    response = client.put("/api/v1/auth/me/experience", headers=headers, json=payload)
    assert response.status_code == status, response.text
    user = client.get("/api/v1/auth/me", headers=headers).json()
    assert user["role"] == "jugador" and "developer_enabled" not in user
    assert user["onboarding_step"] == "player"
    assert client.get("/api/v1/analytics/overview", headers=headers).status_code == 403


def test_can_finish_without_game_and_configuration_is_personal(client):
    _, headers = register(client, "desarrollador")
    _, other = register(client, username="otra_cuenta")
    saved = client.put("/api/v1/auth/me/experience", headers=headers, json={
        "role": "desarrollador", "studio": "Indie", "complete_onboarding": True,
    })
    assert saved.status_code == 200
    assert saved.json()["user"]["analysis_game_id"] is None
    assert saved.json()["user"]["onboarding_step"] is None
    assert client.get("/api/v1/auth/me", headers=other).json()["role"] == "jugador"


def test_existing_accounts_do_not_repeat_onboarding(client, data):
    for name in ["jugadora", "dev"]:
        headers = auth(client, name)
        assert client.get("/api/v1/auth/me", headers=headers).json()["onboarding_step"] is None
