"""Experiencias privadas, aprendizaje explícito y rescate de juegos propios."""
from datetime import datetime, timedelta, timezone
import sqlite3
from pathlib import Path
from tempfile import NamedTemporaryFile
from contextlib import closing

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app.api.deps import get_current_user
from app.api.v1.endpoints.play import router
from app.db.database import get_db
from app.ml.recommender import invalidate_engine
from app.models import GameList, GameListItem, GameStatus, ListType, PlayFeedback, Rating, SteamProfileCache, UserRole
from app.schemas.play import FeedbackInput
from app.services import play_service as play
from app.services.gametrack_score_service import game_score, score_context
from app.services.game_explanation_service import explain_game
from app.services.personal_history_service import personal_history
from app.services.prelaunch_score_service import build_preview
from tests.test_score_steam_history import history, set_library  # noqa: F401


def save(db, user, game, **values):
    return play.save_feedback(db, user, game.id, FeedbackInput(**{'enjoyment': 'liked', **values}))


def queued(db, user, games):
    collection = GameList(user_id=user.id, name='Pendientes', list_type=ListType.BACKLOG)
    db.add(collection); db.flush()
    db.add_all([GameListItem(list_id=collection.id, game_id=game.id) for game in games]); db.commit()


def client_for(db, user=None):
    app = FastAPI(); app.include_router(router, prefix='/api/v1')
    app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.mark.parametrize('reason', ['technical', 'social', 'time', 'other'])
def test_interruption_is_not_a_rejection(history, reason):
    db, user, _, games, *_ = history
    before = game_score(db, user, games[2].id)
    result = save(db, user, games[0], enjoyment='disliked', reason=reason)
    assert not result.learning_applied
    assert db.scalar(select(PlayFeedback)).taste_weight is None
    assert game_score(db, user, games[2].id).model_dump() == before.model_dump()
    assert db.scalar(select(func.count()).select_from(Rating)) == 0


@pytest.mark.parametrize('fields', [
    {'enjoyment': 'mixed'}, {'enjoyment': 'not_sure'},
    {'enjoyment': 'not_sure', 'played': False},
])
def test_uncertain_or_unplayed_does_not_invent_taste(history, fields):
    db, user, _, games, *_ = history
    assert not save(db, user, games[0], **fields).learning_applied
    assert play.learning_context(db, user) is None


def test_edits_keep_one_record_and_stars_unchanged(history):
    db, user, _, games, *_ = history
    db.add(Rating(user_id=user.id, game_id=games[0].id, score=4)); db.commit()
    assert save(db, user, games[0], enjoyment='disliked').learning_applied
    first = db.scalar(select(PlayFeedback)); first_time = first.taste_at
    assert not save(db, user, games[0], reason='technical', note='  Se cortó la partida  ').learning_applied
    assert first.taste_weight == -.75 and first.taste_at == first_time
    assert first.note == 'Se cortó la partida'
    assert db.scalar(select(func.count()).select_from(PlayFeedback)) == 1
    assert db.scalar(select(Rating)).score == 4
    assert save(db, user, games[0]).learning_applied
    assert first.taste_weight == .75


def test_latest_meaningful_opinion_wins_without_other_users(history):
    db, user, other, games, *_ = history
    save(db, other, games[0], enjoyment='disliked')
    assert personal_history(db, user, {})[0][games[0].id]['source'] == 'steam_review'
    save(db, user, games[0], enjoyment='disliked')
    assert personal_history(db, user, {})[0][games[0].id]['source'] == 'play_feedback'
    rating = Rating(user_id=user.id, game_id=games[0].id, score=5,
                    updated_at=datetime.now(timezone.utc) + timedelta(seconds=10))
    db.add(rating); db.commit()
    assert personal_history(db, user, {games[0].id: 5})[0][games[0].id]['source'] == 'rating'


def test_new_stars_immediately_after_feedback_take_precedence(history):
    from app.services.interaction_service import upsert_rating
    from app.schemas.interaction import RatingCreate
    db, user, _, games, *_ = history
    save(db, user, games[0], enjoyment='disliked')
    upsert_rating(db, user, RatingCreate(game_id=games[0].id, score=5))
    assert personal_history(db, user, {games[0].id: 5})[0][games[0].id]['source'] == 'rating'


