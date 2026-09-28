"""El mantenimiento no depende de visitas ni bloquea el catálogo por falta de clave."""
import logging
import signal
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.models import Game, SteamCatalogEntry, SteamCatalogSync, SteamCatalogWorker
from app.services import steam_catalog_worker as worker, steam_service
from app.workers import steam_catalog as worker_command
from tests.test_steam import client  # noqa: F401 - fixture HTTP con la base de este archivo


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "STEAM_API_KEY", "")
    monkeypatch.setattr(settings, "DB_AUTO_CREATE", False)
    monkeypatch.setattr(settings, "STEAM_CATALOG_REQUEST_DELAY_SECONDS", 0)
    monkeypatch.setattr(worker, "_index_retry_at", 0)
    monkeypatch.setattr(worker, "_details_retry_at", 0)
    with Session(engine) as session:
        yield session
    engine.dispose()


def games(db, count=3):
    rows = [Game(name=f"Juego {i}", slug=f"juego-{i}", steam_app_id=100 + i) for i in range(count)]
    db.add_all(rows)
    db.commit()
    return rows


def successful_details(monkeypatch, seen):
    def refresh(db, game):
        seen.append(game.id)
        entry = db.get(SteamCatalogEntry, game.steam_app_id)
        entry.status = "ready"
        entry.next_attempt_at = datetime.now(timezone.utc) + timedelta(hours=6)
        db.commit()
        return True
    monkeypatch.setattr(steam_service, "refresh_game", refresh)


def test_small_batches_progress_without_reprocessing_fresh_games(db, monkeypatch):
    rows = games(db)
    seen = []
    successful_details(monkeypatch, seen)
    assert worker.process_details(db, limit=2)["ready"] == 2
    assert worker.process_details(db, limit=2)["ready"] == 1
    assert set(seen) == {game.id for game in rows} and len(seen) == 3


def test_requested_game_has_priority_and_non_games_are_preserved(db, monkeypatch):
    rows = games(db)
    now = datetime.now(timezone.utc)
    db.add_all([SteamCatalogEntry(appid=game.steam_app_id, last_seen_at=now,
        status="non_game" if i == 0 else "pending", priority=100 if i == 2 else 0)
        for i, game in enumerate(rows)])
    db.commit()
    seen = []
    successful_details(monkeypatch, seen)
    result = worker.process_details(db, limit=1)
    assert result["attempted"] == 1 and seen == [rows[2].id]
    assert db.get(Game, rows[0].id) is not None


def test_outage_pauses_other_games_and_keeps_data(db, monkeypatch):
    rows = games(db)
    monkeypatch.setattr(steam_service, "get_app_details", lambda _: (_ for _ in ()).throw(steam_service.SteamUnavailable("offline")))
    first = worker.process_details(db, limit=3)
    assert first["attempted"] == first["deferred"] == 1
    second = worker.process_details(db, limit=3)
    assert second["attempted"] == 0 and second["retry_after_seconds"] > 0
    entry = db.get(SteamCatalogEntry, rows[0].steam_app_id)
    assert entry.status == "unavailable" and entry.next_attempt_at is not None
    assert len(list(db.scalars(select(Game)))) == 3


def test_cancellation_skips_remote_work(db, monkeypatch):
    games(db)
    seen = []
    successful_details(monkeypatch, seen)
    stop = threading.Event()
    stop.set()
    assert worker.process_details(db, stop_event=stop)["attempted"] == 0
    assert not seen


def test_no_key_skips_index_but_can_enrich_existing_apps(db, monkeypatch):
    games(db, 1)
    seen = []
    successful_details(monkeypatch, seen)
    def forbidden(*args, **kwargs):
        raise AssertionError("No index request without credentials")
    monkeypatch.setattr(worker, "sync_catalog", forbidden)
    result = worker.run_cycle(session_factory=sessionmaker(bind=db.get_bind()))
    assert result["index"] is None and result["details"]["ready"] == 1


