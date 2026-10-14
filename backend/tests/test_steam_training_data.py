"""Isolated fixtures for the collector; these are never training observations."""
from __future__ import annotations

import json
from pathlib import Path
import time

import httpx
import pytest

from app.ml.steam_training_data import (
    CollectionError, ObservationStore, SteamReviewClient, collect, load_appids,
    normalize_review,
)

TEST_KEY = b"test-only-key-not-for-real-data!!"


def review(review_id=101, steamid="76561198000000001", voted_up=True, updated=1700000000):
    return {"recommendationid": str(review_id), "author": {"steamid": steamid,
             "playtime_at_review": 90, "playtime_forever": 180, "playtime_last_two_weeks": 0},
            "voted_up": voted_up, "timestamp_created": 1600000000,
            "timestamp_updated": updated, "language": "spanish", "review": "PRIVATE TEXT",
            "profile_url": "https://example.invalid/private"}


def http_client(responses, requests=None):
    def handle(request):
        if requests is not None:
            requests.append(request)
        value = responses.pop(0)
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json={"response": value})
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_whitelists_real_observation_and_stable_private_author():
    raw = review()
    row = normalize_review(raw, 730, TEST_KEY, "2026-10-14T12:00:00+00:00")
    assert row["user_key"] == normalize_review(raw, 570, TEST_KEY, "later")["user_key"]
    assert row["user_key"] != normalize_review(raw, 730, b"different", "later")["user_key"]
    assert row["voted_up"] is True
    assert row["minutes_at_review"] == 90
    assert row["minutes_forever"] == 180
    assert row["minutes_recent"] == 0
    text = json.dumps(row)
    assert "76561198000000001" not in text
    assert "PRIVATE TEXT" not in text
    assert "profile_url" not in text and "author" not in row


@pytest.mark.parametrize("vote", [None, 1, 0, "true", "false"])
def test_missing_or_coerced_votes_are_not_labels(vote):
    raw = review(voted_up=vote)
    assert normalize_review(raw, 730, TEST_KEY, "now") is None


def test_missing_playtime_is_unknown_and_negative_or_bool_is_not_minutes():
    raw = review()
    raw["author"] = {"steamid": "76561198000000001", "playtime_forever": -1,
                     "playtime_last_two_weeks": False}
    row = normalize_review(raw, 730, TEST_KEY, "now")
    assert row["minutes_at_review"] is None
    assert row["minutes_forever"] is None
    assert row["minutes_recent"] is None
    raw["author"].pop("steamid")
    assert normalize_review(raw, 730, TEST_KEY, "now") is None


@pytest.mark.parametrize("timestamp", [None, 0, -1, True, 10**40, int(time.time()) + 86400])
def test_invalid_or_future_dates_are_unknown_without_overflow(timestamp, tmp_path):
    raw = review()
    raw["timestamp_created"] = raw["timestamp_updated"] = timestamp
    store = ObservationStore(tmp_path)
    row = normalize_review(raw, 730, store.key, "2026-10-14T12:00:00+00:00")
    assert row["created_at"] is None and row["updated_at"] is None
    store.merge(row)
    assert store.summary()["known_review_dates"] == 0
    assert store.summary()["first_review_epoch"] is None


def test_latest_review_replaces_state_without_adding_positive_negative_pair(tmp_path):
    store = ObservationStore(tmp_path)
    first = normalize_review(review(), 730, store.key, "first")
    assert store.merge(first) == "inserted"
    assert store.merge({**first, "retrieved_at": "second"}) == "duplicates"
    changed = normalize_review(review(voted_up=False, updated=1700000001), 730, store.key, "third")
    assert store.merge(changed) == "updated"
    assert store.merge(first) == "older_duplicates"
    conflicting = {**first, "steam_appid": 570}
    assert store.merge(conflicting) == "conflicting_ids"
    store.persist()
    resumed = ObservationStore(tmp_path)
    assert len(resumed.rows) == 1
    assert resumed.summary()["votes_positive"] == 0
    assert resumed.summary()["votes_negative"] == 1
    assert resumed.key == store.key


def test_missing_original_hmac_key_refuses_to_relabel_existing_authors(tmp_path):
    store = ObservationStore(tmp_path)
    store.merge(normalize_review(review(), 730, store.key, "now"))
    store.persist()
    (tmp_path / ".author_hmac.key").unlink()
    with pytest.raises(ValueError, match="clave original"):
        ObservationStore(tmp_path)


