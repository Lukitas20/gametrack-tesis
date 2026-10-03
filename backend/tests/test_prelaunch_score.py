from datetime import datetime, timezone
import socket

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.api.v1.endpoints.recommendations import router
from app.db.database import Base, get_db
from app.ml.recommender import invalidate_engine
from app.models import Game, Genre, Rating, Tag, User, UserPreference
from app.models.enums import UserRole
from app.services import prelaunch_score_service as preview
from app.services.gametrack_score_service import score_context


@pytest.fixture
def profile(monkeypatch):
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **kw: pytest.fail('El cálculo debe ser local'))
    engine = create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        genre = Genre(slug='accion',name='Acción')
        tag = Tag(slug='souls-like',name='Souls-like',kind='community')
        user = User(username='preview-player',role=UserRole.PLAYER)
        other = User(username='other-player',role=UserRole.PLAYER)
        game = Game(slug='history',name='Historial real',genres=[genre],tags=[tag],
            steam_app_id=30,steam_synced_at=datetime.now(timezone.utc),background_image='https://example.com/a.jpg')
        db.add_all([user,other,game]);db.flush()
        db.add(Rating(user_id=user.id,game_id=game.id,score=5));db.commit()
        announced={'appid':123,'name':'Juego futuro','genres':['Acción'],'tags':['Souls-like'],
                   'tags_available':True,'release_label':'2027','cached_at':1700000000}
        yield db,user,other,game,announced
    invalidate_engine()
    engine.dispose()


def test_preview_uses_history_without_public_notes_or_catalog_insert(profile):
    db,user,other,game,announced=profile
    report=preview.build_preview(announced,score_context(db,user))
    assert report.score.preliminary and report.score.evidence == 'prelanzamiento'
    assert 55 < report.score.value <= 75
    assert report.score.metascore is None and report.score.community is None and report.score.review_count == 0
    assert report.score.weights == {'affinity':1.}
    assert any('Historial real' in point.text and '5/5' in point.text for point in report.positives)
    assert any(ref.url == f'#/juego/{game.id}' for ref in report.references)
    assert len(db.query(Game).all()) == 1
    no_profile=preview.build_preview(announced,score_context(db,other))
    assert no_profile.score.value is None and no_profile.positives == []


def test_negative_opinion_reduces_prediction_and_has_reference(profile):
    db,user,_,game,announced=profile
    before=preview.build_preview(announced,score_context(db,user))
    rating=db.query(Rating).one();rating.score=1;db.commit();invalidate_engine()
    after=preview.build_preview(announced,score_context(db,user),'¿Por qué podría no gustarme?')
    assert after.score.value < before.score.value
    assert any('1/5' in point.text for point in after.cautions)
    assert any(f'history-{game.id}' in point.reference_ids for point in after.answer)


def test_genre_only_is_limited_and_missing_traits_have_no_number(profile):
    db,user,_,game,announced=profile
    game.tags=[];db.commit();invalidate_engine()
    announced['tags']=[]
    report=preview.build_preview(announced,score_context(db,user))
    assert report.score.value <= 55
    assert any('rasgos específicos' in point.text for point in report.cautions)
    announced['genres']=[]
    assert preview.build_preview(announced,score_context(db,user)).score.value is None


def test_manual_preferences_work_without_history_and_do_not_claim_experience(profile):
    db,_,other,game,announced=profile
    db.add(UserPreference(user_id=other.id,genre_id=game.genres[0].id));db.commit()
    report=preview.build_preview(announced,score_context(db,other))
    assert report.score.value == 55
    assert any('géneros de tu perfil' in point.text for point in report.positives)
    assert all('Historial real' not in point.text for point in report.positives)


def test_buy_question_does_not_guarantee_quality(profile):
    db,user,_,_,announced=profile
    report=preview.build_preview(announced,score_context(db,user),'¿Conviene comprarlo?')
    assert any('no garantiza' in point.text for point in report.answer)
    assert any('rendimiento' in point.text for point in report.answer)


def test_public_metadata_validates_release_and_cache_is_not_personal(monkeypatch):
    preview._cache.clear();calls=[]
    def handler(request):
        calls.append(request.url)
        if request.url.path == '/api/appdetails':
            return httpx.Response(200,json={'123':{'success':True,'data':{
                'type':'game','name':'Anuncio real','genres':[{'description':'Action'}],
                'release_date':{'coming_soon':True,'date':'2027'}}}})
        return httpx.Response(200,text='<a class="app_tag">Souls-like</a><a class="app_tag">Souls-like</a><div>Not a tag</div>')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(preview.releases,'_http_client',lambda:client)
        metadata=preview.announced_game(123)
        assert metadata['genres']==['Acción'] and metadata['tags']==['Souls-like']
        assert preview.announced_game(123)==metadata and len(calls)==2
        assert 'score' not in metadata
    preview._cache.clear()


@pytest.mark.parametrize('data,status',[
    ({'type':'game','name':'Released','release_date':{'coming_soon':False}},409),
    ({'type':'demo','name':'Demo','release_date':{'coming_soon':True}},404),
    ({'type':'game','name':'','release_date':{'coming_soon':True}},502),
])
def test_invalid_or_released_games_are_not_predicted(monkeypatch,data,status):
    preview._cache.clear()
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'123':{'success':True,'data':data}}))) as client:
        monkeypatch.setattr(preview.releases,'_http_client',lambda:client)
        with pytest.raises(HTTPException) as error:preview.announced_game(123)
    assert error.value.status_code==status


def test_store_tag_failure_falls_back_to_genres_and_network_failure_is_honest(monkeypatch):
    preview._cache.clear()
    def handler(request):
        if request.url.path == '/api/appdetails':
            return httpx.Response(200,json={'123':{'success':True,'data':{'type':'game','name':'Future',
                'genres':[{'description':'Action'}],'release_date':{'coming_soon':True,'date':'Coming soon'}}}})
        raise httpx.ConnectError('no tags')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(preview.releases,'_http_client',lambda:client)
        report=preview.announced_game(123)
        assert report['tags']==[] and report['genres']==['Acción']
    preview._cache.clear()
    def fail(request):raise httpx.ConnectError('offline')
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        monkeypatch.setattr(preview.releases,'_http_client',lambda:client)
        with pytest.raises(HTTPException) as error:preview.announced_game(123)
        assert error.value.status_code==503


def test_preview_routes_auth_role_validation_and_no_store(profile,monkeypatch):
    db,user,_,_,announced=profile
    app=FastAPI();app.include_router(router,prefix='/api/v1')
    app.dependency_overrides[get_db]=lambda:db
    monkeypatch.setattr(preview,'announced_game',lambda appid:announced)
    with TestClient(app) as client:
        path='/api/v1/recommendations/upcoming/123/explanation'
        assert client.get(path).status_code==401
        app.dependency_overrides[get_current_user]=lambda:user
        response=client.get(path)
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        assert response.json()['score']['preliminary'] is True
        assert client.post(path,json={'question':'¿Qué falta confirmar antes de comprar?'}).status_code==200
        assert client.post(path,json={'question':'x'*501}).status_code==422
        assert client.get('/api/v1/recommendations/upcoming/0/explanation').status_code==422
        user.role=UserRole.DEVELOPER
        assert client.get(path).status_code==403
