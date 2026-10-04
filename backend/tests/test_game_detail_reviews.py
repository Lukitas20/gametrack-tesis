"""Opiniones persistidas por su autor y capturas oficiales, sin ejemplos públicos."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.api.v1.endpoints.interactions import router
from app.db.database import Base, get_db
from app.models import User, Game, Review
from app.schemas.game import GameDetail
from app.services.game_service import list_reviews
from app.services.interaction_service import recompute_game_aggregates
from app.services.steam_service import parse_steam_game


@pytest.fixture
def accounts():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user=User(username='autora',full_name='Nombre visible')
        other=User(username='otra')
        game=Game(name='Juego de prueba',slug='juego-prueba')
        db.add_all([user,other,game]);db.commit()
        app=FastAPI();app.include_router(router)
        app.dependency_overrides[get_db]=lambda:db
        app.dependency_overrides[get_current_user]=lambda:user
        with TestClient(app) as client:
            yield db,user,other,game,app,client
    engine.dispose()


def test_publicar_editar_y_recuperar_reseña_del_perfil(accounts):
    db,user,other,game,app,client=accounts
    payload={'game_id':game.id,'title':'Mi experiencia','content':'El combate es muy divertido y los controles responden bien.','is_recommended':True}
    published=client.post('/reviews',json=payload)
    assert published.status_code==201
    rid=published.json()['id']
    mine=client.get('/me/reviews').json()
    assert len(mine)==1 and mine[0]['game']['id']==game.id and mine[0]['content']==payload['content']
    assert mine[0]['author_name']=='Nombre visible' and mine[0]['source']=='user'
    edited=client.post('/reviews',json={**payload,'content':'Después de jugar más, el combate me sigue gustando.'})
    assert edited.status_code==201 and edited.json()['id']==rid
    assert db.scalar(select(func.count(Review.id)))==1
    assert client.get('/me/reviews').json()[0]['content']==edited.json()['content']
    assert client.get('/me/reviews').headers['cache-control']=='no-store'
    assert len(list_reviews(db,game.id))==1
    app.dependency_overrides[get_current_user]=lambda:other
    assert client.get('/me/reviews').json()==[]
    assert client.get('/me/reviews',params={'game_id':game.id}).json()==[]


def test_solo_opiniones_reales_en_ficha_y_perfil(accounts):
    db,user,_,game,_,client=accounts
    db.add_all([Review(game_id=game.id,source='demo',content='Texto fabricado de demostración'),
                Review(game_id=game.id,source='steam',steam_review_id='123',content='Una opinión importada real'),
                Review(game_id=game.id,user_id=user.id,source='user',content='Mi experiencia escrita a mano')])
    db.commit()
    assert {r.source for r in list_reviews(db,game.id)}=={'user','steam'}
    assert len(client.get('/me/reviews').json())==1
    recompute_game_aggregates(db,game.id)
    assert game.reviews_count==2


def test_paginacion_y_filtro_siempre_corresponden_al_usuario(accounts):
    db,user,other,game,_,client=accounts
    games=[Game(name=f'Otro {i}',slug=f'otro-{i}') for i in range(8)]
    db.add_all(games);db.flush()
    db.add_all([Review(game_id=g.id,user_id=user.id,source='user',content='Reseña escrita por su autora') for g in games])
    db.add(Review(game_id=game.id,user_id=other.id,source='user',content='Otra persona escribió esto'))
    db.commit()
    first=client.get('/me/reviews',params={'limit':6}).json()
    second=client.get('/me/reviews',params={'limit':6,'offset':6}).json()
    assert len(first)==6 and len(second)==2
    assert not {r['id'] for r in first}&{r['id'] for r in second}
    assert client.get('/me/reviews',params={'game_id':game.id}).json()==[]
    assert client.get('/me/reviews',params={'limit':101}).status_code==422


def test_capturas_oficiales_deduplicadas_sin_urls_inventadas():
    official='https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/730/ss_real.jpg'
    parsed=parse_steam_game({'name':'Juego','screenshots':[{'path_full':official},{'path_full':official},
        {'path_full':'https://steamstatic.com.attacker.test/ss.jpg'}, {'path_full':'http://steamstatic.com/ss.jpg'},
        {'path_full':'javascript:alert(1)'},{'path_full':'https://['}, {'path_full':'https://login:secret@steamstatic.com/x.jpg'},'bad']})
    assert parsed['screenshots']==[official]
    assert parse_steam_game({'name':'Sin capturas'})['screenshots']==[]
    game=Game(id=1,name='Sin capturas',slug='sin-capturas',avg_rating=0,ratings_count=0,reviews_count=0,
              platforms=[],genres=[],tags=[],steam_app_id=None)
    assert GameDetail.model_validate(game).screenshots==[]


def test_galeria_solicitada_tiene_prioridad_y_respeta_backoff(accounts, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.models import SteamCatalogEntry
    from app.services import steam_service
    db,_,_,game,_,_=accounts
    game.steam_app_id=220
    game.steam_synced_at=datetime.now(timezone.utc)
    db.commit()
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: pytest.fail("La ficha no debe hacer pedidos HTTP"))
    steam_service.maybe_refresh(db,game)
    entry=db.get(SteamCatalogEntry,220)
    assert entry.priority==200 and entry.status=="pending"
    entry.status="unavailable"
    entry.next_attempt_at=datetime.now(timezone.utc)+timedelta(minutes=30)
    db.commit()
    retry_at=entry.next_attempt_at
    steam_service.maybe_refresh(db,game)
    assert entry.next_attempt_at==retry_at and entry.status=="unavailable"


def test_capturas_guardadas_aunque_el_proceso_de_reseñas_falle(accounts,monkeypatch):
    from datetime import datetime, timezone
    from app.services import steam_service
    db,_,_,game,_,_=accounts
    game.steam_app_id=220
    game.description="Descripción anterior"
    game.steam_synced_at=datetime.now(timezone.utc)
    db.commit()
    synced=game.steam_synced_at
    image="https://shared.akamai.steamstatic.com/steam/apps/220/ss_real.jpg"
    monkeypatch.setattr(steam_service,"get_app_details",lambda _: {"type":"game","name":"Juego","short_description":"Texto actualizado","screenshots":[{"path_full":image}]})
    monkeypatch.setattr(steam_service,"get_review_totals",lambda _:None)
    def fail_reviews(*args,**kwargs):
        raise RuntimeError("Reseñas temporalmente inaccesibles")
    monkeypatch.setattr(steam_service,"import_reviews",fail_reviews)
    with pytest.raises(RuntimeError):
        steam_service.refresh_game(db,game)
    db.rollback()
    db.refresh(game)
    assert game.screenshots==[image]
    assert game.steam_synced_at==synced
    assert game.description=="Descripción anterior"
