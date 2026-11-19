"""Checks reales optativos, aislados en un esquema temporal de una BD de prueba.

GAMETRACK_TEST_POSTGRES_URL debe apuntar a una base cuyo nombre empiece por
gametrack_test. Nunca usa DATABASE_URL ni la base de la aplicación por defecto.
"""
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models import Game, Rating, Review, User
from app.services.steam_catalog_service import catalog_lock
from app.services.steam_catalog_worker import WorkerPresence, worker_status
from scripts.copy_sqlite_to_postgres import readonly_sqlite_engine, transfer, TransferError

URL = os.environ.get("GAMETRACK_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="requiere PostgreSQL de prueba explícito")


@pytest.fixture
def pg():
    url = make_url(URL)
    assert url.get_backend_name() == "postgresql"
    assert (url.database or "").startswith("gametrack_test"), "Usar una base de prueba dedicada"
    schema = "check_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    isolated_url = url.update_query_dict({"options": "-csearch_path=" + schema})
    engine = create_engine(isolated_url)
    try:
        root = Path(__file__).resolve().parents[1]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        config.set_main_option("sqlalchemy.url", isolated_url.render_as_string(hide_password=False).replace("%", "%%"))
        command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def test_postgres_migrations_and_copy_preserve_ids_and_sequences(pg, tmp_path):
    assert set(inspect(pg).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    path = tmp_path / "source.db"
    source = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(source)
    with Session(source) as db:
        db.add(User(id=40, username="migration-test", email="test@example.invalid"))
        db.add(Game(id=70, name="Juego de prueba", slug="migration-test", platforms=["Windows"]))
        db.commit()
        db.add(Rating(id=90, user_id=40, game_id=70, score=4.5))
        db.add(Review(id=100, user_id=40, game_id=70, content="Buena jugabilidad de prueba."))
        db.commit()
    source.dispose()
    source = readonly_sqlite_engine(path)
    try:
        plan = transfer(source, pg, Base.metadata)
        assert plan["mode"] == "dry-run" and plan["tables"]["games"] == 1
        with Session(pg) as db:
            assert db.scalar(select(Game.id)) is None
        result = transfer(source, pg, Base.metadata, execute=True, batch_size=1)
        assert result["mode"] == "copied"
        with Session(pg) as db:
            assert db.get(Game, 70).platforms == ["Windows"]
            assert db.get(Rating, 90).score == 4.5
            assert db.get(Review, 100).user_id == 40
            added = Game(name="Nuevo", slug="new-after-copy")
            db.add(added)
            db.commit()
            assert added.id == 71
        with pytest.raises(TransferError, match="contiene datos"):
            transfer(source, pg, Base.metadata, execute=True)
    finally:
        source.dispose()


def test_postgres_lock_coordinates_independent_pools_across_commits(pg):
    other = create_engine(pg.url)
    try:
        with Session(pg) as first, Session(other) as second:
            with catalog_lock(first) as acquired:
                assert acquired
                first.execute(text("SELECT 1"))
                first.commit()
                with catalog_lock(second) as contender:
                    assert not contender
            with catalog_lock(second) as available:
                assert available
    finally:
        other.dispose()


def test_postgres_presence_is_visible_to_another_api_without_key(pg, monkeypatch):
    from app.core.config import settings
    from app.api.v1.endpoints.steam_catalog import catalog_status
    monkeypatch.setattr(settings, "STEAM_CATALOG_WORKER_MODE", "external")
    monkeypatch.setattr(settings, "STEAM_API_KEY", "test-worker-key")
    presence = WorkerPresence(sessionmaker(bind=pg), "external")
    presence.publish()
    other = create_engine(pg.url)
    try:
        monkeypatch.setattr(settings, "STEAM_API_KEY", "")
        with Session(other) as db:
            status = catalog_status(db)
            assert status["worker_running"] and status["key_configured"]
            assert status["status"] != "disabled"
        presence.stop()
        with Session(other) as db:
            assert not worker_status(db)["worker_running"]
    finally:
        other.dispose()
