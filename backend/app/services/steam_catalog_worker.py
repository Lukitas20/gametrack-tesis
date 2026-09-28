"""Mantenimiento por tandas, embebido o independiente de la API.

La cola, el checkpoint y la presencia del worker residen en la misma base.
No hay llamadas a Steam desde el endpoint de estado ni desde el buscador.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, or_, select, update

from app.core.config import settings
from app.db.base import SessionLocal
from app.models import Game, SteamCatalogEntry, SteamCatalogSync, SteamCatalogWorker
from app.services import steam_service
from app.services.steam_catalog_service import catalog_lock, sync_catalog

logger = logging.getLogger(__name__)
_thread: threading.Thread | None = None
_stop = threading.Event()
_index_retry_at = 0.0
_details_retry_at = 0.0
HEARTBEAT_SECONDS = 15
HEARTBEAT_TTL_SECONDS = 90
RETRY_MESSAGE = "La sincronización se reintentará; los datos guardados siguen disponibles."


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def worker_status(db, *, now: datetime | None = None) -> dict:
    """Estado visible desde cualquier API que use la base compartida."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=HEARTBEAT_TTL_SECONDS)
    active_filter = (SteamCatalogWorker.stopped_at.is_(None),
                     SteamCatalogWorker.heartbeat_at >= cutoff)
    latest = db.scalar(select(SteamCatalogWorker).where(*active_filter)
                       .order_by(SteamCatalogWorker.heartbeat_at.desc()).limit(1))
    active = latest is not None
    remote_key = bool(db.scalar(select(SteamCatalogWorker.id).where(
        *active_filter, SteamCatalogWorker.key_configured.is_(True)).limit(1)))
    if latest is None:
        latest = db.scalar(select(SteamCatalogWorker)
                           .order_by(SteamCatalogWorker.heartbeat_at.desc()).limit(1))
    scheduled = latest is not None and latest.mode == "scheduled"
    return {"worker_mode": "scheduled" if scheduled else settings.STEAM_CATALOG_WORKER_MODE,
            "worker_enabled": settings.STEAM_CATALOG_WORKER_ENABLED or active,
            "worker_running": active,
            "worker_heartbeat": latest.heartbeat_at if latest else None,
            "worker_finished_at": latest.stopped_at if latest else None,
            "worker_error": latest.last_error if latest else None,
            "interval_minutes": latest.interval_minutes if active or scheduled else settings.STEAM_CATALOG_INTERVAL_MINUTES,
            # Entre tandas no hay un proceso vivo. Conservar la configuración
            # informada por la última ejecución, sin afirmar que sigue activo.
            "key_configured": bool(settings.STEAM_API_KEY) or remote_key or bool(scheduled and latest.key_configured)}


