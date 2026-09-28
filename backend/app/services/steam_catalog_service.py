"""Índice oficial paginado: una página y su cursor se confirman juntos.

Las fichas se completan con otro worker. Nunca se borra un juego por no
aparecer en una página, una sincronización parcial o un error de Steam.
"""

from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unicodedata

import httpx
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Game, SteamCatalogEntry, SteamCatalogSync

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
OVERLAP_SECONDS = 300
# PostgreSQL scopes advisory locks to the database; never derive this key from
# a hostname or credentials, which can differ between workers on the same DB.
_POSTGRES_LOCK_ID = int.from_bytes(
    hashlib.sha256(b"gametrack:steam-catalog").digest()[:8], "big", signed=True
)


class CatalogError(Exception):
    """Mensaje seguro para UI: jamás incluye URL, clave ni cuerpo remoto."""

    def __init__(self, message: str, retry_after_seconds: int = 60):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@contextmanager
def _postgres_catalog_lock(bind):
    """Keep one dedicated server session through the importer's commits.

    Use a direct PostgreSQL connection or a session-mode pooler. A transaction
    pooler cannot preserve a session advisory lock across transactions.
    """
    connection = None
    try:
        connection = bind.engine.connect()
        # The lock is session scoped; avoid an idle transaction while Steam is
        # being called, without returning the physical connection to the pool.
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        acquired = bool(connection.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": _POSTGRES_LOCK_ID},
        ))
    except SQLAlchemyError:
        if connection is not None:
            # Acquisition may have reached PostgreSQL before a network error.
            # Never put a possibly locked session back in the connection pool.
            try:
                connection.invalidate()
            finally:
                connection.close()
        raise CatalogError("No se pudo obtener el bloqueo del catálogo en PostgreSQL.") from None

    try:
        yield acquired
    finally:
        try:
            if acquired:
                try:
                    released = connection.scalar(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": _POSTGRES_LOCK_ID},
                    )
                    if not released:
                        connection.invalidate()
                except SQLAlchemyError:
                    # Closing the physical session releases its locks, even
                    # when an explicit unlock cannot be acknowledged.
                    connection.invalidate()
        finally:
            connection.close()


@contextmanager
def catalog_lock(db: Session):
    """Exclusión no bloqueante entre worker y CLI, también entre máquinas.

    PostgreSQL mantiene el bloqueo en una sesión dedicada. SQLite usa un
    archivo que sólo identifica la base mediante un hash. Al cerrar el proceso
    se libera el bloqueo, sin eliminar checkpoints.
    """
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        with _postgres_catalog_lock(bind) as acquired:
            yield acquired
        return
    url = bind.url
    identity = str(url)
    if url.get_backend_name() == "sqlite":
        identity = str(Path(url.database).resolve()) if url.database and url.database != ":memory:" else f"memory:{id(bind)}"
    key = hashlib.sha256(identity.encode()).hexdigest()[:24]
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        yield False
        return
    handle = None
    acquired = False
    try:
        handle = open(Path(tempfile.gettempdir()) / f"gametrack-steam-{key}.lock", "a+b")
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        yield acquired
    finally:
        if handle is not None:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        lock.release()


