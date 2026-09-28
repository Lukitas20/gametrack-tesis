"""Ejecutar con ``python -m app.workers.steam_catalog`` desde backend/."""

import truststore

# Igual que la API y los importadores: respeta el almacén TLS del sistema.
truststore.inject_into_ssl()

import logging
import signal
import threading

from sqlalchemy import select

from app.core.config import settings
from app.db.base import SessionLocal, init_db
from app.models import SteamCatalogWorker
from app.services.steam_catalog_worker import run_forever

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # HTTPX incluye la URL completa en INFO; Steam lleva la clave en su query.
    # Se conservan nuestros mensajes operativos sin publicar credenciales.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main() -> int:
    configure_logging()
    if not settings.STEAM_CATALOG_WORKER_ENABLED:
        logger.info("Worker de Steam deshabilitado por configuración.")
        return 0
    stop = threading.Event()

    def request_stop(_signum, _frame):
        stop.set()

    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        if settings.DB_AUTO_CREATE:
            init_db()
        else:
            # El despliegue aplica Alembic antes de iniciar API y worker.
            with SessionLocal() as db:
                db.execute(select(SteamCatalogWorker.id).limit(1))
        logger.info("Worker de Steam iniciado; usa el catálogo y la cola de la base configurada.")
        run_forever(stop_event=stop, mode="external")
        return 0
    except Exception as error:
        logger.error("No se pudo ejecutar el worker (%s). Revisá la conexión y las migraciones.", type(error).__name__)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
