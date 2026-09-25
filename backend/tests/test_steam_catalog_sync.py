"""Índice oficial sin red: checkpoints, transacciones, formato y seguridad."""

from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.models import Game, SteamCatalogEntry, SteamCatalogSync
from app.services import steam_catalog_service as service

NOW = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STEAM_API_KEY", "local-test-secret")
    engine = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def app(appid, name=None, modified=100):
    return {"appid": appid, "name": name or f"Juego {appid}", "last_modified": modified}


def page(apps, more=False):
    return {"response": {"apps": apps, "have_more_results": more}}


def call(db, payload, **kwargs):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        return service.sync_catalog(db, client=client, now=NOW, delay=0, **kwargs)


def test_checkpoint_survives_new_session_and_incremental_cutoff(db):
    requests = []

    def transport(request):
        data = json.loads(request.url.params["input_json"])
        requests.append(data)
        assert request.url.path == "/IStoreService/GetAppList/v1/"
        assert request.url.params["key"] == "local-test-secret"
        assert data["include_games"] is True
        assert not any(data[key] for key in ("include_dlc", "include_software", "include_videos", "include_hardware"))
        return httpx.Response(200, json=page([app(10), app(20)], True) if len(requests) == 1 else page([app(30)]))

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        first = service.sync_catalog(db, max_pages=1, page_size=2, client=client, now=NOW, delay=0)
        assert first["status"] == "running" and first["partial"]
        assert first["cursor_appid"] == 20 and first["since"] == 0
        assert first["completed_at"] is None and first["revision"] == 0
        with Session(db.get_bind()) as resumed:
            final = service.sync_catalog(resumed, client=client, page_size=2, now=NOW + timedelta(hours=2), delay=0)
        assert requests[1]["last_appid"] == 20 and requests[1]["if_modified_since"] == 0
        assert final["status"] == "idle" and not final["partial"]
        assert final["processed"] == final["created"] == 3
        assert final["since"] == int(NOW.timestamp()) - 300
        service.sync_catalog(db, client=client, page_size=2, now=NOW + timedelta(hours=3), delay=0)
        assert requests[2]["last_appid"] == 0
        assert requests[2]["if_modified_since"] == int(NOW.timestamp()) - 300
    assert db.scalar(select(func.count(Game.id))) == 3
    assert db.scalar(select(func.count(SteamCatalogEntry.appid))) == 3


def test_no_key_does_not_request_or_mutate_catalog(db, monkeypatch):
    monkeypatch.setattr(settings, "STEAM_API_KEY", "")
    db.add(Game(name="Conservado", slug="keep", steam_app_id=7))
    db.commit()

    def forbidden(*args, **kwargs):
        raise AssertionError("No se debe pedir Steam sin clave")

    monkeypatch.setattr(service.httpx, "Client", forbidden)
    result = service.sync_catalog(db, now=NOW)
    assert result["status"] == "disabled" and result["pages_this_run"] == 0
    assert "STEAM_API_KEY" in result["last_error"]
    assert db.scalar(select(func.count(Game.id))) == 1
    assert db.scalar(select(func.count(SteamCatalogEntry.appid))) == 0
    assert result["scan_started_at"] is None


def test_update_keeps_identity_and_metadata_and_enqueues_changed_source(db):
    game = Game(name="Nombre anterior", slug="url-estable", steam_app_id=10,
                description="Ficha conservada", steam_synced_at=NOW,
                avg_rating=4.5, ratings_count=10)
    db.add(game)
    db.add(SteamCatalogEntry(appid=10, source_modified=100, status="ready", last_seen_at=NOW))
    db.commit()
    original_id = game.id
    result = call(db, page([app(10, "Nombre nuevo", modified=200)]))
    db.refresh(game)
    entry = db.get(SteamCatalogEntry, 10)
    assert game.id == original_id and game.slug == "url-estable"
    assert game.name == "Nombre nuevo" and game.description == "Ficha conservada"
    assert game.avg_rating == 4.5 and game.ratings_count == 10 and game.steam_synced_at
    assert entry.status == "pending" and entry.source_modified == 200
    assert entry.next_attempt_at is None
    assert result["updated"] == 1 and result["revision"] == 1
    again = call(db, page([app(10, "Nombre nuevo", modified=200)]))
    assert again["updated"] == 0 and again["revision"] == 1


def test_empty_index_never_deletes_old_games(db):
    db.add(Game(name="No aparece", slug="old", steam_app_id=1))
    db.commit()
    result = call(db, page([]))
    assert result["status"] == "idle" and result["processed"] == 0
    assert db.scalar(select(func.count(Game.id))) == 1


