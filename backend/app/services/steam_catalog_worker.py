"""Mantenimiento local por tandas, con cola durable y pausas interrumpibles.

Se ejecuta mientras la API está abierta. El lock de catálogo compartido con el
importador evita procesar dos tandas simultáneamente desde procesos distintos.
No hay llamadas a Steam desde el endpoint de estado ni desde el buscador.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.core.config import settings
from app.db.base import SessionLocal
from app.models import Game, SteamCatalogEntry, SteamCatalogSync
from app.services import steam_service
from app.services.steam_catalog_service import catalog_lock, sync_catalog

logger = logging.getLogger(__name__)
_thread: threading.Thread | None = None
_stop = threading.Event()
_heartbeat: datetime | None = None
_last_error: str | None = None
_index_retry_at = 0.0
_details_retry_at = 0.0


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def worker_status() -> dict:
    return {"worker_enabled": settings.STEAM_CATALOG_WORKER_ENABLED,
            "worker_running": bool(_thread and _thread.is_alive()),
            "worker_heartbeat": _heartbeat, "worker_error": _last_error,
            "interval_minutes": settings.STEAM_CATALOG_INTERVAL_MINUTES}


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


def _run() -> None:
    global _heartbeat, _last_error
    while not _stop.is_set():
        try:
            run_cycle(stop_event=_stop)
            _last_error = None
        except Exception as error:
            _last_error = "La sincronización se reintentará; los datos guardados siguen disponibles."
            logger.warning("Sincronización pospuesta (%s)", type(error).__name__)
        _heartbeat = datetime.now(timezone.utc)
        _stop.wait(max(10.0, settings.STEAM_CATALOG_REQUEST_DELAY_SECONDS))


def start_worker() -> None:
    global _thread
    if not settings.STEAM_CATALOG_WORKER_ENABLED or (_thread and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, name="steam-catalog", daemon=True)
    _thread.start()


def stop_worker() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=2)