def _ensure_sync(db: Session) -> None:
    dialect = db.get_bind().dialect.name
    if dialect in ("sqlite", "postgresql"):
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        db.execute(insert(SteamCatalogSync).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
    elif db.get(SteamCatalogSync, 1) is None:
        try:
            with db.begin_nested():
                db.add(SteamCatalogSync(id=1))
                db.flush()
        except IntegrityError:
            pass


def bump_catalog_revision(db: Session) -> None:
    """Incremento atómico; el llamador confirma junto con la ficha modificada."""
    _ensure_sync(db)
    db.execute(update(SteamCatalogSync).where(SteamCatalogSync.id == 1).values(
        revision=SteamCatalogSync.revision + 1
    ).execution_options(synchronize_session=False))


def get_catalog_status(db: Session, *, key_configured: bool | None = None) -> dict:
    """Lectura acotada; el worker remoto puede informar si tiene clave."""
    row = db.execute(select(SteamCatalogSync.__table__).where(SteamCatalogSync.id == 1)).mappings().first()
    result = dict(row) if row else {
        "id": 1, "revision": 0, "status": "idle", "cursor_appid": 0,
        "since": 0, "scan_started_at": None, "completed_at": None,
        "last_error": None, "processed": 0, "created": 0, "updated": 0,
    }
    result["key_configured"] = (
        bool(settings.STEAM_API_KEY.strip()) if key_configured is None else key_configured
    )
    if not result["key_configured"]:
        result["status"] = "disabled"
        result["last_error"] = "Configurá STEAM_API_KEY en el entorno del sincronizador para actualizar el índice oficial."
    result["partial"] = result["scan_started_at"] is not None
    result["entry_counts"] = dict(db.execute(select(
        SteamCatalogEntry.status, func.count()
    ).group_by(SteamCatalogEntry.status)).all())
    non_game = select(SteamCatalogEntry.appid).where(
        SteamCatalogEntry.appid == Game.steam_app_id, SteamCatalogEntry.status == "non_game"
    ).exists()
    result["total_games"] = db.scalar(select(func.count(Game.id)).where(~non_game)) or 0
    for field in ("scan_started_at", "completed_at"):
        if result[field] is not None:
            result[field] = _utc(result[field])
    return result


def _uint(value, *, positive=False) -> bool:
    return type(value) is int and (1 if positive else 0) <= value <= 2**32 - 1


def _fetch_page(client, *, cursor: int, since: int, page_size: int) -> tuple[list[dict], bool, int]:
    params = {
        "key": settings.STEAM_API_KEY,
        "input_json": json.dumps({
            "include_games": True, "include_dlc": False, "include_software": False,
            "include_videos": False, "include_hardware": False,
            "last_appid": cursor, "if_modified_since": since, "max_results": page_size,
        }),
    }
    try:
        response = client.get(f"{settings.STEAM_API_BASE.rstrip('/')}/IStoreService/GetAppList/v1/", params=params)
    except httpx.HTTPError:
        raise CatalogError("No se pudo conectar con Steam. Se conserva el progreso para reintentar.") from None
    if response.status_code in (401, 403):
        raise CatalogError("Steam rechazó la clave. Revisá STEAM_API_KEY y sus permisos.", 3600)
    if response.status_code == 429:
        retry = response.headers.get("Retry-After", "60")
        retry = min(86400, max(60, int(retry))) if retry.isdecimal() else 60
        raise CatalogError("Steam limitó temporalmente las solicitudes. Se conserva el progreso.", retry)
    if response.status_code != 200:
        raise CatalogError(f"Steam respondió con HTTP {response.status_code}. Se conserva el progreso.")
    try:
        payload = response.json()
    except (ValueError, UnicodeError):
        raise CatalogError("Steam devolvió una respuesta que no es JSON válido.") from None
    data = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("apps"), list):
        raise CatalogError("Steam devolvió un índice con estructura inesperada.")
    apps = data["apps"]
    if len(apps) > page_size:
        raise CatalogError("Steam devolvió una página mayor que la solicitada.")
    previous = cursor
    for app in apps:
        if (not isinstance(app, dict) or not _uint(app.get("appid"), positive=True)
                or app["appid"] <= previous or not isinstance(app.get("name"), str)
                or not _uint(app.get("last_modified", 0))):
            raise CatalogError("Steam devolvió entradas inválidas o un cursor que no avanza.")
        previous = app["appid"]
    more = data.get("have_more_results", len(apps) == page_size)
    reported_cursor = data.get("last_appid", previous)
    if (type(more) is not bool or not _uint(reported_cursor)
            or reported_cursor != previous or (more and not apps)):
        raise CatalogError("Steam devolvió una continuación inválida; se conserva la página anterior.")
    return apps, more, previous


def _slug(name: str, appid: int, counter: int = 0) -> str:
    stem = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "steam"
    suffix = f"-steam-{appid}" + (f"-{counter}" if counter else "")
    return stem[:120 - len(suffix)].rstrip("-") + suffix


def _new_slug(db: Session, name: str, appid: int, reserved: set[str]) -> str:
    candidate = _slug(name, appid)
    counter = 0
    while candidate in reserved:
        counter += 1
        candidate = _slug(name, appid, counter)
        if db.scalar(select(Game.id).where(Game.slug == candidate)) is not None:
            reserved.add(candidate)
    reserved.add(candidate)
    return candidate


def _apply_page(db: Session, apps: list[dict], seen_at: datetime) -> tuple[int, int]:
    created = updated = 0
    revision_changed = False
    # Incluso con páginas de 50k, las consultas IN y las escrituras se
    # mantienen pequeñas. El COMMIT sigue siendo único para toda la página.
    for offset in range(0, len(apps), 400):
        new, changed, affects_recommendations = _apply_chunk(db, apps[offset:offset + 400], seen_at)
        created += new
        updated += changed
        revision_changed |= affects_recommendations
    if revision_changed:
        bump_catalog_revision(db)
    return created, updated


