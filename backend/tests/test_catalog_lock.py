"""Distributed catalog locking without network access or a PostgreSQL server."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.services import steam_catalog_service as service


class FakePostgres:
    def __init__(self):
        self.holder = None


class FakeConnection:
    def __init__(self, engine):
        self.engine = engine
        self.options = {}
        self.statements = []
        self.closed = False
        self.invalidated = False

    def execution_options(self, **options):
        if self.engine.fail_options:
            raise SQLAlchemyError("Driver failed with private connection details")
        self.options.update(options)
        return self

    def scalar(self, statement, parameters):
        statement = str(statement)
        self.statements.append((statement, parameters))
        assert self.options["isolation_level"] == "AUTOCOMMIT"
        if "pg_try_advisory_lock" in statement:
            if self.engine.server.holder is not None:
                return False
            self.engine.server.holder = self
            if self.engine.fail_acquire:
                # Model a lost response after PostgreSQL acquired the lock.
                raise SQLAlchemyError("Acquisition failed with private connection details")
            return True
        assert "pg_advisory_unlock" in statement
        if self.engine.fail_unlock:
            raise SQLAlchemyError("Unlock failed with private connection details")
        if self.engine.unlock_false:
            return False
        assert self.engine.server.holder is self
        self.engine.server.holder = None
        return True

    def invalidate(self):
        self.invalidated = True
        if self.engine.server.holder is self:
            self.engine.server.holder = None

    def close(self):
        # SQLAlchemy close() returns a pooled session; it does not release
        # session-level locks unless the physical connection was invalidated.
        self.closed = True


class FakeEngine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, server=None, **faults):
        self.engine = self
        self.server = server or FakePostgres()
        self.connections = []
        self.fail_connect = faults.get("fail_connect", False)
        self.fail_options = faults.get("fail_options", False)
        self.fail_acquire = faults.get("fail_acquire", False)
        self.fail_unlock = faults.get("fail_unlock", False)
        self.unlock_false = faults.get("unlock_false", False)

    @property
    def url(self):
        raise AssertionError("Database credentials and hostnames must not define the lock")

    def connect(self):
        if self.fail_connect:
            raise SQLAlchemyError("Connect failed with private connection details")
        connection = FakeConnection(self)
        self.connections.append(connection)
        return connection


class FakeSession:
    def __init__(self, engine, *, connection_bound=False):
        self.bind = SimpleNamespace(engine=engine, dialect=engine.dialect) if connection_bound else engine
        self.commits = 0

    def get_bind(self):
        return self.bind

    def commit(self):
        self.commits += 1


@pytest.mark.parametrize("connection_bound", [False, True])
def test_distributed_lock_survives_importer_commits_and_host_aliases(connection_bound):
    first_engine = FakeEngine()
    # Distinct application processes/engines connecting to the same database.
    other_engine = FakeEngine(first_engine.server)
    session = FakeSession(first_engine, connection_bound=connection_bound)
    with service.catalog_lock(session) as acquired:
        assert acquired
        holder = first_engine.connections[0]
        session.commit()
        session.commit()
        assert first_engine.server.holder is holder
        assert len(holder.statements) == 1
        with service.catalog_lock(FakeSession(other_engine)) as busy:
            assert not busy
        contender = other_engine.connections[0]
        assert contender.closed and not contender.invalidated
        assert len(contender.statements) == 1
        assert holder.statements[0][1] == contender.statements[0][1]
    assert holder.closed and not holder.invalidated
    assert first_engine.server.holder is None
    assert holder.statements[0][1] == holder.statements[1][1]
    with service.catalog_lock(FakeSession(other_engine)) as next_worker:
        assert next_worker


def test_import_failure_releases_lock_without_swallowing_original_error():
    engine = FakeEngine()
    original = ValueError("Import failed")
    with pytest.raises(ValueError) as caught:
        with service.catalog_lock(FakeSession(engine)) as acquired:
            assert acquired
            raise original
    assert caught.value is original
    assert engine.server.holder is None
    assert engine.connections[0].closed
    with service.catalog_lock(FakeSession(engine)) as retry:
        assert retry


@pytest.mark.parametrize("fault", ["fail_connect", "fail_options", "fail_acquire"])
def test_failed_acquisition_discards_uncertain_session_and_sanitizes_error(fault):
    engine = FakeEngine(**{fault: True})
    entered = False
    with pytest.raises(service.CatalogError) as caught:
        with service.catalog_lock(FakeSession(engine)):
            entered = True
    assert not entered
    assert "private connection details" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert engine.server.holder is None
    for connection in engine.connections:
        assert connection.invalidated and connection.closed


@pytest.mark.parametrize("fault", ["fail_unlock", "unlock_false"])
def test_unlock_failure_cannot_return_a_locked_session_to_the_pool(fault):
    engine = FakeEngine(**{fault: True})
    with service.catalog_lock(FakeSession(engine)) as acquired:
        assert acquired
    assert engine.connections[0].invalidated and engine.connections[0].closed
    assert engine.server.holder is None
    assert len(engine.connections[0].statements) == 2


def test_cleanup_failure_preserves_import_exception():
    engine = FakeEngine(fail_unlock=True)
    original = RuntimeError("Import interrupted")
    with pytest.raises(RuntimeError) as caught:
        with service.catalog_lock(FakeSession(engine)):
            raise original
    assert caught.value is original
    assert engine.connections[0].invalidated and engine.connections[0].closed
    assert engine.server.holder is None


@pytest.mark.parametrize("local_key", ["", "local-test-key"])
@pytest.mark.parametrize("remote_key_configured", [None, False, True])
def test_catalog_status_can_use_remote_worker_key_without_requiring_it_in_api(
    monkeypatch, local_key, remote_key_configured,
):
    monkeypatch.setattr(settings, "STEAM_API_KEY", local_key)
    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            status = service.get_catalog_status(db, key_configured=remote_key_configured)
        expected = bool(local_key) if remote_key_configured is None else remote_key_configured
        assert status["key_configured"] is expected
        assert (status["status"] == "disabled") is (not expected)
    finally:
        engine.dispose()
