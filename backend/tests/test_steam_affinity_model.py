"""Isolated mathematical fixtures only; never a published training corpus."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from app.db.real_data import DATA_POLICY
from app.ml.steam_affinity_model import (
    AffinityHead, FEATURE_SCHEMA, HEADS, SOURCE, SteamAffinityModel,
    catalog_idf, feature_matrix, game_features, history_for_head,
)

spec = importlib.util.spec_from_file_location("affinity_trainer_test", Path(__file__).parents[1] / "scripts/train_steam_affinity.py")
trainer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trainer)


def game(appid=1, *tags):
    return {"steam_appid": appid, "genres": ["action"], "tags": list(tags or ["fps"])}


def fixture_model(*, explicit=True, hours=True):
    coefficients = np.zeros(len(FEATURE_SCHEMA)); coefficients[0] = 2.; coefficients[2] = -2.
    heads = {name: AffinityHead(coefficients.copy(), 1.4, np.zeros(len(FEATURE_SCHEMA)), np.ones(len(FEATURE_SCHEMA))) for name in HEADS}
    metadata = {"version": 1, "feature_schema": FEATURE_SCHEMA, "source": SOURCE,
                "data_policy": DATA_POLICY, "data_label": "observed",
                "heads": {"explicit": {"automatic_eligible": explicit, "training_positive_rate": .8},
                          "hours": {"automatic_eligible": hours, "training_positive_rate": .8}}}
    return SteamAffinityModel(heads, catalog_idf([game(1, "fps"), game(2, "puzzle")]), metadata)


def test_same_feature_extraction_ignores_ids_name_platform_and_untrusted_vote_counts():
    features = game_features({"steam_appid": 730, "name": "PRIVATE", "genres": [{"slug": "action"}],
                              "tags": [{"slug": "achievements", "kind": "platform"}, {"slug": "fps", "kind": "community", "votes": 400}]})
    assert features == {"genre:action": .4, "tag:fps": 1.}
    assert features == game_features(game(9999, "fps"))
    assert not game_features({"steam_appid": 730, "name": "ignored"})


def test_prior_profile_overlap_changes_personal_prediction_beyond_global_prior():
    model = fixture_model()
    candidates = [game(4, "fps"), game(5, "puzzle")]
    results = model.predict_many(candidates, [{**game(1, "fps"), "recommended": True, "minutes": 900}])
    assert all(row["available"] and row["head"] == "explicit" for row in results)
    assert results[0]["probability"] > results[1]["probability"]
    assert results[0]["relative_affinity"] > results[1]["relative_affinity"]
    assert results[0]["history_games"] == 1
    negative = model.predict(candidates[0], [{**game(1, "fps"), "recommended": False, "minutes": 90000}])
    assert negative["probability"] < results[0]["probability"]
    # None of the target's own runtime hours enters a feature.
    with_target_hours = model.predict({**candidates[0], "minutes": 900000000}, [{**game(1, "fps"), "recommended": True, "minutes": 900}])
    assert with_target_hours == results[0]


def test_batch_equals_individual_and_support_missing_profile_abstains():
    model = fixture_model(); candidates = [game(4, "fps"), game(5, "puzzle"), {}]
    history = [{**game(1, "fps"), "minutes": 9000}]
    assert model.predict_many(candidates, history) == [model.predict(candidate, history) for candidate in candidates]
    assert model.predict(candidates[0], [])["available"] is False
    assert model.predict(candidates[0], [])["probability"] is None
    assert model.predict({}, history)["available"] is False


def test_hours_head_uses_real_engagement_without_relabeling_observed_votes():
    history = [{**game(1, "fps"), "recommended": False, "minutes": 1500},
               {**game(2, "puzzle"), "recommended": True, "minutes": 15}]
    selected = history_for_head(history, "hours")
    assert len(selected) == 1 and selected[0]["recommended"] is None and selected[0]["strength"] == 1.
    assert history[0]["recommended"] is False  # Original target/history untouched.
    result = fixture_model().predict(game(3, "fps"), [{**game(1, "fps"), "minutes": 1500}])
    assert result["available"] and result["head"] == "hours" and result["explicit_history_games"] == 0
    assert fixture_model(hours=False).predict(game(3, "fps"), [{**game(1, "fps"), "minutes": 1500}])["available"] is False


def test_explicit_fractional_strength_preserved_and_neutral_not_used():
    idf = catalog_idf([game(1, "fps"), game(2, "puzzle")])
    history = [{**game(1, "fps"), "recommended": True, "strength": 0},
               {**game(2, "puzzle"), "recommended": True, "strength": .5}]
    _, support = feature_matrix([game(3, "fps")], history, idf)
    assert support["history_games"] == 1


def test_json_portable_only_parameters_no_author_ids_or_rows(tmp_path):
    model = fixture_model(); path = tmp_path / "fixture.json"; model.save(path)
    raw = json.loads(path.read_text())
    assert set(raw) == {"heads", "term_idf", "metadata"}
    assert set(raw["heads"]) == set(HEADS)
    assert "user_key" not in path.read_text() and "review_id" not in path.read_text()
    restored = SteamAffinityModel.load(path)
    history = [{**game(1, "fps"), "recommended": True}]
    assert restored.predict(game(2, "puzzle"), history) == model.predict(game(2, "puzzle"), history)


@pytest.mark.parametrize("change", ["source", "data_label", "data_policy", "feature_schema", "nan", "shape", "zero_scale"])
def test_invalid_or_synthetic_artifact_rejected(tmp_path, change):
    path = tmp_path / "fixture.json"; fixture_model().save(path); raw = json.loads(path.read_text())
    if change == "nan":
        raw["heads"]["explicit"]["coefficients"][0] = float("nan")
    elif change == "shape":
        raw["heads"]["explicit"]["coefficients"].pop()
    elif change == "zero_scale":
        raw["heads"]["explicit"]["feature_scale"][0] = 0
    else:
        raw["metadata"][change] = "demo"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        SteamAffinityModel.load(path)


def test_chronological_examples_exclude_target_future_hours_and_later_history_updates():
    user = "a" * 64
    rows = [{"user_key": user, "steam_appid": 1, "created_at": 100, "updated_at": 100,
             "voted_up": True, "minutes_at_review": 120, "minutes_forever": 999999},
            {"user_key": user, "steam_appid": 2, "created_at": 200, "updated_at": 400,
             "voted_up": False, "minutes_at_review": 500},
            {"user_key": user, "steam_appid": 3, "created_at": 300, "updated_at": 300,
             "voted_up": False, "minutes_at_review": 888888}]
    games = [game(1, "fps"), game(2, "puzzle"), game(3, "fps")]
    samples, skipped = trainer.chronological_examples(rows, games, catalog_idf(games))
    examples = samples["explicit"][trainer.split_user(user)]
    target = next(row for row in examples if row["appid"] == 3)
    assert target["history_games"] == 1 and target["y"] == 0
    expected, _ = feature_matrix([games[2]], [{**games[0], "recommended": True, "minutes": 120}], catalog_idf(games))
    assert np.array_equal(target["x"], expected[0])
    # Current/future observations cannot alter the learning input.
    rows[0]["minutes_forever"] = 1; rows[2]["minutes_at_review"] = 0
    again, _ = trainer.chronological_examples(rows, games, catalog_idf(games))
    assert np.array_equal(target["x"], next(row for row in again["explicit"][trainer.split_user(user)] if row["appid"] == 3)["x"])
    assert skipped["without_strictly_earlier_history"] == 1


def test_author_split_is_disjoint_deterministic_and_not_appid_dependent():
    assignments = {str(index): trainer.split_user(str(index)) for index in range(200)}
    assert set(assignments.values()) == {"train", "validation", "test"}
    assert assignments == {str(index): trainer.split_user(str(index)) for index in range(200)}


def test_manifest_hash_or_fake_provenance_block_before_training(tmp_path):
    path = tmp_path / "observations.jsonl"; path.write_text("")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source": "demo", "data_policy": DATA_POLICY}))
    with pytest.raises(ValueError, match="procedencia"):
        trainer.load_observations(path, manifest)


def test_train_and_validation_not_test_choose_head_and_missing_validation_blocks():
    # Mathematical fixtures exclusively for checking training mechanics. They
    # are never fed to the operational trainer, packaged or claimed as real.
    rows = []
    for index in range(80):
        values = np.zeros(len(FEATURE_SCHEMA)); values[0] = index / 80; values[6] = values[0]
        rows.append({"x": values, "y": int(index % 3 != 0), "user": str(index), "appid": index % 4})
    head, report = trainer.train_head({"train": rows, "validation": [], "test": rows[:5]})
    assert not report["automatic_eligible"]
    assert "validation_requires_100_observed_targets" in report["activation_reasons"]
    assert report["evaluation"]["test"]["model"]["examples"] == 5
    assert np.isfinite(head.coefficients).all()
    changed_test = [{**row, "y": 1 - row["y"]} for row in rows[:5]]
    same_head, same_report = trainer.train_head({"train": rows, "validation": [], "test": changed_test})
    assert np.array_equal(head.coefficients, same_head.coefficients)
    assert head.intercept == same_head.intercept
    assert report["regularization_c"] == same_report["regularization_c"]
    assert report["activation_reasons"] == same_report["activation_reasons"]
