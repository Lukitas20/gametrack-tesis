"""Isolated integration fixtures; no operational observations or training."""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.ml.recommender import invalidate_engine
from app.models import Game, Genre, Rating, SteamIdentity, SteamProfileCache, Tag, User
from app.services import gametrack_score_service as scores
from app.services import steam_affinity_service as affinity


class ControlledPredictions:
    """Stub only at the trained-model boundary, no trained fixture artifact."""
    def __init__(self, **changes):
        self.changes = changes
        self.calls = []

    def predict_many(self, candidates, history):
        self.calls.append((candidates, history))
        return [{"available": True, "relative_affinity": .7, "probability": .9, "confidence": 1.,
                 "maximum_overlap": 1., "feature_coverage": 1., "history_games": len(history),
                 "head": "hours", **self.changes} for _ in candidates]


def ready(monkeypatch, **prediction_changes):
    model = ControlledPredictions(**prediction_changes)
    status = dict(status="ready", source="steam_public_review_api", model_version="unit-test-only",
                  training_observations=30, training_examples=20, heads={"hours": True, "explicit": True})
    monkeypatch.setattr(affinity, "load_affinity_model", lambda: (model, status))
    return model


def missing(monkeypatch):
    monkeypatch.setattr(affinity, "load_affinity_model", lambda: (None, {
        "status": "missing", "source": "steam_public_review_api", "training_observations": 0}))