@pytest.mark.parametrize("payload", [
    {}, {"response": {}}, {"response": {"apps": None}},
    page([{"appid": True, "name": "Inválido"}]),
    page([{"appid": 1, "name": None}]),
    page([app(2), app(1)]), page([app(1), app(1)]),
    page([app(1, modified=-1)]), page([], True),
    {"response": {"apps": [app(1)], "have_more_results": "false"}},
    {"response": {"apps": [app(1)], "last_appid": 50}},
])
def test_bad_payload_is_not_mistaken_for_completed_catalog(db, payload):
    result = call(db, payload)
    assert result["status"] == "error" and result["partial"]
    assert result["cursor_appid"] == result["since"] == 0
    assert result["completed_at"] is None
    assert db.scalar(select(func.count(Game.id))) == 0


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_errors_keep_checkpoint_and_never_expose_key_or_body(db, status):
    call(db, page([app(10)], True), max_pages=1, page_size=1)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        status, headers={"Retry-After": "120"}, text="private local-test-secret"
    ))) as client:
        result = service.sync_catalog(db, client=client, now=NOW, delay=0)
    assert result["status"] == "error" and result["cursor_appid"] == 10
    assert result["processed"] == 1 and result["since"] == 0
    assert "local-test-secret" not in json.dumps(result, default=str)
    assert "private" not in result["last_error"]
    assert result["retry_after_seconds"] >= 60
    if status == 429:
        assert result["retry_after_seconds"] == 120
    resumed = call(db, page([app(20)]))
    assert resumed["status"] == "idle" and resumed["created"] == 2


def test_transport_error_sanitizes_exception(db):
    def fail(request):
        raise httpx.ConnectError("private local-test-secret", request=request)
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        result = service.sync_catalog(db, client=client, now=NOW, delay=0)
    assert result["status"] == "error"
    assert "private" not in result["last_error"] and "local-test-secret" not in result["last_error"]


def test_html_response_not_complete(db):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<html>try later</html>"))) as client:
        result = service.sync_catalog(db, client=client, now=NOW)
    assert result["status"] == "error" and result["completed_at"] is None


def test_rollback_page_and_cursor_together(db, monkeypatch):
    call(db, page([app(10)], True), max_pages=1, page_size=1)
    apply = service._apply_page
    def fail_after_insert(*args):
        apply(*args)
        raise service.CatalogError("Fallo simulado antes del commit")
    monkeypatch.setattr(service, "_apply_page", fail_after_insert)
    failed = call(db, page([app(20)]))
    assert failed["status"] == "error" and failed["cursor_appid"] == 10
    assert db.scalar(select(func.count(Game.id))) == 1
    assert db.get(SteamCatalogEntry, 20) is None
    monkeypatch.setattr(service, "_apply_page", apply)
    resumed = call(db, page([app(20)]))
    assert resumed["created"] == 2 and resumed["status"] == "idle"


def test_large_page_uses_bounded_lookups_and_unique_short_slugs(db):
    db.add(Game(name="Colisión", slug="steam-steam-1"))
    db.commit()
    sizes = []
    selects = []
    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)
        if " IN (" in statement:
            sizes.append(len(parameters))
    event.listen(db.get_bind(), "before_cursor_execute", observe)
    try:
        result = call(db, page([app(i, "日本語" if i < 3 else "x" * 500) for i in range(1, 1002)]), page_size=2000)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe)
    assert result["created"] == 1001 and result["revision"] == 0
    assert max(sizes) <= 400 and len(selects) < 30
    slugs = db.scalars(select(Game.slug)).all()
    assert len(set(slugs)) == 1002 and max(map(len, slugs)) <= 120
    assert db.scalar(select(Game.slug).where(Game.steam_app_id == 1)) == "steam-steam-1-1"


def test_lock_prevents_duplicate_worker_and_cli(db):
    # Dos Engines contra el mismo archivo también comparten exclusión.
    other_engine = create_engine(db.get_bind().url)
    try:
        with service.catalog_lock(db) as first, Session(other_engine) as other:
            assert first
            with service.catalog_lock(other) as second:
                assert not second
            result = service.sync_catalog(other, now=NOW)
            assert result["busy"] and result["pages_this_run"] == 0
        with service.catalog_lock(db) as after:
            assert after
    finally:
        other_engine.dispose()


def test_revision_participates_in_callers_transaction(db):
    call(db, page([]))
    service.bump_catalog_revision(db)
    service.bump_catalog_revision(db)
    assert service.get_catalog_status(db)["revision"] == 2
    db.rollback()
    assert service.get_catalog_status(db)["revision"] == 0


def test_unnamed_steam_entries_advance_cursor_without_inventing_titles(db):
    # Observado en el índice público: appid396420 tiene name="".
    first = call(db, page([app(10), {"appid": 20, "name": "", "last_modified": 100}], True),
                 max_pages=1, page_size=2)
    assert first["partial"] and first["cursor_appid"] == 20
    assert first["processed"] == 2 and first["created"] == 1
    assert db.scalar(select(Game).where(Game.steam_app_id == 20)) is None
    second = call(db, page([app(30)]), page_size=2)
    assert not second["partial"] and second["created"] == 2
    # El corte incremental vuelve a recorrer desde AppID0: aparece su nombre.
    final = call(db, page([app(20, "Nombre publicado", modified=200)]), page_size=2)
    assert final["status"] == "idle"
    assert db.scalar(select(Game.name).where(Game.steam_app_id == 20)) == "Nombre publicado"