def test_completed_index_waits_interval_and_partial_scan_resumes(db, monkeypatch):
    monkeypatch.setattr(settings, "STEAM_API_KEY", "test-only")
    db.add(SteamCatalogSync(id=1, completed_at=datetime.now(timezone.utc), status="idle"))
    db.commit()
    calls = []
    def sync(*args, **kwargs):
        calls.append(kwargs)
        return {"status": "running"}
    monkeypatch.setattr(worker, "sync_catalog", sync)
    factory = sessionmaker(bind=db.get_bind())
    worker.run_cycle(session_factory=factory)
    assert not calls
    state = db.get(SteamCatalogSync, 1)
    state.scan_started_at = datetime.now(timezone.utc)
    db.commit()
    worker.run_cycle(session_factory=factory)
    assert len(calls) == 1 and calls[0]["max_pages"] == 1


def test_public_status_does_not_call_steam_or_expose_key(client, monkeypatch):
    secret = "test-key-must-not-leak"
    monkeypatch.setattr(settings, "STEAM_API_KEY", secret)
    def forbidden(*args, **kwargs):
        raise AssertionError("Status must not trigger remote calls")
    monkeypatch.setattr(worker, "sync_catalog", forbidden)
    response = client.get("/api/v1/steam/catalog/status")
    assert response.status_code == 200
    assert response.json()["key_configured"] is True
    assert secret not in response.text


def test_store_retry_after_applies_to_whole_worker(db, monkeypatch):
    games(db, 2)
    def limited(_):
        raise steam_service.SteamUnavailable("Steam respondió 429", 600)
    monkeypatch.setattr(steam_service, "get_app_details", limited)
    assert worker.process_details(db, limit=2)["attempted"] == 1
    assert worker.process_details(db, limit=2)["retry_after_seconds"] >= 590


def test_remote_presence_works_across_sessions_and_expires(db, monkeypatch):
    monkeypatch.setattr(settings, "STEAM_CATALOG_WORKER_MODE", "external")
    monkeypatch.setattr(settings, "STEAM_API_KEY", "worker-only-secret")
    factory = sessionmaker(bind=db.get_bind())
    presence = worker.WorkerPresence(factory, "external")
    now = datetime.now(timezone.utc)
    presence.publish(now=now)
    # La API no necesita tener la clave que posee el proceso remoto.
    monkeypatch.setattr(settings, "STEAM_API_KEY", "")
    with factory() as api_db:
        active = worker.worker_status(api_db, now=now + timedelta(seconds=89))
        assert active["worker_running"] and active["key_configured"]
        assert active["worker_mode"] == "external"
        assert "worker-only-secret" not in str(active)
        expired = worker.worker_status(api_db, now=now + timedelta(seconds=91))
        assert not expired["worker_running"] and not expired["key_configured"]
        assert expired["worker_heartbeat"] is not None


def test_clean_stop_is_visible_without_waiting_for_expiry(db):
    factory = sessionmaker(bind=db.get_bind())
    presence = worker.WorkerPresence(factory, "external")
    presence.start()
    assert worker.worker_status(db)["worker_running"]
    presence.stop()
    db.expire_all()
    status = worker.worker_status(db)
    assert not status["worker_running"]
    assert db.get(SteamCatalogWorker, presence.worker_id).stopped_at is not None
    assert not presence._thread.is_alive()


def test_any_active_keyed_worker_counts_even_if_latest_has_no_key(db, monkeypatch):
    factory = sessionmaker(bind=db.get_bind())
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(settings, "STEAM_API_KEY", "worker-secret")
    worker.WorkerPresence(factory, "external").publish(now=now)
    monkeypatch.setattr(settings, "STEAM_API_KEY", "")
    worker.WorkerPresence(factory, "embedded").publish(now=now + timedelta(seconds=1))
    assert worker.worker_status(db, now=now + timedelta(seconds=2))["key_configured"]


def test_status_endpoint_sees_remote_worker_without_local_key(client, db, monkeypatch):
    monkeypatch.setattr(settings, "STEAM_API_KEY", "worker-only-secret")
    worker.WorkerPresence(sessionmaker(bind=db.get_bind()), "external").publish()
    monkeypatch.setattr(settings, "STEAM_API_KEY", "")
    monkeypatch.setattr(settings, "STEAM_CATALOG_WORKER_MODE", "external")
    response = client.get("/api/v1/steam/catalog/status")
    assert response.status_code == 200
    data = response.json()
    assert data["key_configured"] and data["worker_running"]
    assert data["status"] != "disabled"
    assert "worker-only-secret" not in response.text


