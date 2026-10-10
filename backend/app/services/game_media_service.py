"""Capturas de una ficha abierta, sin esperar al sincronizador del catálogo."""

from datetime import datetime, timezone
from threading import BoundedSemaphore, Lock

import httpx
from sqlalchemy.orm import Session

from app.models import Game
from app.services import steam_service
from app.services.steam_media import extract_screenshots, sanitize_screenshots


# Memoria acotada y ninguna espera: una galería nunca forma una cola de red.
# Las capturas y el backoff sobreviven a reinicios en la base de datos.
_MEDIA_LOCKS = tuple(Lock() for _ in range(32))
_MEDIA_SLOTS = BoundedSemaphore(2)
_MEDIA_TIMEOUT = httpx.Timeout(4.0, connect=2.0, pool=1.0)


def _cached(game: Game) -> dict | None:
    images = sanitize_screenshots(game.screenshots)
    if images:
        return {"screenshots": images, "status": "available"}
    if game.screenshots == [] or game.steam_app_id is None:
        return {"screenshots": [], "status": "empty"}
    return None


def _pending() -> dict:
    return {"screenshots": [], "status": "pending"}


def get_game_media(db: Session, game: Game) -> dict:
    """Consulta como máximo una ficha de Steam y guarda sólo capturas reales.

    No importa reseñas ni cambia las categorías, el nombre o la fecha de
    sincronización. Una respuesta válida sin capturas se cachea como [];
    fallos temporales conservan None y el backoff de la cola del catálogo.
    """
    cached = _cached(game)
    if cached is not None:
        return cached
    appid = game.steam_app_id
    lock = _MEDIA_LOCKS[appid % len(_MEDIA_LOCKS)]
    if not lock.acquire(blocking=False):
        return _pending()
    slot = False
    try:
        # Otra visita pudo completar esta ficha mientras se preparaba el GET.
        db.refresh(game, attribute_names=["screenshots"])
        cached = _cached(game)
        if cached is not None:
            return cached
        entry = steam_service._catalog_entry(db, game)
        db.flush()
        db.refresh(entry)
        now = datetime.now(timezone.utc)
        retry_at = entry.next_attempt_at
        if retry_at is not None and retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        if entry.status == "non_game":
            return {"screenshots": [], "status": "empty"}
        if entry.status == "unavailable" and retry_at is not None and retry_at > now:
            return _pending()
        slot = _MEDIA_SLOTS.acquire(blocking=False)
        if not slot:
            return _pending()
        # Liberar la conexión durante Steam respeta el pool pequeño de Cloud.
        db.commit()
        try:
            data = steam_service.get_app_details(appid, timeout=_MEDIA_TIMEOUT)
            if data is None or data.get("type") != "game":
                raise steam_service.SteamUnavailable("Las capturas no están disponibles públicamente")
            if data.get("screenshots") is not None and not isinstance(data["screenshots"], list):
                raise steam_service.SteamUnavailable("Steam devolvió capturas con formato inválido")
        except steam_service.SteamUnavailable as error:
            steam_service._record_unavailable(
                db, entry, "No se pudieron obtener capturas de Steam", error.retry_after_seconds
            )
            return {"screenshots": [], "status": "unavailable"}
        images = extract_screenshots(data)
        game.screenshots = images
        db.commit()
        return {"screenshots": images, "status": "available" if images else "empty"}
    finally:
        if slot:
            _MEDIA_SLOTS.release()
        lock.release()
