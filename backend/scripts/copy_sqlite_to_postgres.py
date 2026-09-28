#!/usr/bin/env python
"""Copy a local GameTrack database into an already migrated, empty PostgreSQL DB.

    python scripts/copy_sqlite_to_postgres.py --source ./gametrack.db
    python scripts/copy_sqlite_to_postgres.py --source ./gametrack.db --execute

The destination is settings.DATABASE_URL; credentials are never CLI arguments
or output. The default only validates the schemas and prints row counts. Stop
the destination API/worker before copying and start them after a successful copy.
The SQLite connection reads one consistent snapshot, including committed WAL
data; keep its -wal/-shm files with the database if they exist. SQLite timestamps
are interpreted as UTC, as in GameTrack. Use a direct PostgreSQL connection.
This does not copy the local trained model artifact or modify the source.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import Integer, MetaData, String, create_engine, func, inspect, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import Table


# These rows describe running processes, not persistent application data.
TRANSIENT_TABLES = frozenset({"steam_catalog_workers"})
MAX_BATCH_SIZE = 1000


class TransferError(Exception):
    """An intentionally sanitized error suitable for displaying in the CLI."""


def readonly_sqlite_engine(source: Path) -> Engine:
    """Open an existing file; SQLite must never create or change the source."""
    try:
        path = source.resolve(strict=True)
    except (OSError, RuntimeError):
        raise TransferError("El archivo SQLite de origen no existe o no es accesible.") from None
    if not path.is_file():
        raise TransferError("El origen debe ser un archivo SQLite existente.")
    uri = f"{path.as_uri()}?mode=ro"

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(uri, uri=True, timeout=30)
        connection.execute("PRAGMA query_only = ON")
        return connection

    return create_engine(
        "sqlite://", creator=connect, poolclass=NullPool,
        echo=False, hide_parameters=True,
    )


def _validate_schema(
    connection: Connection, tables: list[Table], *, source: bool,
) -> None:
    """Reject old/partial schemas before attempting a single insert."""
    inspector = inspect(connection)
    side = "origen SQLite" if source else "destino PostgreSQL"
    for table in tables:
        if source and table.name in TRANSIENT_TABLES:
            continue
        if not inspector.has_table(table.name, schema=table.schema):
            raise TransferError(
                f"Falta la tabla {table.name} en el {side}; aplicá las migraciones primero."
            )
        columns = {
            column["name"]
            for column in inspector.get_columns(table.name, schema=table.schema)
        }
        expected = {column.name for column in table.columns}
        if columns != expected:
            raise TransferError(
                f"El esquema de {table.name} en el {side} no coincide; "
                "aplicá las migraciones primero."
            )


def _assert_empty(connection: Connection, tables: list[Table]) -> None:
    # Inspect all application tables, even ones not copied (worker heartbeats).
    occupied = [
        table.name for table in tables
        if connection.execute(select(1).select_from(table).limit(1)).first() is not None
    ]
    if occupied:
        raise TransferError(
            "El destino contiene datos en: " + ", ".join(occupied)
            + ". Usá una base vacía; no se borró ni reemplazó información."
        )


def _source_counts(connection: Connection, tables: list[Table]) -> dict[str, int]:
    return {
        table.name: int(connection.scalar(select(func.count()).select_from(table)) or 0)
        for table in tables if table.name not in TRANSIENT_TABLES
    }


def _validate_text_lengths(connection: Connection, tables: list[Table]) -> None:
    """SQLite no impone VARCHAR(n); avisar antes de copiar sin recortar historia."""
    oversized = []
    for table in tables:
        if table.name in TRANSIENT_TABLES:
            continue
        columns = [column for column in table.columns
                   if isinstance(column.type, String) and column.type.length]
        if not columns:
            continue
        maxima = connection.execute(select(*[
            func.max(func.length(column)) for column in columns
        ])).one()
        oversized.extend(f"{table.name}.{column.name}" for column, maximum in zip(columns, maxima)
                         if maximum is not None and maximum > column.type.length)
    if oversized:
        raise TransferError("El origen excede límites de texto de PostgreSQL en: "
                            + ", ".join(oversized) + ". Revisá esos campos; no se recortaron datos.")


def _copy_rows(
    source: Connection, destination: Connection, tables: list[Table], batch_size: int,
) -> dict[str, int]:
    """Use metadata types for JSON/date/boolean/enum conversion across dialects."""
    copied: dict[str, int] = {}
    for table in tables:
        if table.name in TRANSIENT_TABLES:
            continue
        copied[table.name] = 0
        rows = source.execute(select(table)).mappings()
        try:
            while batch := rows.fetchmany(batch_size):
                destination.execute(table.insert(), [dict(row) for row in batch])
                copied[table.name] += len(batch)
        finally:
            rows.close()
    return copied


def _reset_sequences(connection: Connection, tables: list[Table]) -> None:
    """Explicit primary keys must not collide with PostgreSQL's next SERIAL ID."""
    preparer = connection.dialect.identifier_preparer
    for table in tables:
        if table.name in TRANSIENT_TABLES:
            continue
        for column in table.primary_key.columns:
            if not isinstance(column.type, Integer) or column.autoincrement is False:
                continue
            sequence = connection.execute(
                text(
                    "SELECT namespace.nspname, sequence.relname "
                    "FROM pg_class AS sequence "
                    "JOIN pg_namespace AS namespace ON namespace.oid = sequence.relnamespace "
                    "WHERE sequence.oid = "
                    "CAST(pg_get_serial_sequence(:table_name, :column_name) AS regclass)"
                ),
                {"table_name": preparer.format_table(table), "column_name": column.name},
            ).first()
            if sequence is None:
                continue
            maximum = connection.scalar(select(func.max(column)))
            next_id = max(int(maximum or 0) + 1, 1)
            schema_name, sequence_name = sequence
            quoted = f"{preparer.quote_schema(schema_name)}.{preparer.quote(sequence_name)}"
            # Unlike setval(), RESTART is transactional: a later failure rolls
            # back sequence changes together with the copied rows.
            connection.exec_driver_sql(
                f"ALTER SEQUENCE {quoted} RESTART WITH {next_id}"
            )