def test_quiz_and_group_use_feedback_and_keep_hard_constraints(history):
    from app.api.v1.endpoints.quiz import run_quiz, QuizRequest
    from app.ml.recommender import get_engine
    from app.ml.group_recommender import group_scores
    db, user, other, games, *_ = history
    engine = get_engine(db)
    request = QuizRequest(genres=['accion'], exclude_game_ids=[games[i].id for i in (0, 1, 4, 5)])
    before = {item.game_id: item.score for item in run_quiz(db, request, engine, user=user).ranked}
    save(db, user, games[0], enjoyment='disliked')
    after = {item.game_id: item.score for item in run_quiz(db, request, engine, user=user).ranked}
    assert after[games[2].id] < before[games[2].id]
    strict = run_quiz(db, QuizRequest(company='amigos', allow_relaxation=False), engine, user=user)
    assert strict.ranked == []  # afinidad no transforma un juego individual en cooperativo
    group = group_scores(db, engine, [user, other], 'balanced')
    assert group[games[2].id]['participants'][0]['basis'] == 'experiencias_personales'


def test_feedback_changes_similar_scores_and_cites_real_experience(history):
    db, user, _, games, *_ = history
    before = game_score(db, user, games[2].id)
    save(db, user, games[0], enjoyment='disliked')
    after = game_score(db, user, games[2].id)
    assert after.affinity < before.affinity
    explanation = explain_game(db, user, games[2].id, 'Comparalo con mi historial')
    assert any('experiencia que no te gustó' in p.text for p in explanation.cautions)
    assert any(ref.url == f'#/juego/{games[0].id}' for ref in explanation.references)
    announced = {'appid': 123, 'name': 'Futuro táctico', 'genres': ['Acción'],
                 'tags': ['Tactical Shooter'], 'tags_available': True, 'release_label': '2027', 'cached_at': 1}
    preview = build_preview(announced, score_context(db, user))
    assert preview.score.preliminary
    assert any('experiencia que no te gustó' in p.text for p in preview.cautions)


def test_api_private_history_and_owner_scoped_updates(history):
    db, user, other, games, *_ = history
    save(db, other, games[0], note='Nota del otro usuario')
    with client_for(db, user) as client:
        response = client.get('/api/v1/play/feedback')
        assert response.json() == [] and response.headers['cache-control'] == 'no-store'
        assert client.get(f'/api/v1/play/feedback/{games[0].id}').json() is None
        response = client.put(f'/api/v1/play/feedback/{games[0].id}', json={'enjoyment': 'liked'})
        assert response.status_code == 200 and response.json()['learning_applied']
        assert len(client.get('/api/v1/play/feedback').json()) == 1
        assert client.put('/api/v1/play/feedback/99999', json={'enjoyment': 'liked'}).status_code == 404
    assert db.scalar(select(PlayFeedback).where(PlayFeedback.user_id == other.id)).note == 'Nota del otro usuario'


def test_api_requires_player_and_valid_input(history):
    db, user, _, games, *_ = history
    with client_for(db) as client:
        assert client.get('/api/v1/play/feedback').status_code == 401
    with client_for(db, user) as client:
        for payload in ({'enjoyment': 'invented'}, {'enjoyment': 'liked', 'minutes': -1},
                        {'enjoyment': 'liked', 'minutes': 10081}, {'enjoyment': 'liked', 'played': False},
                        {'enjoyment': 'not_sure', 'played': False, 'minutes': 1},
                        {'enjoyment': 'liked', 'user_id': 123}, {'enjoyment': 'liked', 'note': 'a' * 501}):
            assert client.put(f'/api/v1/play/feedback/{games[0].id}', json=payload).status_code == 422
        assert client.get('/api/v1/play/backlog?mode=unknown').status_code == 422
        assert client.get('/api/v1/play/backlog?limit=25').status_code == 422
        user.role = UserRole.DEVELOPER; db.commit()
        assert client.get('/api/v1/play/backlog').status_code == 403
        assert client.put(f'/api/v1/play/feedback/{games[0].id}', json={'enjoyment': 'liked'}).status_code == 403


