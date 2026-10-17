"""Accesos por tipo: validar antes de emitir sesión o importar historial."""
import pytest
from tests.test_api import client, db, data  # noqa: F401
from tests.test_account_experiences import register


@pytest.mark.parametrize('role',['jugador','desarrollador'])
def test_matching_login_portal_and_opposite_access(client,monkeypatch,role):
    account,_=register(client,role)
    payload={'username':'cuenta_nueva','password':'una-frase-larga-123','role':role}
    correct=client.post('/api/v1/auth/login',json=payload)
    assert correct.status_code==200 and correct.json()['user']['id']==account['id']
    opposite='desarrollador' if role=='jugador' else 'jugador'
    from app.api.v1.endpoints import auth
    def forbidden(*args): pytest.fail('Un acceso incorrecto no debe preparar datos de la cuenta')
    monkeypatch.setattr(auth,'ensure_steam_preferences',forbidden)
    wrong=client.post('/api/v1/auth/login',json={**payload,'role':opposite})
    assert wrong.status_code==403 and 'access_token' not in wrong.json()
    assert 'acceso de' in wrong.json()['detail']
    assert client.post('/api/v1/auth/login',json={**payload,'password':'incorrecta','role':opposite}).status_code==401


def test_legacy_login_is_preserved_and_invalid_portal_type_rejected(client):
    register(client)
    payload={'username':'cuenta_nueva','password':'una-frase-larga-123'}
    assert client.post('/api/v1/auth/login',json=payload).status_code==200
    assert client.post('/api/v1/auth/login',json={**payload,'role':'admin'}).status_code==422