class WorkerPresence:
    """Publica presencia durante llamadas lentas sin compartir sesiones SQL."""

    def __init__(self, session_factory, mode: str):
        self.session_factory = session_factory
        self.mode = mode
        self.worker_id = str(uuid4())
        self.last_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def publish(self, *, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        with self.session_factory() as db:
            row = db.get(SteamCatalogWorker, self.worker_id)
            if row is None:
                row = SteamCatalogWorker(id=self.worker_id, mode=self.mode)
                db.add(row)
            row.heartbeat_at = now
            row.stopped_at = None
            row.key_configured = bool(settings.STEAM_API_KEY)
            row.interval_minutes = settings.STEAM_CATALOG_INTERVAL_MINUTES
            row.last_error = self.last_error
            # La limpieza tiene un límite fijo, también tras reinicios abruptos.
            obsolete = select(SteamCatalogWorker.id).where(
                SteamCatalogWorker.heartbeat_at < now - timedelta(days=1),
                SteamCatalogWorker.id != self.worker_id).limit(100)
            db.execute(delete(SteamCatalogWorker).where(SteamCatalogWorker.id.in_(obsolete)))
            db.commit()

    def _publish_loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                self.publish()
            except Exception as error:
                # No incluir URLs de conexión ni credenciales en los logs.
                logger.warning("No se pudo actualizar el estado del worker (%s)", type(error).__name__)

    def start(self) -> None:
        self.publish()
        self._thread = threading.Thread(target=self._publish_loop, name="steam-presence", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
        with self.session_factory() as db:
            db.execute(update(SteamCatalogWorker).where(SteamCatalogWorker.id == self.worker_id)
                       .values(stopped_at=datetime.now(timezone.utc), last_error=self.last_error))
            db.commit()


def _register_existing(db, limit: int, now: datetime) -> None:
    """Las bases anteriores pueden tener fichas de Steam sin fila de cola."""
    rows = db.execute(select(Game.steam_app_id, Game.steam_synced_at)
        .outerjoin(SteamCatalogEntry, SteamCatalogEntry.appid == Game.steam_app_id)
        .where(Game.steam_app_id.is_not(None), SteamCatalogEntry.appid.is_(None))
        .order_by(Game.id).limit(limit)).all()
    for appid, synced in rows:
        db.add(SteamCatalogEntry(appid=appid, last_seen_at=now,
            status="ready" if synced else "pending",
            next_attempt_at=utc(synced) + timedelta(minutes=settings.STEAM_SYNC_TTL_MINUTES)
                if synced else now))
    if rows:
        db.commit()


def process_details(db, *, limit: int | None = None,
                    stop_event: threading.Event | None = None) -> dict:
    global _details_retry_at
    limit = limit or settings.STEAM_CATALOG_DETAIL_BATCH_SIZE
    stop = stop_event or threading.Event()
    result = {"attempted": 0, "ready": 0, "deferred": 0, "non_game": 0, "busy": False}
    if time.monotonic() < _details_retry_at:
        result["retry_after_seconds"] = int(_details_retry_at - time.monotonic()) + 1
        return result
    with catalog_lock(db) as acquired:
        if not acquired:
            result["busy"] = True
            return result
        now = datetime.now(timezone.utc)
        _register_existing(db, limit, now)
        # Sólo IDs: no materializar el catálogo ni sus géneros y reseñas.
        ids = list(db.scalars(select(Game.id)
            .join(SteamCatalogEntry, SteamCatalogEntry.appid == Game.steam_app_id)
            .where(SteamCatalogEntry.status != "non_game",
                   SteamCatalogEntry.priority > 0 if settings.STEAM_CATALOG_REQUESTED_ONLY else True,
                   or_(SteamCatalogEntry.next_attempt_at.is_(None),
                       SteamCatalogEntry.next_attempt_at <= now))
            .order_by(SteamCatalogEntry.priority.desc(),
                      SteamCatalogEntry.next_attempt_at.asc().nulls_first(), Game.id)
            .limit(limit)))
        for game_id in ids:
            if stop.is_set():
                break
            game = db.get(Game, game_id)
            if game is None:
                continue
            appid = game.steam_app_id
            result["attempted"] += 1
            remote_failed = False
            try:
                steam_service.refresh_game(db, game)
            except steam_service.SteamUnavailable as error:
                # refresh_game conserva la última ficha y programa el reintento.
                _details_retry_at = time.monotonic() + max(60, getattr(error, "retry_after_seconds", 60))
                remote_failed = True
            except Exception as error:
                db.rollback()
                entry = db.get(SteamCatalogEntry, appid)
                if entry:
                    entry.attempts += 1
                    entry.last_attempt_at = datetime.now(timezone.utc)
                    entry.next_attempt_at = entry.last_attempt_at + timedelta(minutes=30)
                    entry.last_error = "No se pudo guardar la ficha; se reintentará."
                    db.commit()
                logger.warning("Ficha pospuesta (%s)", type(error).__name__)
            entry = db.get(SteamCatalogEntry, appid)
            outcome = entry.status if entry else "unavailable"
            result[outcome if outcome in {"ready", "non_game"} else "deferred"] += 1
            if remote_failed:
                break  # Una caída o límite afecta al servicio, no sólo a este AppID.
            if stop.wait(settings.STEAM_CATALOG_REQUEST_DELAY_SECONDS):
                break
    return result


def run_cycle(*, session_factory=SessionLocal, stop_event=None,
              include_index: bool = True, detail_limit: int | None = None) -> dict:
    """Una página de índice y una tanda de fichas; permite alternar ambas tareas."""
    global _index_retry_at
    result = {"index": None, "details": None}
    if stop_event is not None and stop_event.is_set():
        return result
    with session_factory() as db:
        state = db.get(SteamCatalogSync, 1)
        due = state is None or state.scan_started_at is not None or state.completed_at is None
        if not due:
            due = utc(state.completed_at) + timedelta(minutes=settings.STEAM_CATALOG_INTERVAL_MINUTES) <= datetime.now(timezone.utc)
        if include_index and settings.STEAM_API_KEY and due and time.monotonic() >= _index_retry_at:
            result["index"] = sync_catalog(db, max_pages=1,
                page_size=settings.STEAM_CATALOG_PAGE_SIZE,
                delay=settings.STEAM_CATALOG_REQUEST_DELAY_SECONDS)
            if result["index"].get("status") == "error":
                _index_retry_at = time.monotonic() + max(60, result["index"].get("retry_after_seconds") or 60)
        db.rollback()  # soltar cualquier snapshot antes de la tanda de detalles
        if not stop_event or not stop_event.is_set():
            result["details"] = process_details(db, limit=detail_limit, stop_event=stop_event)
    return result


def run_forever(*, stop_event: threading.Event | None = None,
                session_factory=SessionLocal, mode: str = "external") -> None:
    """Loop compartido por el servidor local y el comando independiente."""
    stop = stop_event or threading.Event()
    presence = WorkerPresence(session_factory, mode)
    presence.start()
    try:
        while not stop.is_set():
            try:
                run_cycle(session_factory=session_factory, stop_event=stop)
                presence.last_error = None
            except Exception as error:
                presence.last_error = RETRY_MESSAGE
                logger.warning("Sincronización pospuesta (%s)", type(error).__name__)
            stop.wait(max(10.0, settings.STEAM_CATALOG_REQUEST_DELAY_SECONDS))
    finally:
        try:
            presence.stop()
        except Exception as error:
            # Si la base está caída, la presencia caduca automáticamente.
            logger.warning("No se pudo cerrar el estado del worker (%s)", type(error).__name__)


def _run() -> None:
    try:
        run_forever(stop_event=_stop, mode="embedded")
    except Exception as error:
        logger.warning("No se pudo iniciar el worker (%s)", type(error).__name__)


def start_worker() -> None:
    global _thread
    if (not settings.STEAM_CATALOG_WORKER_ENABLED
            or settings.STEAM_CATALOG_WORKER_MODE != "embedded"
            or (_thread and _thread.is_alive())):
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="steam-catalog", daemon=True)
    _thread.start()


def stop_worker() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=2)
