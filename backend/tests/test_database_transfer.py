"""Data-transfer safety with real SQLite snapshots and a PostgreSQL control fake.

The fake only intercepts PostgreSQL transaction controls. All row reads, typed
inserts, foreign keys, counts, commits and rollbacks use actual SQLite databases.
Native PostgreSQL locking/sequence syntax still needs a PostgreSQL smoke test.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum as PythonEnum
from hashlib import sha256
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, JSON,
    MetaData, String, Table, create_engine, event, select,
)
from sqlalchemy.dialects.postgresql import dialect as postgres_dialect
from sqlalchemy.exc import IntegrityError, OperationalError

from scripts import copy_sqlite_to_postgres as migration


class Kind(PythonEnum):
    PLAYER = "player"
    DEVELOPER = "developer"


@pytest.fixture
def databases(tmp_path):
    metadata = MetaData()
    # Register child first, ensuring transfer follows FK dependency order.
    library = Table(
        "library", metadata,
        Column("id", Integer, primary_key=True),
        Column("account_id", ForeignKey("accounts.id"), nullable=False),
        Column("note", String),
        Column("score", Float),
    )
    accounts = Table(
        "accounts", metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String, nullable=False, unique=True),
        Column("active", Boolean, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True)),
        Column("profile", JSON),
        Column("kind", Enum(Kind, native_enum=False, values_callable=lambda cls: [v.value for v in cls])),
    )
    workers = Table(
        "steam_catalog_workers", metadata,
        Column("id", Integer, primary_key=True),
        Column("heartbeat_at", DateTime(timezone=True)),
    )
    source_path = tmp_path / "source with space #.db"
    source_writer = create_engine(f"sqlite:///{source_path}")
    target = create_engine(f"sqlite:///{tmp_path / 'target.db'}")
    for engine in (source_writer, target):
        @event.listens_for(engine, "connect")
        def configure(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
        metadata.create_all(engine)
    with source_writer.begin() as db:
        db.execute(accounts.insert(), [
            {"id": index, "name": f"Jugador {index}", "active": index == 9,
             "created_at": datetime(2026, 9, 28, 17, 8, 6, 123456, tzinfo=timezone.utc),
             "updated_at": None, "profile": {"preferencias": ["acción", "coop"], "peso": 0.75},
             "kind": Kind.PLAYER if index == 9 else Kind.DEVELOPER}
            for index in (9, 47, 900)
        ])
        db.execute(library.insert(), [
            {"id": 30, "account_id": 9, "note": "Tesis ✓", "score": 0.0},
            {"id": 500, "account_id": 47, "note": None, "score": None},
        ])
        db.execute(workers.insert(), {"id": 1, "heartbeat_at": datetime.now(timezone.utc)})
    readonly = migration.readonly_sqlite_engine(source_path)
    yield SimpleNamespace(
        source=readonly, writer=source_writer, path=source_path, target=target,
        metadata=metadata, accounts=accounts, library=library, workers=workers,
    )
    readonly.dispose()
    source_writer.dispose()
    target.dispose()


class PostgresConnectionControl:
    dialect = postgres_dialect()

    def __init__(self, connection, controls):
        self.connection = connection
        self.controls = controls

    def execute(self, *args, **kwargs):
        return self.connection.execute(*args, **kwargs)

    def scalar(self, *args, **kwargs):
        return self.connection.scalar(*args, **kwargs)

    def exec_driver_sql(self, sql):
        self.controls.statements.append(sql)
        if sql.startswith("LOCK TABLE") and self.controls.on_lock:
            self.controls.on_lock(self.connection)


class PostgresTransactionControl:
    dialect = postgres_dialect()

    def __init__(self, sqlite_engine):
        self.engine = sqlite_engine
        self.statements = []
        self.on_lock = None
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def begin(self):
        try:
            with self.engine.begin() as connection:
                yield PostgresConnectionControl(connection, self)
        except BaseException:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1


@pytest.fixture
def control(databases, monkeypatch):
    destination = PostgresTransactionControl(databases.target)
    original_validate = migration._validate_schema

    def validate(connection, tables, *, source):
        original_validate(
            getattr(connection, "connection", connection) if not source else connection,
            tables, source=source,
        )

    monkeypatch.setattr(migration, "_validate_schema", validate)
    monkeypatch.setattr(migration, "_reset_sequences", lambda *_args: None)
    return destination


def row_snapshot(engine, table):
    with engine.connect() as db:
        return db.execute(select(table).order_by(*table.primary_key.columns)).mappings().all()


def test_default_dry_run_never_inserts_and_reports_persistent_rows(databases, control):
    original = sha256(databases.path.read_bytes()).digest()
    report = migration.transfer(databases.source, control, databases.metadata)
    assert report == {
        "mode": "dry-run", "tables": {"accounts": 3, "library": 2},
        "total_rows": 5, "omitted_transient_tables": ["steam_catalog_workers"],
    }
    assert control.statements == []
    assert row_snapshot(databases.target, databases.accounts) == []
    assert sha256(databases.path.read_bytes()).digest() == original


@pytest.mark.parametrize("execute", [False, True])
def test_legacy_sqlite_oversized_text_is_reported_without_truncation(databases, control, execute):
    databases.accounts.c.name.type.length = 4
    original = sha256(databases.path.read_bytes()).digest()
    with pytest.raises(migration.TransferError, match=r"accounts\.name") as error:
        migration.transfer(databases.source, control, databases.metadata, execute=execute)
    assert "Jugador" not in str(error.value)
    assert row_snapshot(databases.target, databases.accounts) == []
    assert sha256(databases.path.read_bytes()).digest() == original


def test_explicit_copy_preserves_ids_relations_typed_values_and_source(databases, control):
    original = sha256(databases.path.read_bytes()).digest()
    report = migration.transfer(
        databases.source, control, databases.metadata, execute=True, batch_size=2,
    )
    assert report["mode"] == "copied"
    for table in (databases.accounts, databases.library):
        assert row_snapshot(databases.target, table) == row_snapshot(databases.source, table)
    account = row_snapshot(databases.target, databases.accounts)[0]
    assert account["active"] is True
    assert account["kind"] is Kind.PLAYER
    assert account["profile"]["preferencias"] == ["acción", "coop"]
    assert account["created_at"].microsecond == 123456
    assert row_snapshot(databases.target, databases.workers) == []
    assert sha256(databases.path.read_bytes()).digest() == original
    assert control.commits == 1
    assert "SET LOCAL TIME ZONE 'UTC'" in control.statements
    assert any(sql.startswith("LOCK TABLE") for sql in control.statements)


@pytest.mark.parametrize("execute", [False, True])
def test_existing_destination_is_rejected_before_lock_without_overwriting(databases, control, execute):
    with databases.target.begin() as db:
        db.execute(databases.workers.insert(), {"id": 99})
    with pytest.raises(migration.TransferError, match="contiene datos"):
        migration.transfer(databases.source, control, databases.metadata, execute=execute)
    assert control.statements == []
    assert row_snapshot(databases.target, databases.workers)[0]["id"] == 99
    assert row_snapshot(databases.target, databases.accounts) == []


def test_second_empty_check_detects_a_writer_that_won_before_lock(databases, control):
    control.on_lock = lambda db: db.execute(databases.workers.insert(), {"id": 99})
    with pytest.raises(migration.TransferError, match="contiene datos"):
        migration.transfer(databases.source, control, databases.metadata, execute=True)
    assert control.rollbacks == 1
    assert row_snapshot(databases.target, databases.accounts) == []


def test_insert_error_rolls_back_previous_batches_and_tables(databases, control, monkeypatch):
    original_copy = migration._copy_rows

    def fail_after_copy(source, destination, tables, batch_size):
        original_copy(source, destination, tables, batch_size)
        # A real FK failure after otherwise valid inserts must undo them all.
        destination.execute(databases.library.insert(), {"id": 700, "account_id": 999999})

    monkeypatch.setattr(migration, "_copy_rows", fail_after_copy)
    with pytest.raises(IntegrityError):
        migration.transfer(databases.source, control, databases.metadata, execute=True, batch_size=1)
    assert control.rollbacks == 1 and control.commits == 0
    for table in databases.metadata.sorted_tables:
        assert row_snapshot(databases.target, table) == []


def test_count_mismatch_rolls_back_copy(databases, control, monkeypatch):
    original_copy = migration._copy_rows

    def incomplete_copy(source, destination, tables, batch_size):
        report = original_copy(source, destination, tables, batch_size)
        destination.execute(databases.library.delete().where(databases.library.c.id == 30))
        return report

    monkeypatch.setattr(migration, "_copy_rows", incomplete_copy)
    with pytest.raises(migration.TransferError, match="recuentos"):
        migration.transfer(databases.source, control, databases.metadata, execute=True)
    assert row_snapshot(databases.target, databases.accounts) == []


@pytest.mark.parametrize("side", ["source", "target"])
def test_missing_or_old_schema_rejected_before_copy(databases, control, side):
    engine = databases.writer if side == "source" else databases.target
    with engine.begin() as db:
        db.exec_driver_sql("ALTER TABLE accounts ADD COLUMN unexpected TEXT")
    with pytest.raises(migration.TransferError, match="esquema"):
        migration.transfer(databases.source, control, databases.metadata, execute=True)
    assert control.statements == []
    assert row_snapshot(databases.target, databases.library) == []


def test_source_can_predate_worker_heartbeat_table(databases, control):
    with databases.writer.begin() as db:
        databases.workers.drop(db)
    report = migration.transfer(databases.source, control, databases.metadata, execute=True)
    assert report["total_rows"] == 5


@pytest.mark.parametrize("batch_size", [0, -1, 1001, True, 1.5, "500", None])
def test_invalid_batch_sizes_never_connect(databases, control, batch_size):
    with pytest.raises(migration.TransferError, match="lote"):
        migration.transfer(databases.source, control, databases.metadata, batch_size=batch_size)
    assert control.commits == 0 and control.rollbacks == 0


def test_only_postgres_destination_accepted(databases):
    with pytest.raises(migration.TransferError, match="PostgreSQL"):
        migration.transfer(databases.source, databases.target, databases.metadata)


def test_readonly_sqlite_cannot_create_missing_file_or_modify_existing(databases, tmp_path):
    missing = tmp_path / "absent.db"
    with pytest.raises(migration.TransferError, match="no existe"):
        migration.readonly_sqlite_engine(missing)
    assert not missing.exists()
    with databases.source.connect() as db:
        with pytest.raises(OperationalError, match="readonly"):
            db.execute(databases.library.delete())
    assert len(row_snapshot(databases.source, databases.library)) == 2


def test_readonly_snapshot_reads_committed_wal_and_stays_consistent(tmp_path):
    path = tmp_path / "live.db"
    writer = create_engine(f"sqlite:///{path}")
    reader = None
    try:
        with writer.connect() as write:
            write.exec_driver_sql("PRAGMA journal_mode=WAL")
            write.exec_driver_sql("PRAGMA wal_autocheckpoint=0")
            write.exec_driver_sql("CREATE TABLE entries (id INTEGER PRIMARY KEY)")
            write.exec_driver_sql("INSERT INTO entries VALUES (1)")
            write.commit()
            assert path.with_name("live.db-wal").is_file()
            reader = migration.readonly_sqlite_engine(path)
            with reader.connect() as read:
                read.exec_driver_sql("BEGIN")
                assert read.exec_driver_sql("SELECT count(*) FROM entries").scalar() == 1
                write.exec_driver_sql("INSERT INTO entries VALUES (2)")
                write.commit()
                assert read.exec_driver_sql("SELECT count(*) FROM entries").scalar() == 1
                read.rollback()
                assert read.exec_driver_sql("SELECT count(*) FROM entries").scalar() == 2
    finally:
        if reader is not None:
            reader.dispose()
        writer.dispose()


@pytest.mark.parametrize("maximum, expected", [(None, 1), (0, 1), (-5, 1), (900, 901)])
def test_sequence_reset_quotes_identifiers_and_uses_transactional_restart(maximum, expected):
    metadata = MetaData()
    table = Table('Odd"Table', metadata, Column("id", Integer, primary_key=True), schema="Custom Schema")
    no_sequence = Table("manual", metadata, Column("id", Integer, primary_key=True, autoincrement=False))
    calls = []
    statements = []

    class Connection:
        dialect = postgres_dialect()

        def execute(self, sql, params):
            calls.append((str(sql), params))
            return SimpleNamespace(first=lambda: ('Odd"Schema', 'items"; DROP TABLE users;--'))

        def scalar(self, _sql):
            return maximum

        def exec_driver_sql(self, sql):
            statements.append(sql)

    migration._reset_sequences(Connection(), [table, no_sequence])
    assert len(calls) == 1
    assert calls[0][1] == {"table_name": '"Custom Schema"."Odd""Table"', "column_name": "id"}
    assert statements == [
        f'ALTER SEQUENCE "Odd""Schema"."items""; DROP TABLE users;--" RESTART WITH {expected}'
    ]
    assert "setval" not in calls[0][0]


def test_no_sequence_means_no_reset():
    metadata = MetaData()
    table = Table("plain_id", metadata, Column("id", Integer, primary_key=True))
    connection = SimpleNamespace(
        dialect=postgres_dialect(), execute=lambda *_args: SimpleNamespace(first=lambda: None),
    )
    migration._reset_sequences(connection, [table])


def test_cli_sanitizes_database_exceptions_and_disposes_resources(monkeypatch, capsys, tmp_path):
    disposed = []
    source = SimpleNamespace(dispose=lambda: disposed.append("source"))
    monkeypatch.setattr(migration, "readonly_sqlite_engine", lambda _path: source)

    def fail_connection(*_args, **_kwargs):
        raise RuntimeError("postgresql://secret-user:secret-password@private-host/db and private row data")

    monkeypatch.setattr(migration, "create_engine", fail_connection)
    assert migration.main(["--source", str(tmp_path / "ignored.db")]) == 1
    output = capsys.readouterr()
    assert "No se pudo completar" in output.err
    assert not output.out
    for secret in ("secret-user", "secret-password", "private-host", "private row data"):
        assert secret not in output.err
    assert disposed == ["source"]


@pytest.mark.parametrize("execute", [False, True])
def test_cli_requires_explicit_execute_flag(monkeypatch, capsys, tmp_path, execute):
    options = []
    disposable = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setattr(migration, "readonly_sqlite_engine", lambda _path: disposable)
    monkeypatch.setattr(migration, "create_engine", lambda *_args, **_kwargs: disposable)

    def fake_transfer(*_args, **kwargs):
        options.append(kwargs)
        return {"mode": "copied" if kwargs["execute"] else "dry-run", "total_rows": 0}

    monkeypatch.setattr(migration, "transfer", fake_transfer)
    arguments = ["--source", str(tmp_path / "ignored.db")]
    if execute:
        arguments.append("--execute")
    assert migration.main(arguments) == 0
    assert options == [{"execute": execute, "batch_size": 500}]
    assert not capsys.readouterr().err