def test_backlog_uses_only_own_verified_library_and_pending(history):
    db, user, other, games, *_ = history
    set_library(db, user, {'status': 'ok', 'items': [
        {'appid': 100, 'minutes': 0}, {'appid': 101, 'minutes': 90}, {'appid': 102, 'minutes': 900},
        {'appid': 99999, 'minutes': 0}]})
    queued(db, user, [games[1], games[2], games[3]])
    queued(db, other, [games[4]])
    items = {row.game.id: row for row in play.backlog(db, user).items}
    assert set(items) == {game.id for game in games[:4]}
    assert items[games[1].id].source == 'steam_pending'
    assert items[games[3].id].source == 'pending' and items[games[3].id].minutes is None
    assert 'no verificamos' in items[games[3].id].reason
    assert play.backlog(db, user).unmapped_games == 1
    assert [row.game.id for row in play.backlog(db, user, 'unplayed').items] == [games[0].id]
    assert {row.game.id for row in play.backlog(db, user, 'pending').items} == {games[i].id for i in (1, 2, 3)}


@pytest.mark.parametrize('state', ['private', 'unavailable', 'wrong_identity', 'unverified'])
def test_backlog_cannot_read_private_or_wrong_steam_cache(history, state):
    db, user, _, games, *_ = history
    queued(db, user, [games[3]])
    cache = db.get(SteamProfileCache, user.id)
    if state == 'wrong_identity': cache.steam_id = '76561198000000099'
    elif state == 'unverified': db.delete(user.steam_identity)
    else: cache.library = {'status': state, 'items': [{'appid': 100, 'minutes': 0}]}
    db.commit(); db.expire(user)
    result = play.backlog(db, user)
    assert [row.game.id for row in result.items] == [games[3].id]
    assert result.items[0].source == 'pending'


def test_completed_abandoned_rejected_and_no_replay_not_rescued(history):
    db, user, _, games, *_ = history
    queued(db, user, games)
    db.add_all([Rating(user_id=user.id, game_id=games[0].id, score=5, status=GameStatus.COMPLETED),
                Rating(user_id=user.id, game_id=games[1].id, score=4, status=GameStatus.ABANDONED)])
    db.commit()
    save(db, user, games[2], enjoyment='disliked')
    save(db, user, games[3], enjoyment='not_sure', replay=False)
    assert {row.game.id for row in play.backlog(db, user).items} == {games[4].id, games[5].id}


def test_self_report_prevents_zero_steam_hours_from_claiming_unplayed(history):
    db, user, _, games, *_ = history
    set_library(db, user, {'status': 'ok', 'items': [{'appid': 100, 'minutes': 0}]})
    save(db, user, games[0], enjoyment='mixed', minutes=180)
    assert play.backlog(db, user, 'unplayed').items == []
    assert play.backlog(db, user).items == []


def test_large_library_is_batched_and_does_not_invent_games(history):
    db, user, _, games, *_ = history
    set_library(db, user, {'status': 'ok', 'items': [{'appid': appid, 'minutes': 0} for appid in range(100, 2100)]})
    result = play.backlog(db, user)
    assert result.unmapped_games == 1994
    assert {row.game.id for row in result.items} == {game.id for game in games}
    set_library(db, user, {'status': 'ok', 'items': []})
    assert play.backlog(db, user).items == []


def test_migration_preserves_existing_data():
    with NamedTemporaryFile(suffix='.sqlite', dir=Path.cwd(), delete=False) as temp:
        filename = Path(temp.name)
    try:
        _check_migration(filename)
    finally:
        filename.unlink(missing_ok=True)


def _check_migration(filename):
    with closing(sqlite3.connect(filename)) as connection:
        connection.executescript("CREATE TABLE alembic_version(version_num VARCHAR(32) PRIMARY KEY);"
                                 "INSERT INTO alembic_version VALUES ('f7a31d908c42');"
                                 "CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT);"
                                 "CREATE TABLE games(id INTEGER PRIMARY KEY, name TEXT);"
                                 "INSERT INTO users VALUES (1, 'Conservar');"
                                 "INSERT INTO games VALUES (1, 'Juego existente');")
    config = Config('alembic.ini'); config.set_main_option('sqlalchemy.url', 'sqlite:///' + filename.as_posix())
    command.upgrade(config, 'head')
    with closing(sqlite3.connect(filename)) as connection:
        assert connection.execute('SELECT username FROM users').fetchone()[0] == 'Conservar'
        assert connection.execute('SELECT name FROM games').fetchone()[0] == 'Juego existente'
        assert connection.execute('SELECT version_num FROM alembic_version').fetchone()[0] == 'a9d740ec812b'
        assert connection.execute('SELECT COUNT(*) FROM play_feedback').fetchone()[0] == 0