def test_external_mode_does_not_start_embedded_thread(monkeypatch):
    monkeypatch.setattr(settings, "STEAM_CATALOG_WORKER_MODE", "external")
    monkeypatch.setattr(settings, "STEAM_CATALOG_WORKER_ENABLED", True)
    monkeypatch.setattr(worker, "_thread", None)
    worker.start_worker()
    assert worker._thread is None


def test_cancelled_cycle_does_not_connect_to_database_or_steam():
    stop = threading.Event()
    stop.set()
    def forbidden():
        raise AssertionError("A stopped cycle must not open a database session")
    assert worker.run_cycle(session_factory=forbidden, stop_event=stop) == {
        "index": None, "details": None,
    }


def test_run_forever_cleans_up_and_only_persists_safe_error(db, monkeypatch, caplog):
    stop = threading.Event()
    def failed_cycle(**kwargs):
        stop.set()
        raise RuntimeError("postgresql://password@host/db?key=secret-key")
    monkeypatch.setattr(worker, "run_cycle", failed_cycle)
    worker.run_forever(stop_event=stop, session_factory=sessionmaker(bind=db.get_bind()))
    rows = list(db.scalars(select(SteamCatalogWorker)))
    assert len(rows) == 1 and rows[0].stopped_at is not None
    assert rows[0].last_error == worker.RETRY_MESSAGE
    assert "password" not in caplog.text and "secret-key" not in caplog.text


@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT])
def test_standalone_uses_existing_schema_and_restores_signal_handlers(db, monkeypatch, stop_signal):
    monkeypatch.setattr(settings, "STEAM_CATALOG_WORKER_ENABLED", True)
    monkeypatch.setattr(worker_command, "SessionLocal", sessionmaker(bind=db.get_bind()))
    handlers = {signal.SIGINT: object(), signal.SIGTERM: object()}
    original = dict(handlers)
    def register(sig, handler):
        old = handlers[sig]
        handlers[sig] = handler
        return old
    def forbid_schema_creation():
        raise AssertionError("Production worker must require migrations")
    def until_cancelled(*, stop_event, mode):
        assert mode == "external" and not stop_event.is_set()
        handlers[stop_signal](stop_signal, None)
        assert stop_event.is_set()
    monkeypatch.setattr(worker_command.signal, "signal", register)
    monkeypatch.setattr(worker_command, "init_db", forbid_schema_creation)
    monkeypatch.setattr(worker_command, "run_forever", until_cancelled)
    assert worker_command.main() == 0
    assert handlers == original


def test_standalone_fails_cleanly_if_migrations_are_missing(db, tmp_path, monkeypatch, caplog):
    engine = create_engine(f"sqlite:///{tmp_path / 'unmigrated.db'}")
    monkeypatch.setattr(settings, "STEAM_CATALOG_WORKER_ENABLED", True)
    monkeypatch.setattr(worker_command, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(worker_command, "run_forever", lambda **_: pytest.fail("Missing schema must prevent the loop"))
    try:
        assert worker_command.main() == 1
        assert "OperationalError" in caplog.text
        assert "unmigrated.db" not in caplog.text
    finally:
        engine.dispose()


def test_worker_http_logging_omits_steam_key(monkeypatch, caplog):
    # Ejecutar una solicitud real de HTTPX contra un transporte local ejercita
    # el logger que, en INFO, publica la URL con los parámetros de autenticación.
    for name in ("httpx", "httpcore"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.INFO)
    with caplog.at_level(logging.INFO):
        worker_command.configure_logging()
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
            client.get("https://api.steampowered.com/IStoreService/GetAppList/v1/",
                       params={"key": "must-remain-private"})
        worker_command.logger.info("Worker operativo")
    assert "Worker operativo" in caplog.text
    assert "must-remain-private" not in caplog.text
    assert "HTTP Request" not in caplog.text