def _apply_chunk(db: Session, apps: list[dict], seen_at: datetime) -> tuple[int, int, bool]:
    # La API real incluye AppIDs sin nombre público. No crear un título
    # inventado ni borrar un nombre local; el cursor sí avanza sobre ellos.
    # Si Steam les asigna un nombre, entrarán en una próxima pasada de cambios.
    apps = [app for app in apps if app["name"].strip()]
    ids = [app["appid"] for app in apps]
    # Selecciona únicamente columnas del lote: sin eager-load de géneros/tags.
    games = {row.steam_app_id: row for row in db.execute(select(
        Game.id, Game.steam_app_id, Game.name, Game.steam_synced_at
    ).where(Game.steam_app_id.in_(ids)))} if ids else {}
    entries = {entry.appid: entry for entry in db.scalars(select(
        SteamCatalogEntry
    ).where(SteamCatalogEntry.appid.in_(ids)))} if ids else {}
    created = updated = 0
    revision_changed = False
    candidates = [_slug(app["name"].strip()[:200], app["appid"]) for app in apps if app["appid"] not in games]
    reserved = set(db.scalars(select(Game.slug).where(Game.slug.in_(candidates)))) if candidates else set()
    new_games: list[dict] = []
    for app in apps:
        appid, name = app["appid"], app["name"].strip()[:200]
        source_modified = app.get("last_modified", 0)
        game = games.get(appid)
        changed = False
        if game is None:
            new_games.append({"steam_app_id": appid, "name": name, "slug": _new_slug(db, name, appid, reserved)})
            created += 1
        elif game.name != name:
            db.execute(update(Game).where(Game.id == game.id).values(name=name))
            changed = True
            revision_changed |= game.steam_synced_at is not None
        entry = entries.get(appid)
        if entry is None:
            db.add(SteamCatalogEntry(
                appid=appid, source_modified=source_modified, last_seen_at=seen_at,
                status="ready" if game is not None and game.steam_synced_at is not None else "pending",
            ))
        else:
            entry.last_seen_at = seen_at
            if source_modified > entry.source_modified:
                entry.source_modified = source_modified
                entry.status = "pending"
                entry.next_attempt_at = None
                entry.last_error = None
                changed = True
        if game is not None and changed:
            updated += 1
    if new_games:
        db.execute(insert(Game), new_games)
    return created, updated, revision_changed


def sync_catalog(db: Session, *, max_pages: int | None = None, now: datetime | None = None,
                 page_size: int | None = None, delay: float = 1.0, client=None) -> dict:
    """Sincroniza hasta terminar o alcanzar max_pages; errores son reanudables.

    ``since`` sólo avanza al confirmar la última página. Si el proceso termina,
    el siguiente arranque continúa con el mismo corte temporal y AppID.
    Un límite de páginas deja ``partial=True``, nunca declara éxito completo.
    """
    page_size = page_size or getattr(settings, "STEAM_CATALOG_PAGE_SIZE", 1000)
    if not 1 <= page_size <= 50000 or (max_pages is not None and max_pages < 1) or delay < 0:
        raise ValueError("page_size debe ser 1..50000, max_pages positivo y delay no negativo")
    with catalog_lock(db) as acquired:
        if not acquired:
            return {**get_catalog_status(db), "busy": True, "pages_this_run": 0}
        _ensure_sync(db)
        state = db.get(SteamCatalogSync, 1, populate_existing=True)
        if not settings.STEAM_API_KEY.strip():
            state.status = "disabled"
            state.last_error = "Configurá STEAM_API_KEY en el entorno del sincronizador para actualizar el índice oficial."
            db.commit()
            return {**get_catalog_status(db), "pages_this_run": 0}
        if state.scan_started_at is None:
            state.scan_started_at = _utc(now)
            state.cursor_appid = 0
            state.processed = state.created = state.updated = 0
        state.status, state.last_error = "running", None
        db.commit()
        pages = 0
        try:
            with nullcontext(client) if client is not None else httpx.Client(timeout=30, follow_redirects=False) as http:
                while max_pages is None or pages < max_pages:
                    apps, more, cursor = _fetch_page(http, cursor=state.cursor_appid, since=state.since, page_size=page_size)
                    created, updated = _apply_page(db, apps, _utc(now))
                    state.cursor_appid = cursor
                    state.processed += len(apps)
                    state.created += created
                    state.updated += updated
                    if not more:
                        state.since = max(0, int(_utc(state.scan_started_at).timestamp()) - OVERLAP_SECONDS)
                        state.completed_at = _utc(now)
                        state.scan_started_at = None
                        state.status = "idle"
                        state.cursor_appid = 0
                    # El lote entero, revisión, contadores y cursor son una transacción.
                    db.commit()
                    pages += 1
                    if not more:
                        break
                    if delay and (max_pages is None or pages < max_pages):
                        time.sleep(delay)
        except CatalogError as exc:
            db.rollback()
            state = db.get(SteamCatalogSync, 1, populate_existing=True)
            state.status, state.last_error = "error", str(exc)
            db.commit()
            return {**get_catalog_status(db), "pages_this_run": pages, "retry_after_seconds": exc.retry_after_seconds}
        return {**get_catalog_status(db), "pages_this_run": pages}
