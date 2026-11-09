"""Filtros de evidencia: scope, sentimiento por aspecto y fechas originales."""
from datetime import datetime, timezone

import pytest

from tests.test_api import auth, client, data, db
from app.models import Review
from app.models.interaction import ReviewAspect
from app.models.enums import Aspect, Sentiment


@pytest.fixture
def evidence(db, data):
    data['breve'].developer = 'Estudio Test'
    data['largo'].developer = 'Estudio Test'
    data['incognito'].developer = 'Otro'
    positive = Review(game_id=data['breve'].id, source='steam', steam_review_id='1',
                      content='Me encanta la historia pero el rendimiento es pésimo.',
                      sentiment=Sentiment.POSITIVE, is_analyzed=True,
                      published_at=datetime(2024, 2, 1, 23, 59, tzinfo=timezone.utc), helpful_count=9)
    positive.aspects = [ReviewAspect(game_id=data['breve'].id, aspect=Aspect.PERFORMANCE,
                                    sentiment=Sentiment.NEGATIVE, score=-.8, evidence='el rendimiento es pésimo')]
    negative = Review(game_id=data['largo'].id, user_id=data['player'].id, source='user', content='No me gusta pero el rendimiento es excelente.',
                      sentiment=Sentiment.NEGATIVE, is_analyzed=True,
                      created_at=datetime(2024, 2, 2, tzinfo=timezone.utc), helpful_count=1)
    negative.aspects = [ReviewAspect(game_id=data['largo'].id, aspect=Aspect.PERFORMANCE,
                                    sentiment=Sentiment.POSITIVE, score=.8)]
    unknown = Review(game_id=data['breve'].id, source='steam', steam_review_id='2',
                     content='Opinión histórica sin fecha original conocida.',
                     created_at=datetime(2024, 2, 1, tzinfo=timezone.utc))
    other = Review(game_id=data['incognito'].id, source='steam', content='Otro estudio: texto privado al alcance elegido.')
    db.add_all([positive, negative, unknown, other]);db.commit()
    return positive, negative, unknown, other


def test_reviews_require_developer(client, data):
    url='/api/v1/analytics/reviews'
    assert client.get(url).status_code == 401
    assert client.get(url,headers=auth(client,'jugadora')).status_code == 403


def test_scoped_search_and_pagination(client, data, evidence):
    headers=auth(client,'dev')
    result=client.get('/api/v1/analytics/reviews',headers=headers,params={'limit':1,'sort':'oldest'}).json()
    assert result['total'] == 3 and result['undated'] == 1
    assert result['items'][0]['id'] == evidence[0].id
    assert result['items'][0]['game_name'] == 'Juego Breve'
    assert result['items'][0]['publication_date'].startswith('2024-02-01')
    page=client.get('/api/v1/analytics/reviews',headers=headers,params={'limit':1,'offset':1,'sort':'oldest'}).json()
    assert page['items'][0]['id'] == evidence[1].id
    game=client.get('/api/v1/analytics/reviews',headers=headers,params={'game_id':data['breve'].id}).json()
    assert game['total'] == 2
    assert client.get('/api/v1/analytics/reviews',headers=headers,params={'search':'%'}).json()['total'] == 0
    assert client.get('/api/v1/analytics/reviews',headers=headers,params={'source':'user'}).json()['total'] == 1


def test_negative_aspect_in_positive_review(client, data, evidence):
    response=client.get('/api/v1/analytics/reviews',headers=auth(client,'dev'),
                        params={'aspect':'optimizacion','sentiment':'negativo'})
    assert response.status_code == 200
    assert [r['id'] for r in response.json()['items']] == [evidence[0].id]
    assert response.json()['items'][0]['sentiment'] == 'positivo'
    global_negative=client.get('/api/v1/analytics/reviews',headers=auth(client,'dev'),params={'sentiment':'negativo'}).json()
    assert [r['id'] for r in global_negative['items']] == [evidence[1].id]


def test_report_fragment_opens_exact_review_only_in_its_scope(client, data, evidence):
    headers = auth(client, 'dev')
    review = evidence[0]
    for path in (f'/api/v1/analytics/games/{review.game_id}', '/api/v1/analytics/studio'):
        report = client.get(path, headers=headers).json()
        choices = report.get('juegos_del_estudio', report.get('juegos'))
        assert {game['id'] for game in choices} == {data['breve'].id, data['largo'].id}
        reference = report['referencias_negativas']['optimizacion'][0]
        assert reference == {'cita': 'el rendimiento es pésimo', 'review_id': review.id}
        assert report['citas_negativas']['optimizacion'] == [reference['cita']]
    url = '/api/v1/analytics/reviews'
    params = {'studio': 'Estudio Test', 'review_id': review.id}
    result = client.get(url, headers=headers, params=params).json()
    assert [(item['id'], item['content']) for item in result['items']] == [(review.id, review.content)]
    assert client.get(url, headers=headers, params={**params, 'studio': 'Otro'}).json()['total'] == 0
    assert client.get(url, headers=headers, params={**params, 'review_id': evidence[3].id}).json()['total'] == 0


def test_date_filter_uses_original_date_and_full_last_day(client, data, evidence):
    result=client.get('/api/v1/analytics/reviews',headers=auth(client,'dev'),
                      params={'date_from':'2024-02-01','date_to':'2024-02-01'}).json()
    assert result['total'] == 1 and result['undated'] == 1
    assert result['items'][0]['id'] == evidence[0].id
    result=client.get('/api/v1/analytics/reviews',headers=auth(client,'dev'),params={'sort':'oldest'}).json()
    assert result['items'][-1]['publication_date'] is None


@pytest.mark.parametrize('params,status',[
    ({'game_id':999999},404),({'game_id':1,'studio':'Otro'},400),
    ({'date_from':'2024-03-01','date_to':'2024-02-01'},422),
    ({'date_to':'9999-12-31'},422),({'aspect':'inventado'},422),
    ({'limit':101},422),({'offset':-1},422),({'sort':'invalid'},422),
    ({'review_id':0},422),
])
def test_invalid_filters(client, data, evidence, params, status):
    assert client.get('/api/v1/analytics/reviews',headers=auth(client,'dev'),params=params).status_code == status