def transfer(
    source_engine: Engine, destination_engine: Engine, metadata: MetaData,
    *, execute: bool = False, batch_size: int = 500,
) -> dict[str, Any]:
    if source_engine.dialect.name != "sqlite":
        raise TransferError("El origen debe ser SQLite.")
    if destination_engine.dialect.name != "postgresql":
        raise TransferError("DATABASE_URL debe apuntar al PostgreSQL de destino.")
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise TransferError("El tamaño de lote debe estar entre 1 y 1000.")
    tables = list(metadata.sorted_tables)
    if not tables:
        raise TransferError("No se encontraron las tablas de la aplicación.")

    with source_engine.connect() as source, destination_engine.begin() as destination:
        # Python's sqlite3 legacy mode does not BEGIN on SELECT. This explicit
        # transaction gives counts and streamed rows the same consistent snapshot.
        source.exec_driver_sql("BEGIN")
        try:
            _validate_schema(source, tables, source=True)
            _validate_schema(destination, tables, source=False)
            _assert_empty(destination, tables)
            _validate_text_lengths(source, tables)
            if execute:
                destination.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                destination.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
                quoted = ", ".join(
                    destination.dialect.identifier_preparer.format_table(table)
                    for table in tables
                )
                destination.exec_driver_sql(
                    f"LOCK TABLE {quoted} IN SHARE ROW EXCLUSIVE MODE"
                )
                # Recheck after the lock: another connection could have inserted
                # rows between the initial validation and acquiring every lock.
                _assert_empty(destination, tables)
            counts = _source_counts(source, tables)
            if execute:
                copied = _copy_rows(source, destination, tables, batch_size)
                if copied != counts or _source_counts(destination, tables) != counts:
                    raise TransferError("La verificación de recuentos falló; se canceló la copia.")
                _reset_sequences(destination, tables)
            return {
                "mode": "copied" if execute else "dry-run",
                "tables": counts,
                "total_rows": sum(counts.values()),
                "omitted_transient_tables": sorted(
                    table.name for table in tables if table.name in TRANSIENT_TABLES
                ),
            }
        finally:
            source.rollback()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="archivo SQLite existente")
    parser.add_argument("--execute", action="store_true", help="copiar tras validar (por defecto sólo informa)")
    parser.add_argument("--batch-size", type=int, default=500, help="filas por lote, entre 1 y 1000")
    args = parser.parse_args(argv)
    source_engine = None
    destination_engine = None
    try:
        # Lazy imports keep --help and helper tests independent of settings/.env.
        backend_root = str(Path(__file__).resolve().parents[1])
        if backend_root not in sys.path:
            sys.path.insert(0, backend_root)
        from app.core.config import settings
        from app.db.database import Base
        import app.models  # noqa: F401: registers every application table

        source_engine = readonly_sqlite_engine(args.source)
        destination_engine = create_engine(
            settings.DATABASE_URL, echo=False, hide_parameters=True, pool_pre_ping=True,
        )
        result = transfer(
            source_engine, destination_engine, Base.metadata,
            execute=args.execute, batch_size=args.batch_size,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not args.execute:
            print("Validación completada. Ejecutá nuevamente con --execute para copiar.")
        return 0
    except TransferError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # DB exceptions can embed the DSN or row parameters; never print them.
        print(
            "No se pudo completar la transferencia. Revisá la conexión, las migraciones "
            "y los permisos. La transacción de datos fue cancelada; el origen no cambió.",
            file=sys.stderr,
        )
        return 1
    finally:
        if source_engine is not None:
            source_engine.dispose()
        if destination_engine is not None:
            destination_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