@pytest.fixture
def app_data(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        genre = Genre(slug="accion", name="Acción")
        shooter = Tag(slug="tactical-shooter", name="Tactical shooter", kind="community")
        puzzle = Tag(slug="puzzle", name="Puzzle", kind="community")
        user = User(username="isolated-affinity-integration", steam_id="76561198000000001")
        db.add_all([genre, shooter, puzzle, user]); db.flush()
        db.add(SteamIdentity(user_id=user.id, steam_id=user.steam_id))
        games = [Game(name=f"Isolated game {index}", slug=f"isolated-affinity-{index}",
                      steam_app_id=1000 + index, genres=[genre], tags=[shooter if index < 2 else puzzle],
                      description="Isolated test game", steam_synced_at=datetime.now(timezone.utc),
                      background_image="https://example.test/cover.jpg", metacritic=90,
                      steam_total_reviews=20000, steam_positive_reviews=19000)
                 for index in range(4)]
        db.add_all(games); db.commit(); invalidate_engine()
        monkeypatch.setattr(scores.steam_profile_service, "fetch_library", lambda _: pytest.fail("Unexpected network in isolated test"))
        yield db, user, games
    invalidate_engine(); engine.dispose()


def library(db, user, games, *, minutes=90000, recommended=None):
    content = {"status": "ok", "items": [{"appid": games[0].steam_app_id, "minutes": minutes}]}
    if recommended is not None:
        content["user_reviews"] = {"1": {"status": "ok", "items": [
            {"appid": games[0].steam_app_id, "is_recommended": recommended}]}}
    db.add(SteamProfileCache(user_id=user.id, steam_id=user.steam_id, library=content, friends={}))
    db.commit(); invalidate_engine()


def test_model_contribution_is_auditable_bounded_and_same_in_detail_and_feed(app_data, monkeypatch):
    db, user, games = app_data
    library(db, user, games)
    model = ready(monkeypatch)
    context = scores.score_context(db, user)
    assert context["basis"] == "historial_personal_steam_entrenado"
    assert context["affinity_weights"][games[1].id] == pytest.approx(.25)
    score = scores.score_game(games[1], context)
    assert score.learning.used and score.learning.source == "steam_public_review_api"
    assert score.learning.weight == score.weights["steam_affinity"]
    assert 0 < score.weights["steam_affinity"] <= .25
    assert score.weights["affinity"] + score.weights["steam_affinity"] == pytest.approx(.85)
    assert sum(score.weights.values()) == pytest.approx(1.)
    assert score.base_value == round(sum(score.components[key] * weight for key, weight in score.weights.items()) * 100)
    assert score.components["steam_affinity"] == .7  # Relative affinity, not .9 positive prevalence.
    feed = scores.discovery(db, user)
    assert next(item.gametrack_score for item in feed.items if item.game.id == games[1].id) == score
    assert model.calls and model.calls[0][1][0]["recommended"] is None
    assert model.calls[0][1][0]["minutes"] == 90000
    assert db.scalar(select(func.count()).select_from(Rating)) == 0


@pytest.mark.parametrize("rating", [1., 5.])
def test_own_rating_is_preserved_even_if_model_disagrees(app_data, monkeypatch, rating):
    db, user, games = app_data
    db.add(Rating(user_id=user.id, game_id=games[0].id, score=rating)); db.commit(); invalidate_engine()
    ready(monkeypatch)
    result = scores.game_score(db, user, games[0].id)
    assert result.value == round((rating - 1) / 4 * 100)
    assert result.components == {"own_rating": (rating - 1) / 4}
    assert result.weights == {"own_rating": 1.}
    assert not result.learning.used and result.learning.status == "own_experience"


def test_1500_steam_hours_remain_own_interest_and_never_become_rating(app_data, monkeypatch):
    db, user, games = app_data
    library(db, user, games, minutes=1500 * 60)
    missing(monkeypatch)
    before = scores.game_score(db, user, games[0].id)
    ready(monkeypatch)
    after = scores.game_score(db, user, games[0].id)
    assert after.value == before.value and after.affinity == before.affinity
    assert after.affinity >= 90
    assert not after.learning.used and after.learning.status == "own_experience"
    assert "steam_affinity" not in after.weights
    assert db.scalar(select(func.count()).select_from(Rating)) == 0


def test_negative_steam_review_dominates_hours_and_model_cannot_lift_comparable_rejection(app_data, monkeypatch):
    db, user, games = app_data
    library(db, user, games, minutes=900000, recommended=False)
    missing(monkeypatch)
    baseline_own = scores.game_score(db, user, games[0].id)
    baseline_similar = scores.game_score(db, user, games[1].id)
    ready(monkeypatch)
    own = scores.game_score(db, user, games[0].id)
    similar = scores.game_score(db, user, games[1].id)
    assert own.affinity == baseline_own.affinity == 0
    assert own.value == baseline_own.value and not own.learning.used
    assert similar.affinity == baseline_similar.affinity
    assert not similar.learning.used and "steam_affinity" not in similar.weights


def test_missing_artifact_preserves_steam_only_recommendations_without_reviews(app_data, monkeypatch):
    db, user, games = app_data
    library(db, user, games)
    missing(monkeypatch)
    feed = scores.discovery(db, user)
    assert feed.personal_data and games[1].id in {item.game.id for item in feed.items}
    assert all(not item.gametrack_score.learning.used for item in feed.items)
    assert all("steam_affinity" not in item.gametrack_score.weights for item in feed.items)


def test_missing_history_preserves_general_feed_and_does_not_call_model(app_data, monkeypatch):
    db, user, _ = app_data
    model = ready(monkeypatch)
    feed = scores.discovery(db, user)
    assert feed.items and not feed.personal_data
    assert all(item.gametrack_score.value is None and not item.gametrack_score.learning.used for item in feed.items)
    assert not model.calls


@pytest.mark.parametrize("change", [{"feature_coverage": .49}, {"maximum_overlap": .14}, {"available": False}])
def test_service_abstains_for_missing_candidate_coverage_or_history_overlap(monkeypatch, change):
    ready(monkeypatch, **change)
    engine = SimpleNamespace(game_ids=[1, 2], affinity_features={
        1: {"genres": ["accion"], "tags": ["fps"]}, 2: {"genres": ["accion"], "tags": ["fps"]}})
    values, support, details, status = affinity.affinity_scores(engine, {1: {"source": "steam_playtime", "weight": .9, "minutes": 90000}})
    assert not values.any() and not support.any() and not details
    assert status["status"] == "ready" and status["history_examples"] == 1


def test_service_preserves_explicit_strength_and_batches_without_catalog_square(monkeypatch):
    model = ready(monkeypatch)
    ids = list(range(1, 2101))
    features = {gid: {"genres": ["accion"], "tags": ["fps"]} for gid in ids}
    engine = SimpleNamespace(game_ids=ids, affinity_features=features)
    signals = {1: {"source": "rating", "weight": -.5},
               2: {"source": "steam_playtime", "weight": .9, "minutes": 90000}}
    values, support, details, status = affinity.affinity_scores(engine, signals)
    assert len(values) == len(support) == len(details) == len(ids)
    assert [len(candidates) for candidates, _ in model.calls] == [2048, 52]
    assert model.calls[0][1][0]["strength"] == -.5 and model.calls[0][1][0]["recommended"] is False
    assert model.calls[0][1][1]["recommended"] is None
    assert np.all(values == .7) and np.all(support == 1.)
    assert status["history_examples"] == 2


def test_service_no_history_or_prediction_error_preserves_fallback(monkeypatch):
    model = ready(monkeypatch)
    engine = SimpleNamespace(game_ids=[1], affinity_features={1: {"genres": ["accion"], "tags": ["fps"]}})
    values, support, details, status = affinity.affinity_scores(engine, {})
    assert not values.any() and not support.any() and not details and not model.calls
    monkeypatch.setattr(model, "predict_many", lambda *_: (_ for _ in ()).throw(ValueError("isolated failure")))
    values, support, details, status = affinity.affinity_scores(engine, {1: {"source": "rating", "weight": 1.}})
    assert not values.any() and not support.any() and not details and status["status"] == "invalid"
