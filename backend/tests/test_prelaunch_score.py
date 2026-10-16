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
from app.models import Game, Genre, Rating, Tag, User, UserPreference, SteamIdentity, SteamProfileCache
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
    assert report.score.value is None and report.score.confidence == 'none'
    assert all(not ref.id.startswith('history-') for ref in report.references)
    assert any('rasgos específicos' in point.text for point in report.cautions)
    announced['genres']=[]
    assert preview.build_preview(announced,score_context(db,user)).score.value is None


def test_manual_preferences_work_without_history_and_do_not_claim_experience(profile):
    db,_,other,game,announced=profile
    db.add(UserPreference(user_id=other.id,genre_id=game.genres[0].id));db.commit()
    report=preview.build_preview(announced,score_context(db,other))
    assert report.score.value is None
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


def test_age_gate_uses_public_hover_tags_without_fabricating_age(monkeypatch):
    preview._cache.clear(); calls=[]
    def handler(request):
        calls.append(request)
        if request.url.path == '/api/appdetails':
            return httpx.Response(200,json={'123':{'success':True,'data':{
                'type':'game','name':'Anuncio observado','genres':[{'description':'Action'}],
                'release_date':{'coming_soon':True,'date':'2027'}}}})
        if request.url.path == '/app/123/':
            return httpx.Response(302,headers={'Location':'https://store.steampowered.com/agecheck/app/123/'})
        assert request.url.path == '/apphoverpublic/123/'
        return httpx.Response(200,text='<div class="app_tag"><span>FPS</span></div><div class="app_tag">First-Person</div>')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(preview.releases,'_http_client',lambda:client)
        metadata=preview.announced_game(123)
        assert metadata['tags']==['FPS','First-Person']
        assert metadata['tag_source']=='https://store.steampowered.com/apphoverpublic/123/'
        assert metadata['tags_available'] is True
        assert all('birth' not in str(request.url).lower() and not request.headers.get('cookie') for request in calls)
        assert preview.announced_game(123)==metadata and len(calls)==3
    preview._cache.clear()


def test_tag_parser_ignores_add_button_and_keeps_nested_real_labels():
    parser=preview.StoreTags()
    parser.feed('<div class="app_tag">+</div><div class="app_tag"><span>FPS</span></div>'
                '<a class="app_tag">First-Person</a><div class="app_tag">FPS</div>')
    assert parser.tags==['FPS','First-Person']


def test_incomplete_tags_are_retried_so_outage_does_not_last_an_hour(monkeypatch):
    preview._cache.clear(); now=[1000]; calls=[]
    monkeypatch.setattr(preview.time,'time',lambda:now[0])
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == '/api/appdetails':
            return httpx.Response(200,json={'123':{'success':True,'data':{
                'type':'game','name':'Future','genres':[{'description':'Action'}],
                'release_date':{'coming_soon':True}}}})
        return httpx.Response(200,text='No tags available')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(preview.releases,'_http_client',lambda:client)
        assert preview.announced_game(123)['tags_available'] is False
        assert len(calls)==3
        now[0]+=preview.INCOMPLETE_TTL+1
        preview.announced_game(123)
        assert len(calls)==6
    preview._cache.clear()


def test_unrelated_action_game_is_not_cited_or_treated_as_dislike(profile):
    db,user,_,game,announced=profile
    fps=Tag(slug='fps',name='FPS',kind='community')
    db.add(Game(slug='other-shooter',name='Catálogo shooter',genres=game.genres,tags=[fps]))
    db.commit(); invalidate_engine()
    announced['tags']=['FPS']
    result=preview.build_preview(announced,preview.preview_context(db,user))
    assert result.score.value is None
    assert result.score.confidence=='none'
    assert 'baja' not in result.summary
    assert all(not ref.id.startswith('history-') for ref in result.references)
    assert all(game.name not in p.text for p in result.positives)


def test_comparable_game_beyond_top_40_hours_is_used_and_other_genres_do_not_dilute(profile,monkeypatch):
    db,user,_,_,announced=profile
    action=db.query(Genre).filter_by(slug='accion').one()
    racing=Tag(slug='cars',name='Cars',kind='community')
    fps=Tag(slug='fps',name='FPS',kind='community')
    shooter=Game(slug='played-shooter',name='Shooter con menos horas',steam_app_id=999,
                 genres=[action],tags=[fps])
    unrelated=[Game(slug=f'racer-{i}',name=f'Carreras {i}',steam_app_id=1000+i,
                    genres=[action],tags=[racing]) for i in range(45)]
    user.steam_id='76561198000000001'
    db.add_all([shooter,*unrelated]); db.flush()
    db.add(SteamIdentity(user_id=user.id,steam_id=user.steam_id))
    cache=SteamProfileCache(user_id=user.id,steam_id=user.steam_id,library={'status':'ok','items':[
        {'appid':999,'minutes':6000},*[{'appid':g.steam_app_id,'minutes':60000} for g in unrelated]]},friends={})
    db.add(cache); db.commit(); invalidate_engine()
    announced['tags']=['FPS']
    monkeypatch.setattr(preview,'announced_game',lambda appid:announced)
    result=preview.explain_upcoming(db,user,123,'Comparalo con mi historial')
    assert result.score.value is not None and result.score.value > 40
    assert any(shooter.name in p.text for p in result.positives)
    assert any(f'history-{shooter.id}' in p.reference_ids for p in result.answer)
    assert all(not ref.title.startswith('Carreras') for ref in result.references)
    assert any('47 juegos' in reason for reason in result.score.reasons)
    cache.library={'status':'ok','items':[{'appid':999,'minutes':6000}]}; db.commit()
    assert preview.explain_upcoming(db,user,123).score.value==result.score.value


def test_preview_does_not_compute_full_catalog_predictions(profile,monkeypatch):
    db,user,_,_,announced=profile
    engine=preview.get_engine(db)
    def full_matrix(*args,**kwargs): pytest.fail('Un anuncio sólo necesita comparar contra el historial personal')
    monkeypatch.setattr(engine,'personal_history_similarities',full_matrix)
    monkeypatch.setattr(engine,'personal_scores',full_matrix)
    monkeypatch.setattr(preview,'announced_game',lambda appid:announced)
    assert preview.explain_upcoming(db,user,123).score.value is not None
