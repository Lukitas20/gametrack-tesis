"""Tandas sin servicios externos: checkpoints, presupuesto y estado entre jobs."""
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models import Game, SteamCatalogSync, SteamCatalogWorker
from app.services import steam_catalog_service, steam_catalog_worker as worker
from app.workers import steam_catalog_batch as batch
from tests.test_steam_catalog_worker import db, games, successful_details  # noqa: F401


@pytest.fixture
def factory(db, monkeypatch):
    monkeypatch.setattr(batch, "database_size_bytes", lambda _: 100 * 1024 ** 2)
    return sessionmaker(bind=db.get_bind())


def test_storage_limit_prevents_steam_calls_and_keeps_games(db, factory, monkeypatch):
    games(db, 1)
    monkeypatch.setattr(batch, "database_size_bytes", lambda _: 400 * 1024 ** 2)
    monkeypatch.setattr(batch, "run_cycle", lambda **_: pytest.fail("Storage limit must stop ingestion"))
    report = batch.run_batch(session_factory=factory)
    assert report["stop_reason"] == "storage_limit" and report["cycles"] == 0
    row = db.scalar(select(SteamCatalogWorker))
    assert row.stopped_at is not None and row.last_error == batch.STORAGE_MESSAGE
    assert db.scalar(select(Game)) is not None


def test_storage_is_rechecked_between_pages(factory, monkeypatch):
    sizes = iter([399 * 1024 ** 2, 401 * 1024 ** 2])
    monkeypatch.setattr(batch, "database_size_bytes", lambda _: next(sizes))
    monkeypatch.setattr(batch, "run_cycle", lambda **_: {
        "index": {"partial": True, "pages_this_run": 1}, "details": None})
    report = batch.run_batch(session_factory=factory)
    assert report["cycles"] == report["index_pages"] == 1
    assert report["stop_reason"] == "storage_limit"


def test_separate_jobs_resume_real_index_checkpoint(db, factory, monkeypatch):
    monkeypatch.setattr(settings, "STEAM_API_KEY", "test-only")
    monkeypatch.setattr(settings, "STEAM_CATALOG_REQUESTED_ONLY", True)
    cursors = []

    def page(_client, *, cursor, **_):
        cursors.append(cursor)
        return ([{"appid": 100 + cursor, "name": f"Juego {cursor}", "last_modified": 0}],
                cursor == 0, 100 + cursor)

    monkeypatch.setattr(steam_catalog_service, "_fetch_page", page)
    first = batch.run_batch(session_factory=factory, max_cycles=1)
    assert first["stop_reason"] == "cycle_limit" and first["index_pages"] == 1
    with factory() as check:
        assert check.get(SteamCatalogSync, 1).cursor_appid == 100
        assert check.get(SteamCatalogSync, 1).scan_started_at is not None
    second = batch.run_batch(session_factory=factory)
    assert second["stop_reason"] == "idle" and second["index_pages"] == 1
    assert cursors == [0, 100]
    with factory() as check:
        assert check.get(SteamCatalogSync, 1).scan_started_at is None
        assert len(list(check.scalars(select(Game)))) == 2


def test_requested_only_skips_unvisited_games(db, monkeypatch):
    rows = games(db, 2)
    worker.steam_service.queue_game_refresh(db, rows[1])
    monkeypatch.setattr(settings, "STEAM_CATALOG_REQUESTED_ONLY", True)
    seen = []
    successful_details(monkeypatch, seen)
    assert worker.process_details(db)["ready"] == 1
    assert seen == [rows[1].id]


def test_clean_job_is_known_between_scheduled_runs(db, factory, monkeypatch):
    monkeypatch.setattr(settings, "STEAM_API_KEY", "worker-secret")
    presence = worker.WorkerPresence(factory, "scheduled")
    presence.start()
    presence.stop()
    monkeypatch.setattr(settings, "STEAM_API_KEY", "")
    status = worker.worker_status(db, now=datetime.now(timezone.utc) + timedelta(hours=3))
    assert status["worker_mode"] == "scheduled"
    assert status["key_configured"] and not status["worker_running"]
    assert status["worker_finished_at"] is not None
    assert "worker-secret" not in str(status)


def test_timeout_closes_presence_without_starting_another_cycle(db, factory, monkeypatch):
    class ExpiredTimer:
        def __init__(self, seconds, callback):
            self.callback = callback
        def start(self):
            self.callback()
        def cancel(self):
            pass
        def join(self):
            pass
    monkeypatch.setattr(batch.threading, "Timer", ExpiredTimer)
    monkeypatch.setattr(batch, "run_cycle", lambda **_: pytest.fail("Time budget exhausted"))
    report = batch.run_batch(session_factory=factory)
    assert report["stop_reason"] == "time_budget"
    assert db.scalar(select(SteamCatalogWorker)).stopped_at is not None


def test_remote_error_exits_batch_instead_of_consuming_minutes(factory, monkeypatch):
    monkeypatch.setattr(batch, "run_cycle", lambda **_: {
        "index": {"status": "error", "pages_this_run": 0}, "details": None})
    report = batch.run_batch(session_factory=factory)
    assert report["stop_reason"] == "retry" and report["cycles"] == 1


def test_batch_failure_records_safe_error_and_closes_presence(db, factory, monkeypatch):
    def fail(**_):
        raise RuntimeError("private-database-password")
    monkeypatch.setattr(batch, "run_cycle", fail)
    with pytest.raises(RuntimeError):
        batch.run_batch(session_factory=factory)
    row = db.scalar(select(SteamCatalogWorker))
    assert row.stopped_at is not None and row.last_error == batch.RETRY_MESSAGE


def test_stopped_batch_does_not_open_connections():
    stop = threading.Event()
    stop.set()
    assert batch.run_batch(stop_event=stop, session_factory=lambda: pytest.fail("Cancelled"))["stop_reason"] == "interrupted"


@pytest.mark.parametrize("url", [
    "sqlite://", "postgresql://u:p@ep-test-pooler.neon.tech/db?sslmode=require",
    "postgresql://u:p@ep-test.neon.tech/db", "postgresql://u:p@ep-test.neon.tech/db?sslmode=disable",
])
def test_cli_rejects_wrong_database_or_neon_connection(url):
    with pytest.raises(ValueError):
        batch.validate_connection(url)


def test_direct_neon_url_with_tls_is_supported():
    batch.validate_connection("postgresql://u:p@ep-test.neon.tech/db?sslmode=require&channel_binding=require")


@pytest.mark.parametrize("reason,code", [("idle", 0), ("time_budget", 0), ("storage_limit", 1), ("retry", 1)])
def test_cli_exit_status_reports_incomplete_work(monkeypatch, reason, code):
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://u:p@ep-test.neon.tech/db?sslmode=require")
    monkeypatch.setattr(settings, "STEAM_API_KEY", "test-only")
    monkeypatch.setattr(batch, "run_batch", lambda **_: {"stop_reason": reason})
    assert batch.main([]) == code


def test_cli_redacts_database_failures(monkeypatch, caplog):
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://u:p@ep-test.neon.tech/db?sslmode=require")
    monkeypatch.setattr(settings, "STEAM_API_KEY", "test-only")
    monkeypatch.setattr(batch, "run_batch", lambda **_: (_ for _ in ()).throw(RuntimeError("private-password")))
    assert batch.main([]) == 1
    assert "private-password" not in caplog.text
