"""Read-only verification mechanics in an isolated database, never production data."""
import importlib.util
from pathlib import Path
import sys

import pytest
from sqlalchemy import text
from app.services import steam_affinity_service

from tests.test_steam_affinity_integration import app_data, library, ready


spec = importlib.util.spec_from_file_location(
    "verify_affinity_runtime", Path(__file__).resolve().parents[1] / "scripts" / "verify_affinity_runtime.py")
runtime = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runtime
spec.loader.exec_module(runtime)


def test_production_entry_refuses_sqlite_before_connecting(app_data):
    db, *_ = app_data
    with pytest.raises(runtime.VerificationError, match="requires_postgresql"):
        runtime.begin_read_only(db)


def test_requires_database_enforced_read_only(app_data):
    db, *_ = app_data
    with pytest.raises(runtime.VerificationError, match="read_only_transaction_not_confirmed"):
        runtime.inspect_read_only_session(db)


def test_verifies_actual_score_path_and_emits_only_aggregate_counts(app_data, monkeypatch):
    db, user, games = app_data
    library(db, user, games)
    ready(monkeypatch)
    monkeypatch.setattr(runtime, "load_affinity_model", steam_affinity_service.load_affinity_model)
    db.execute(text("PRAGMA query_only=ON"))
    result = runtime.inspect_read_only_session(db)
    assert result["result"] == "verified"
    assert result["read_only"] and result["external_http_calls"] == 0
    assert result["profiles_sampled"] == 1
    assert result["candidates_with_model_contribution"] > 0
    assert result["own_history_checks"] == 1 and result["integrity_failures"] == 0
    assert not {"username", "steam_id", "user_id", "database_url", "history", "game_ids"} & result.keys()
    assert not db.new and not db.dirty and not db.deleted
