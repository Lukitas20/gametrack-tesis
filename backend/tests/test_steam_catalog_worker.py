"""El mantenimiento no depende de visitas ni bloquea el catálogo por falta de clave."""
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.models import Game, SteamCatalogEntry, SteamCatalogSync
from app.services import steam_catalog_worker as worker, steam_service
from tests.test_steam import client  # noqa: F401 - fixture HTTP con la base de este archivo


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "STEAM_API_KEY", "")
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
