"""Regresiones de autorización por tipo, con cuentas aisladas de prueba."""
import pytest

from app.core.security import create_access_token
from app.models import User
from tests.test_api import client, db, data, auth  # noqa: F401
from tests.test_account_experiences import register


@pytest.mark.parametrize('role,opposite',[('jugador','desarrollador'),('desarrollador','jugador')])
def test_type_is_fixed_before_and_after_onboarding(client,role,opposite):
    _, headers=register(client,role)
    assert client.put('/api/v1/auth/me/experience',headers=headers,json={'role':opposite,'studio':'Indie'}).status_code==403
    assert client.put('/api/v1/auth/me/experience',headers=headers,json={
        'role':role,'complete_onboarding':True,**({'studio':'Indie'} if role=='desarrollador' else {})}).status_code==200
    assert client.put('/api/v1/auth/me/experience',headers=headers,json={'role':opposite,'studio':'Indie'}).status_code==403
    assert client.get('/api/v1/auth/me',headers=headers).json()['role']==role


@pytest.mark.parametrize('legacy_flag',[False,True])
def test_legacy_flag_and_role_in_token_cannot_grant_studio_access(client,db,data,legacy_flag):
    user,_=register(client)
    record=db.get(User,user['id']);record.developer_enabled=legacy_flag;db.commit()
    token=create_access_token({'sub':str(record.id),'role':'desarrollador'})
    headers={'Authorization':f'Bearer {token}'}
    assert not record.is_developer
    for path in ['/analytics/overview','/analytics/studio',f"/analytics/games/{data['breve'].id}"]:
        assert client.get('/api/v1'+path,headers=headers).status_code==403
    assert client.post('/api/v1/reviews/analyze',headers=headers,json={'content':'La historia me gustó.'}).status_code==403
    for fields in ({'role':'desarrollador'},{'developer_enabled':True},{'onboarding_step':'choose'}):
        assert client.put('/api/v1/auth/me',headers=headers,json=fields).status_code==422
    assert client.get('/api/v1/auth/me',headers=headers).json()['role']=='jugador'


@pytest.mark.parametrize('fields',[{'developer_enabled':True},{'onboarding_step':'choose'}])
def test_registration_cannot_override_internal_fields(client,fields):
    assert client.post('/api/v1/auth/register',json={'username':'invalida','password':'una-frase-larga-123',**fields}).status_code==422


@pytest.mark.parametrize('method,path,payload',[
    ('GET','/me/lists',None),('GET','/me/lists/summary',None),
    ('POST','/me/lists',{'name':'Una lista'}),('GET','/me/ratings',None),('GET','/me/reviews',None),
    ('POST','/ratings',{'game_id':1,'score':4}),
    ('POST','/reviews',{'game_id':1,'content':'Una experiencia muy buena.','is_recommended':True}),
    ('GET','/recommendations',None),('GET','/recommendations/discovery',None),
    ('GET','/recommendations/game/1/score',None),('GET','/recommendations/upcoming/123/explanation',None),
    ('GET','/friends',None),('GET','/steam/me/profile',None),('POST','/steam/me/sync',None),
    ('GET','/steam/me/reviews',None),('GET','/steam/me/achievements/123',None),
    ('PUT','/auth/me/preferences',{'genre_ids':[1]}),('POST','/auth/me/preferences/steam',None),
    ('POST','/quiz/suggest',{'mood':'historia','company':'solo'}),
    ('GET','/play/feedback',None),('GET','/play/backlog',None),
])
def test_developer_cannot_use_player_functions(client,method,path,payload):
    _,headers=register(client,'desarrollador')
    response=client.request(method,'/api/v1'+path,headers=headers,**({'json':payload} if payload is not None else {}))
    assert response.status_code==403,response.text


@pytest.mark.parametrize('role',['jugador','desarrollador'])
def test_steam_initial_choice_can_only_be_consumed_once(client,db,data,role):
    user,headers=register(client)
    # Estado inicial reservado al registro Steam, inaccesible por actualización de perfil.
    record=db.get(User,user['id']);record.onboarding_step='choose';db.commit()
    assert client.get('/api/v1/me/lists',headers=headers).status_code==403
    preferences={'genre_ids':[data['breve'].genres[0].id]}
    assert client.put('/api/v1/auth/me/preferences',headers=headers,json=preferences).status_code==403
    chosen=client.put('/api/v1/auth/me/experience',headers=headers,json={
        'role':role,**({'studio':'Indie'} if role=='desarrollador' else {})})
    assert chosen.status_code==200
    assert client.put('/api/v1/auth/me/preferences',headers=headers,json=preferences).status_code==(200 if role=='jugador' else 403)
    opposite='jugador' if role=='desarrollador' else 'desarrollador'
    assert client.put('/api/v1/auth/me/experience',headers=headers,json={'role':opposite,'studio':'Indie'}).status_code==403
