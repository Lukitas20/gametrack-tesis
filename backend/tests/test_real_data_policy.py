"""Contratos de procedencia sobre fixtures aislados; no son datos operativos."""
import pytest
from sqlalchemy import func, select

from tests.test_api import auth, client, data, db
from app.core.config import settings
from app.db.real_data import DATA_POLICY, refresh_legacy_aggregates
from app.ml.local_model import LocalModel, fit, load_for_database, read_training_data, train_and_save
from app.ml.analytics import game_analytics
from app.ml.recommender import RecommenderEngine
from app.models import Rating, Review, User, UserPreference
from app.models.enums import Aspect, Sentiment
from app.models.interaction import ReviewAspect


@pytest.fixture
def mixed(db, data):
    demo = User(username='legacy-generated', bio='Perfil de gusto: RPG.')
    db.add(demo); db.flush()
    game = data['breve']
    db.add_all([Rating(user_id=demo.id, game_id=game.id, score=5),
                Rating(user_id=data['player'].id, game_id=game.id, score=2)])
    real = Review(game_id=game.id, source='steam', steam_review_id='policy-real',
                  content='La historia tiene problemas.', is_recommended=False,
                  sentiment=Sentiment.NEGATIVE, is_analyzed=True)
    fake = Review(game_id=game.id, user_id=demo.id, source='demo', content='Historia perfecta inventada.',
                  sentiment=Sentiment.POSITIVE, is_analyzed=True)
    for item, sentiment, value in [(real, Sentiment.NEGATIVE, -1), (fake, Sentiment.POSITIVE, 1)]:
        item.aspects = [ReviewAspect(game_id=game.id, aspect=Aspect.STORY, sentiment=sentiment,
                                     score=value, evidence=item.content)]
    db.add_all([real, fake]); db.commit(); db.expire_all()
    return demo.id, real.id, fake.id


def test_queries_reports_and_learning_exclude_legacy_demo(db, data, mixed):
    demo_id, real_id, fake_id = mixed
    assert db.scalar(select(func.count(Rating.id))) == 1
    assert [row.id for row in db.scalars(select(Review))] == [real_id]
    assert db.scalar(select(func.count(ReviewAspect.id))) == 1
    assert len(read_training_data(db)[0]) == 1
    assert demo_id not in RecommenderEngine(db).user_ids
    report = game_analytics(db, data['breve'])
    assert report['resenas']['analizadas'] == 1
    assert report['resenas']['distribucion']['negativo'] == 1
    assert report['juego']['cantidad_ratings_local'] == 1
    assert report['juego']['rating_local_promedio'] == 2
    assert report['aspectos'][0]['menciones'] == 1
    # La política no destruye las filas del histórico.
    assert db.scalar(select(func.count()).select_from(Review.__table__)) == 2


def test_refresh_removes_synthetic_votes_without_deleting_history(db, data, mixed):
    refresh_legacy_aggregates(db)
    assert data['breve'].ratings_count == 2  # un voto real + una reseña Steam real
    assert data['breve'].avg_rating == 1.5
    assert db.scalar(select(func.count()).select_from(Rating.__table__)) == 2


def test_missing_origin_or_unowned_user_review_is_not_evidence(db, data):
    db.add_all([Review(game_id=data['breve'].id, source='seed', content='No publicar'),
                Review(game_id=data['breve'].id, source='other', content='Origen desconocido'),
                Review(game_id=data['breve'].id, source='user', content='Sin autor registrado')])
    db.commit()
    assert list(db.scalars(select(Review))) == []


def test_new_training_rejects_demo_even_when_enough_examples(db, data, tmp_path):
    target = tmp_path/'forbidden.npz'
    with pytest.raises(ValueError, match='sintéticos'):
        train_and_save(db, target, data_label='demo')
    assert not target.exists()


@pytest.mark.parametrize('label,policy', [('demo', DATA_POLICY), ('observed', None), (None, None)])
def test_old_or_demo_artifact_cannot_be_loaded(db, data, tmp_path, monkeypatch, label, policy):
    path = tmp_path/'legacy.npz'
    model = fit([(u, g, 3.) for u in range(3) for g in range(1, 4)], epochs=1)
    model.metadata.update(data_label=label, data_policy=policy, automatic_eligible=True)
    model.save(path); monkeypatch.setattr(settings, 'LOCAL_MODEL_PATH', str(path))
    loaded, status = load_for_database(db)
    assert loaded is None and status['status'] == 'blocked_provenance'
    assert status['automatic_eligible'] is False


def test_public_api_does_not_offer_old_strategy_or_synthetic_source(client, data):
    assert client.get('/api/v1/recommendations?strategy=ia_local', headers=auth(client,'jugadora')).status_code == 422
    assert client.get('/api/v1/analytics/reviews?source=seed', headers=auth(client,'dev')).status_code == 422


def test_demo_profiles_are_retired_even_with_valid_credentials(client, db, data):
    from app.core.security import hash_password
    user = User(username='jugador.demo', hashed_password=hash_password('test-password'))
    db.add(user); db.commit()
    assert client.post('/api/v1/auth/login', json={'username':'jugador.demo','password':'test-password'}).status_code == 403


def test_synthetic_evaluation_is_rejected_before_opening_database(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    path = Path(__file__).resolve().parents[1]/'scripts/analyze_reviews.py'
    spec = importlib.util.spec_from_file_location('real_review_cli', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(sys, 'argv', ['analyze_reviews.py','--evaluate'])
    monkeypatch.setattr(module, 'SessionLocal', lambda: pytest.fail('No abrir base para una evaluación retirada'))
    with pytest.raises(SystemExit) as result:
        module.main()
    assert result.value.code == 2
