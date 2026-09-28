"""Tandas finitas para GitHub Actions: sin servidor ni conexión entre ejecuciones."""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import BASE_DIR, settings
from app.db.base import SessionLocal
from app.services.steam_catalog_worker import WorkerPresence, run_cycle
from app.workers.steam_catalog import configure_logging

logger = logging.getLogger(__name__)
STORAGE_MESSAGE = "Actualización pausada por el límite de almacenamiento configurado. Los juegos guardados siguen disponibles."
RETRY_MESSAGE = "No se pudo completar la tanda. Se conserva el progreso para la próxima ejecución."


def database_size_bytes(db) -> int:
    # Incluye tablas, índices y espacio físico: una medida conservadora.
    return int(db.scalar(text("SELECT pg_database_size(current_database())")))


def validate_connection(url: str) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("La tanda remota requiere PostgreSQL.")
    if parsed.host and parsed.host.endswith(".neon.tech"):
        if "-pooler" in parsed.host:
            raise ValueError("Usá la conexión directa de Neon, con pooling desactivado.")
        if parsed.query.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
            raise ValueError("La conexión de Neon debe usar TLS.")


def run_batch(*, seconds: int = 360, max_cycles: int = 60,
              max_db_mib: int = 400, session_factory=SessionLocal,
              stop_event: threading.Event | None = None) -> dict:
    if min(seconds, max_cycles, max_db_mib) <= 0:
        raise ValueError("Los límites deben ser positivos.")
    stop = stop_event or threading.Event()
    report = {"cycles": 0, "index_pages": 0, "details_attempted": 0,
              "details_ready": 0, "database_mib": 0.0, "stop_reason": "cycle_limit"}
    if stop.is_set():
        return {**report, "stop_reason": "interrupted"}
    timed_out = threading.Event()

    def expire():
        timed_out.set()
        stop.set()

    timer = threading.Timer(seconds, expire)
    timer.daemon = True
    presence = WorkerPresence(session_factory, "scheduled")
    presence.start()
    timer.start()
    try:
        for _ in range(max_cycles):
            if stop.is_set():
                break
            with session_factory() as db:
                size = database_size_bytes(db)
            report["database_mib"] = round(size / (1024 ** 2), 2)
            if size >= max_db_mib * 1024 ** 2:
                presence.last_error = STORAGE_MESSAGE
                report["stop_reason"] = "storage_limit"
                break
            result = run_cycle(session_factory=session_factory, stop_event=stop)
            index = result["index"] or {}
            details = result["details"] or {}
            report["cycles"] += 1
            report["index_pages"] += index.get("pages_this_run", 0)
            report["details_attempted"] += details.get("attempted", 0)
            report["details_ready"] += details.get("ready", 0)
            if (index.get("status") == "error" or details.get("deferred")
                    or details.get("retry_after_seconds")):
                presence.last_error = RETRY_MESSAGE
                report["stop_reason"] = "retry"
                break
            if index.get("busy") or details.get("busy"):
                report["stop_reason"] = "busy"
                break
            if not index.get("partial") and not details.get("attempted"):
                report["stop_reason"] = "idle"
                break
            stop.wait(settings.STEAM_CATALOG_REQUEST_DELAY_SECONDS)
        if stop.is_set():
            report["stop_reason"] = "time_budget" if timed_out.is_set() else "interrupted"
    except Exception:
        presence.last_error = RETRY_MESSAGE
        raise
    finally:
        timer.cancel()
        timer.join()
        presence.stop()
    return report


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("El límite debe ser positivo.")
    return number


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=positive_int, default=360)
    parser.add_argument("--max-cycles", type=positive_int, default=60)
    parser.add_argument("--max-db-mib", type=positive_int, default=400)
    parser.add_argument("--migrate", action="store_true", help="Aplicar Alembic antes de la tanda")
    args = parser.parse_args(argv)
    configure_logging()
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set())
                for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        validate_connection(settings.DATABASE_URL)
        if not settings.STEAM_API_KEY.strip():
            logger.error("Falta STEAM_API_KEY en los secretos del sincronizador.")
            return 1
        if args.migrate:
            from alembic import command
            from alembic.config import Config
            config = Config(str(BASE_DIR / "alembic.ini"))
            config.set_main_option("script_location", str(BASE_DIR / "alembic"))
            command.upgrade(config, "head")
        report = run_batch(seconds=args.seconds, max_cycles=args.max_cycles,
                           max_db_mib=args.max_db_mib, stop_event=stop)
        print(json.dumps(report, ensure_ascii=False))
        return 1 if report["stop_reason"] in {"retry", "storage_limit", "interrupted"} else 0
    except Exception as error:
        # Los errores SQL pueden incluir la URL o parámetros privados.
        logger.error("No se pudo ejecutar la tanda (%s). Revisá conexión directa, TLS y migraciones.", type(error).__name__)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
