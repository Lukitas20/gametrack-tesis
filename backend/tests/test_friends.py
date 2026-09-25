"""Consentimiento, privacidad y límites de las recomendaciones entre amigos."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints.friends import router
from app.core.security import create_access_token
from app.db.base import Base
from app.db.database import get_db
from app.models import Friendship, User, UserRole
from app.services.friendship_service import resolve_group_members

BASE = "/api/v1/friends"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def users(db):
    result = [
        User(username="ana", email="privado@example.org", hashed_password="private", steam_id="12345"),
        User(username="bruno", full_name="Bruno", avatar_url="/avatar.png"),
        User(username="carla"),
        User(username="dev", role=UserRole.DEVELOPER),
        User(username="inactive", is_active=False),
    ]
    db.add_all(result)
    db.commit()
    return result


@pytest.fixture
def client(db):
    # Una app de pruebas mínima evita que el lifespan cree tablas en la base real.
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client


def _headers(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': str(user.id)})}"}


def _request(client, sender, recipient):
    response = client.post(
        f"{BASE}/requests", headers=_headers(sender), json={"username": recipient.username}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _befriend(client, sender, recipient):
    request_id = _request(client, sender, recipient)
    response = client.post(f"{BASE}/requests/{request_id}/accept", headers=_headers(recipient))
    assert response.status_code == 200, response.text
    return request_id


def test_solicitud_aceptada_es_simetrica_y_solo_expone_identidad_publica(client, users, db):
    ana, bruno = users[:2]
    request_id = _request(client, ana, bruno)
    outgoing = client.get(BASE, headers=_headers(ana)).json()
    incoming = client.get(BASE, headers=_headers(bruno)).json()
    assert outgoing["friends"] == incoming["friends"] == []
    assert outgoing["outgoing"][0]["id"] == incoming["incoming"][0]["id"] == request_id
    assert outgoing["outgoing"][0]["user"]["id"] == bruno.id
    public_ana = incoming["incoming"][0]["user"]
    assert set(public_ana) == {"id", "username", "full_name", "avatar_url"}
    assert "privado" not in str(incoming)
    assert client.post(f"{BASE}/requests/{request_id}/accept", headers=_headers(bruno)).status_code == 200
    for viewer, other in [(ana, bruno), (bruno, ana)]:
        result = client.get(BASE, headers=_headers(viewer)).json()
        assert [friend["id"] for friend in result["friends"]] == [other.id]
        assert result["incoming"] == result["outgoing"] == []
    assert db.get(Friendship, request_id).accepted_at is not None


@pytest.mark.parametrize("actor_index", [0, 1])
def test_quien_envia_cancela_y_quien_recibe_rechaza(client, users, actor_index, db):
    request_id = _request(client, users[0], users[1])
    response = client.delete(f"{BASE}/requests/{request_id}", headers=_headers(users[actor_index]))
    assert response.status_code == 204
    assert response.content == b""
    assert db.get(Friendship, request_id) is None
    # Se puede volver a invitar; un ID viejo nunca actúa sobre la nueva invitación.
    new_id = _request(client, users[0], users[1])
    assert new_id > request_id
    assert client.delete(f"{BASE}/requests/{request_id}", headers=_headers(users[0])).status_code == 404


def test_ni_emisor_ni_tercero_pueden_aceptar_solicitud(client, users, db):
    request_id = _request(client, users[0], users[1])
    assert client.post(f"{BASE}/requests/{request_id}/accept", headers=_headers(users[0])).status_code == 403
    assert client.post(f"{BASE}/requests/{request_id}/accept", headers=_headers(users[2])).status_code == 404
    assert client.delete(f"{BASE}/requests/{request_id}", headers=_headers(users[2])).status_code == 404
    assert db.get(Friendship, request_id).status == "pending"


def test_duplicado_y_solicitud_cruzada_no_aceptan_implicitamente(client, users, db):
    request_id = _request(client, users[0], users[1])
    for sender, recipient in [(users[0], users[1]), (users[1], users[0])]:
        result = client.post(f"{BASE}/requests", headers=_headers(sender), json={"username": recipient.username})
        assert result.status_code == 409
    assert db.get(Friendship, request_id).status == "pending"
    assert len(db.scalars(select(Friendship)).all()) == 1


def test_amigos_no_pueden_enviarse_otra_solicitud(client, users):
    request_id = _befriend(client, users[0], users[1])
    assert client.post(f"{BASE}/requests", headers=_headers(users[0]), json={"username": users[1].username}).status_code == 409
    assert client.post(f"{BASE}/requests/{request_id}/accept", headers=_headers(users[1])).status_code == 404
    assert client.delete(f"{BASE}/requests/{request_id}", headers=_headers(users[0])).status_code == 404


@pytest.mark.parametrize("actor_index", [0, 1])
def test_cualquier_amigo_puede_quitar_amistad_y_revoca_grupo(client, users, db, actor_index):
    _befriend(client, users[0], users[1])
    actor, other = users[actor_index], users[1 - actor_index]
    assert client.delete(f"{BASE}/{other.id}", headers=_headers(users[2])).status_code == 404
    assert client.delete(f"{BASE}/{other.id}", headers=_headers(actor)).status_code == 204
    assert client.get(BASE, headers=_headers(other)).json()["friends"] == []
    with pytest.raises(HTTPException) as error:
        resolve_group_members(db, actor, [other.id])
    assert error.value.status_code == 403


def test_remove_friend_no_elimina_solicitudes_pendientes(client, users, db):
    request_id = _request(client, users[0], users[1])
    assert client.delete(f"{BASE}/{users[1].id}", headers=_headers(users[0])).status_code == 404
    assert db.get(Friendship, request_id).status == "pending"


@pytest.mark.parametrize("username,expected", [("ana", 400), ("dev", 404), ("inactive", 404), ("nadie", 404), ("an", 404), ("ANA", 404), (" ", 422)])
def test_destinatario_exacto_y_valido(client, users, username, expected):
    response = client.post(f"{BASE}/requests", headers=_headers(users[0]), json={"username": username})
    assert response.status_code == expected


def test_espacios_perifericos_se_ignoran(client, users):
    response = client.post(f"{BASE}/requests", headers=_headers(users[0]), json={"username": " bruno "})
    assert response.status_code == 201


@pytest.mark.parametrize("actor_index,expected", [(None, 401), (3, 403), (4, 401)])
def test_todos_los_endpoints_exigen_jugador_activo(client, users, actor_index, expected):
    headers = {} if actor_index is None else _headers(users[actor_index])
    results = [
        client.get(BASE, headers=headers),
        client.post(f"{BASE}/requests", headers=headers, json={"username": "bruno"}),
        client.post(f"{BASE}/requests/123/accept", headers=headers),
        client.delete(f"{BASE}/requests/123", headers=headers),
        client.delete(f"{BASE}/123", headers=headers),
    ]
    assert [result.status_code for result in results] == [expected] * 5


@pytest.mark.parametrize("change", ["inactive", "developer"])
def test_contactos_inactivos_o_developer_no_se_listan_ni_se_usan_en_grupo(client, users, db, change):
    _befriend(client, users[0], users[1])
    if change == "inactive":
        users[1].is_active = False
    else:
        users[1].role = UserRole.DEVELOPER
    db.commit()
    assert client.get(BASE, headers=_headers(users[0])).json()["friends"] == []
    with pytest.raises(HTTPException) as error:
        resolve_group_members(db, users[0], [users[1].id])
    assert error.value.status_code == 403


def test_solicitud_de_cuenta_desactivada_no_puede_aceptarse(client, users, db):
    request_id = _request(client, users[0], users[1])
    users[0].is_active = False
    db.commit()
    assert client.post(f"{BASE}/requests/{request_id}/accept", headers=_headers(users[1])).status_code == 404


def test_grupo_incluye_anfitrion_y_amigos_una_vez_en_orden(client, users, db):
    _befriend(client, users[0], users[1])
    _befriend(client, users[2], users[0])
    assert resolve_group_members(db, users[0], []) == [users[0]]
    assert resolve_group_members(db, users[0], [users[2].id, users[1].id, users[2].id]) == [users[0], users[2], users[1]]


def test_grupo_rechaza_pendientes_ajenos_y_grupos_parcialmente_autorizados(client, users, db):
    _befriend(client, users[0], users[1])
    _request(client, users[0], users[2])
    for ids in [[users[2].id], [users[1].id, users[2].id], [9999]]:
        with pytest.raises(HTTPException) as error:
            resolve_group_members(db, users[0], ids)
        assert error.value.status_code == 403


@pytest.mark.parametrize("ids,expected", [([1], 400), ([999, 998, 997, 996, 995], 422), ([0], 422), ([-1], 422), ([True], 422), (["2"], 422)])
def test_grupo_rechaza_self_limites_e_ids_invalidos(users, db, ids, expected):
    assert users[0].id == 1
    with pytest.raises(HTTPException) as error:
        resolve_group_members(db, users[0], ids)
    assert error.value.status_code == expected


def test_grupo_admite_cuatro_amigos(users, db):
    extra = [User(username=f"amigo{index}") for index in range(4)]
    db.add_all(extra)
    db.flush()
    db.add_all([
        Friendship(user_low_id=users[0].id, user_high_id=friend.id, requester_id=friend.id, status="accepted")
        for friend in extra
    ])
    db.commit()
    assert resolve_group_members(db, users[0], [friend.id for friend in extra]) == [users[0], *extra]


@pytest.mark.parametrize("values", [
    {"user_low_id": 1, "user_high_id": 1, "requester_id": 1},
    {"user_low_id": 2, "user_high_id": 1, "requester_id": 1},
    {"user_low_id": 1, "user_high_id": 2, "requester_id": 3},
    {"user_low_id": 1, "user_high_id": 2, "requester_id": 1, "status": "invalid"},
])
def test_db_impide_parejas_o_estados_invalidos(db, users, values):
    db.add(Friendship(**values))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_db_impide_duplicados_incluso_con_solicitante_opuesto(db, users):
    db.add(Friendship(user_low_id=users[0].id, user_high_id=users[1].id, requester_id=users[0].id))
    db.commit()
    db.add(Friendship(user_low_id=users[0].id, user_high_id=users[1].id, requester_id=users[1].id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_migracion_amigos_conserva_constraints_y_puede_revertirse(tmp_path):
    backend_root = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'friends-migration.db'}"
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    metadata_engine = create_engine("sqlite://")
    Base.metadata.create_all(metadata_engine)
    actual, expected = inspect(engine), inspect(metadata_engine)
    for inspect_method in ("get_check_constraints", "get_unique_constraints", "get_foreign_keys"):
        assert getattr(actual, inspect_method)("friendships") == getattr(expected, inspect_method)("friendships")
    engine.dispose()
    metadata_engine.dispose()
    command.downgrade(config, "f4b82d1e6a07")
    downgraded = create_engine(database_url)
    assert "friendships" not in inspect(downgraded).get_table_names()
    assert "users" in inspect(downgraded).get_table_names()
    downgraded.dispose()