def test_report_measures_multi_game_overlap_and_both_real_vote_values(tmp_path):
    store = ObservationStore(tmp_path)
    for index, appid in enumerate([730, 570, 440]):
        store.merge(normalize_review(review(review_id=101 + index, voted_up=index != 1), appid, store.key, "now"))
    summary = store.summary()
    assert summary["observations"] == 3
    assert summary["users_with_at_least_2_games"] == 1
    assert summary["users_with_at_least_3_games"] == 1
    assert summary["users_with_at_least_5_games"] == 0
    assert summary["users_with_both_votes"] == 1
    assert summary["minutes_at_review_coverage"] == 1
    assert summary["training_readiness"] == "not_evaluated"


def test_collection_resumes_cursor_budgets_and_does_not_require_success(tmp_path):
    requests = []
    with http_client([{"reviews": [review()], "cursor": "next+cursor/="}], requests) as http:
        client = SteamReviewClient(http, max_requests=1, sleep=lambda _: None)
        manifest = collect([730, 570], tmp_path, client, max_games=2, pages_per_game=2, max_pages=1, progress=lambda _: None)
    assert manifest["summary"]["observations"] == 1
    assert manifest["requests_this_run"] == 1
    params = json.loads(requests[0].url.params["input_json"])
    assert params["cursor"] == "*" and params["languages"] == ["all"]
    assert params["filter"] == 1 and params["purchase_type"] == 1
    assert "key" not in params
    requests.clear()
    with http_client([{"reviews": [], "cursor": ""}], requests) as http:
        client = SteamReviewClient(http, max_requests=1, sleep=lambda _: None)
        manifest = collect([730], tmp_path, client, max_games=1, pages_per_game=2, max_pages=2, progress=lambda _: None)
    assert json.loads(requests[0].url.params["input_json"])["cursor"] == "next+cursor/="
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert checkpoint["games"]["730"]["exhausted"] is True
    assert checkpoint["games"]["730"]["pages"] == 2
    assert len(checkpoint["page_receipts"]) == 2
    assert manifest["summary"]["observations"] == 1
    assert manifest["observations_sha256"]
    assert "PRIVATE TEXT" not in (tmp_path / "observations.jsonl").read_text()


def test_retry_after_honored_and_request_budget_counts_retries():
    pauses = []
    responses = [httpx.Response(429, headers={"Retry-After": "7"}),
                 {"reviews": [], "cursor": ""}]
    with http_client(responses) as http:
        client = SteamReviewClient(http, max_requests=2, sleep=pauses.append, monotonic=lambda: 0)
        assert client.page(730, "*")[0] == []
        assert client.requests == 2
        assert 7 in pauses
        with pytest.raises(CollectionError, match="presupuesto"):
            client.page(730, "*")


def test_long_retry_after_stops_without_retrying_too_early():
    with http_client([httpx.Response(429, headers={"Retry-After": "300"})]) as http:
        client = SteamReviewClient(http, sleep=lambda _: pytest.fail("Should stop, not sleep"))
        with pytest.raises(CollectionError, match="120s"):
            client.page(730, "*")
        assert client.requests == 1


def test_failure_keeps_prior_page_and_reports_partial(tmp_path):
    with http_client([{"reviews": [review()], "cursor": "next"}, httpx.Response(503)]) as http:
        client = SteamReviewClient(http, retries=0, sleep=lambda _: None)
        manifest = collect([730], tmp_path, client, pages_per_game=2, progress=lambda _: None)
    assert manifest["status"] == "partial"
    assert manifest["summary"]["observations"] == 1
    assert "HTTP 503" in manifest["error"]
    assert json.loads((tmp_path / "checkpoint.json").read_text())["games"]["730"]["cursor"] == "next"


def test_invalid_schema_does_not_become_empty_success(tmp_path):
    with http_client([{"error": "bad request"}]) as http:
        client = SteamReviewClient(http, sleep=lambda _: None)
        manifest = collect([730], tmp_path, client, progress=lambda _: None)
    assert manifest["status"] == "partial"
    assert manifest["run_counts"] == {}
    assert manifest["summary"]["observations"] == 0


def test_input_json_aliases_and_unique_valid_appids(tmp_path):
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps({"games": [{"steam_appid": 730}, {"steam_app_id": "570"},
                                         {"appid": 440}, 730, True, -1, "broken"]}))
    assert load_appids(path) == [730, 570, 440]


def test_enforces_minimum_delay_before_collection():
    with http_client([]) as http:
        with pytest.raises(ValueError, match="Pausa mínima"):
            SteamReviewClient(http, delay=0)
