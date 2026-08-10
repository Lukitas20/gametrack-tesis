"""Ingesta del catálogo desde SteamSpy, en dos niveles.

**Nivel 0 — índice** (``ingest_index_entries``): a partir del endpoint masivo
de SteamSpy (``request=all``, 1000 juegos por pedido) crea las fichas
pendientes que falten, actualiza la señal de calidad de TODO el catálogo
(positivos/negativos/dueños, que el endpoint masivo ya trae) y encola cada
AppID para el nivel 1, priorizado por dueños estimados.

**Nivel 1 — rasgos** (``enrich_next_batch``): consume la cola de a lotes
contra ``request=appdetails`` (≈1 pedido/segundo) y le da a cada juego lo
mínimo que necesita para participar de una recomendación: etiquetas
comunitarias con votos, género y la señal de calidad actualizada. No trae
descripción, imágenes ni reseñas: eso sigue siendo trabajo de la ficha rica
de Steam (``steam_service``), que se completa sola al abrir el juego.

Dos invariantes que el worker preserva a propósito:

1. **No saber no es lo mismo que saber que no está** (el mismo de
   ``steam_service.SteamUnavailable``). Un fallo de transporte levanta
   ``SteamSpyUnavailable`` y deja la fila de la cola en ``pending`` con el
   intento anotado; NUNCA marca el juego como inexistente ni toca su fila en
   ``games``. Sólo una respuesta válida de SteamSpy sin datos (``name``
   vacío) marca ``skipped`` — y aun entonces el juego no se borra: decidir
   qué hacer con esas fichas es política de catálogo, no del worker.
2. **Cortar, no insistir**: ante ``max_consecutive_failures`` fallos de
   transporte seguidos el lote se aborta con lo procesado hasta ahí. Si
   SteamSpy se cayó, seguir es acumular timeouts durante horas sin nadie
   mirando; el estado queda en la base y la próxima corrida retoma.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Game, Genre, Tag, game_tags
from app.models.steamspy import SYNC_DONE, SYNC_PENDING, SYNC_SKIPPED, SteamSpySync
from app.services import steam_service

# Cadencia documentada por SteamSpy para el endpoint por juego.
DEFAULT_SLEEP_SECONDS = 1.0
DEFAULT_MAX_CONSECUTIVE_FAILURES = 5
INDEX_CHUNK_SIZE = 2000


class SteamSpyUnavailable(RuntimeError):
    """No se pudo hablar con SteamSpy (timeout, error de red, 5xx, no-JSON).

    Espejo de ``steam_service.SteamUnavailable``, y por la misma razón: es
    un servicio comunitario que bajo carga responde "Too many connections"
    en texto plano o directamente no responde. Confundir eso con "este
    AppID no tiene datos" marcaría media cola como ``skipped`` durante una
    caída — la versión a escala del bug que borró juegos del catálogo.
    """


def fetch_appdetails(steam_app_id: int) -> dict:
    """Ficha de SteamSpy para un juego. Siempre devuelve el JSON crudo.

    Levanta ``SteamSpyUnavailable`` si no hubo respuesta utilizable. La
    interpretación del contenido (¿tiene datos o es un AppID sin ficha?) es
    de quien llama, vía ``is_empty_entry``: separar transporte de semántica
    es lo que mantiene el invariante testeable.
    """
    try:
        response = steam_service._http_client().get(
            steam_service.STEAMSPY_BASE,
            params={"request": "appdetails", "appid": steam_app_id},
        )
    except httpx.HTTPError as error:
        raise SteamSpyUnavailable(f"No se pudo contactar a SteamSpy: {error}") from error

    if response.status_code != 200:
        raise SteamSpyUnavailable(f"SteamSpy respondió {response.status_code}")

    try:
        return response.json()
    except ValueError as error:  # "Too many connections" en texto plano, proxy...
        raise SteamSpyUnavailable("SteamSpy respondió algo que no es JSON") from error


def is_empty_entry(payload: dict) -> bool:
    """``True`` si SteamSpy respondió bien pero no tiene datos del AppID.

    Para un AppID sin ficha SteamSpy devuelve la estructura completa con
    ``name`` nulo y contadores en cero. Se exige TODO vacío, no sólo el
    nombre: mejor reintentar de más un caso raro que descartar un juego real
    por una respuesta parcial.
    """
    name = (payload.get("name") or "").strip()
    developer = (payload.get("developer") or "").strip()
    signal = (payload.get("positive") or 0) + (payload.get("negative") or 0)
    return not name and not developer and signal == 0


def parse_owners(raw: Any) -> int:
    """Punto medio de la banda de dueños ("100,000,000 .. 200,000,000")."""
    if isinstance(raw, (int, float)):
        return int(raw)
    if not raw:
        return 0
    parts = [p.strip().replace(",", "") for p in str(raw).split("..")]
    try:
        numbers = [int(p) for p in parts if p]
    except ValueError:
        return 0
    if not numbers:
        return 0
    return sum(numbers) // len(numbers)


# ---------------------------------------------------------------------------
# Nivel 0: índice masivo
# ---------------------------------------------------------------------------


@dataclass
class IndexReport:
    created: int = 0        # fichas pendientes nuevas en ``games``
    updated: int = 0        # juegos existentes con señal de SteamSpy refrescada
    queued: int = 0         # filas nuevas en la cola de nivel 1
    reprioritized: int = 0  # filas de la cola con prioridad/nombre refrescados
    invalid: int = 0        # entradas del índice sin AppID o sin nombre


def ingest_index_entries(
    db: Session, entries: Iterable[dict[str, Any]], limit: int | None = None
) -> IndexReport:
    """Vuelca una tanda del índice masivo de SteamSpy sobre la base.

    Idempotente y aditiva: correrla de nuevo actualiza señal y prioridades
    sin duplicar juegos ni filas de cola, y jamás borra nada. El ``limit``
    (para pruebas) acota cuántas fichas NUEVAS se crean; las entradas que ya
    existían se actualizan igual.

    Sustituye a ``scripts/import_steam_appindex.import_stub_catalog`` para el
    flujo nuevo: hace lo mismo con los stubs y además captura la señal de
    calidad que ese script tiraba y alimenta la cola de nivel 1.
    """
    report = IndexReport()

    games_by_appid: dict[int, Game] = {
        game.steam_app_id: game
        for game in db.scalars(select(Game).where(Game.steam_app_id.is_not(None)))
    }
    existing_slugs = {row[0] for row in db.execute(select(Game.slug))}
    queue_by_appid: dict[int, SteamSpySync] = {
        row.steam_app_id: row for row in db.scalars(select(SteamSpySync))
    }

    pending_flush = 0
    for entry in entries:
        appid = entry.get("appid")
        name = (entry.get("name") or "").strip()
        if not appid or not name:
            report.invalid += 1
            continue

        positive = entry.get("positive") or 0
        negative = entry.get("negative") or 0
        owners = parse_owners(entry.get("owners"))

        game = games_by_appid.get(appid)
        if game is None:
            if limit is not None and report.created >= limit:
                continue
            slug = steam_service.slugify(name)
            if slug in existing_slugs:
                slug = f"{slug}-{appid}"
            existing_slugs.add(slug)
            game = Game(
                steam_app_id=appid,
                slug=slug,
                name=name,
                developer=(entry.get("developer") or "").strip() or None,
                publisher=(entry.get("publisher") or "").strip() or None,
            )
            db.add(game)
            games_by_appid[appid] = game
            report.created += 1
        else:
            report.updated += 1

        game.steamspy_positive = positive
        game.steamspy_negative = negative
        game.steamspy_owners = owners

        queue_row = queue_by_appid.get(appid)
        if queue_row is None:
            queue_row = SteamSpySync(
                steam_app_id=appid, name=name, priority=owners, status=SYNC_PENDING
            )
            db.add(queue_row)
            queue_by_appid[appid] = queue_row
            report.queued += 1
        else:
            # La prioridad y el nombre se refrescan; el estado no: una fila
            # ``done`` sigue hecha, una ``pending`` con intentos los conserva.
            queue_row.priority = owners
            queue_row.name = name
            report.reprioritized += 1

        pending_flush += 1
        if pending_flush >= INDEX_CHUNK_SIZE:
            db.commit()
            pending_flush = 0

    db.commit()
    return report


# ---------------------------------------------------------------------------
# Nivel 1: rasgos por juego
# ---------------------------------------------------------------------------


@dataclass
class BatchReport:
    processed: int = 0    # filas de la cola tocadas en esta corrida
    enriched: int = 0     # juegos que recibieron etiquetas/género/señal
    skipped: int = 0      # AppIDs sin datos en SteamSpy (no son juegos)
    unavailable: int = 0  # fallos de transporte (quedaron en pending)
    aborted: bool = False  # el lote se cortó por fallos consecutivos
    errors: list[str] = field(default_factory=list)


def _get_or_create_community_tag(db: Session, name: str) -> Tag:
    """Como ``steam_service._get_or_create`` pero fijando ``kind``.

    Si la etiqueta ya existía como categoría de plataforma no se pisa su
    ``kind``: los slugs de un origen y otro no colisionan en la práctica
    (las categorías llegan en español, las comunitarias en inglés), y si
    alguna vez lo hicieran, "plataforma" es la clasificación conservadora.
    """
    slug = steam_service.slugify(name)
    existing = db.scalar(select(Tag).where(Tag.slug == slug))
    if existing:
        return existing
    created = Tag(slug=slug, name=name, kind="community")
    db.add(created)
    db.flush()
    return created


def apply_appdetails(db: Session, game: Game, payload: dict) -> None:
    """Vuelca la ficha de SteamSpy sobre un juego. Sólo agrega, nunca quita.

    Las etiquetas comunitarias se SUMAN a las existentes (las categorías de
    plataforma que haya traído la tienda siguen ahí: cumplen otro rol). Los
    géneros sólo se asignan si el juego no tenía: los de la ficha rica de
    Steam son más completos y no hay que pisarlos con menos información.
    """
    # SteamSpy devuelve ``"tags": []`` (lista vacía) cuando no hay votos, y
    # un dict nombre->votos cuando los hay. La lista vacía no es un error.
    raw_tags = payload.get("tags") or {}
    tags_with_votes: dict[str, int] = raw_tags if isinstance(raw_tags, dict) else {}

    current_slugs = {tag.slug for tag in game.tags}
    applied: list[tuple[int, int]] = []  # (tag_id, votos)
    for name, votes in tags_with_votes.items():
        tag = _get_or_create_community_tag(db, name)
        if tag.slug not in current_slugs:
            game.tags.append(tag)
            current_slugs.add(tag.slug)
        applied.append((tag.id, int(votes or 0)))

    if not game.genres:
        raw_genres = [g.strip() for g in (payload.get("genre") or "").split(",")]
        for raw_genre in raw_genres:
            if not raw_genre:
                continue
            translated = steam_service.GENRE_TRANSLATIONS.get(
                raw_genre.lower(), raw_genre
            )
            genre = steam_service._get_or_create(db, Genre, translated)
            if genre not in game.genres:
                game.genres.append(genre)

    game.steamspy_positive = payload.get("positive") or 0
    game.steamspy_negative = payload.get("negative") or 0
    game.steamspy_owners = parse_owners(payload.get("owners"))
    if not game.developer:
        game.developer = (payload.get("developer") or "").strip() or None
    if not game.publisher:
        game.publisher = (payload.get("publisher") or "").strip() or None

    # Los votos van con UPDATE explícito: la relación ``secondary`` maneja la
    # membresía (y deja votes=1 al insertar), esto escribe la evidencia.
    db.flush()
    for tag_id, votes in applied:
        db.execute(
            update(game_tags)
            .where(game_tags.c.game_id == game.id, game_tags.c.tag_id == tag_id)
            .values(votes=max(votes, 1))
        )


def enrich_next_batch(
    db: Session,
    limit: int = 50,
    *,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
    fetch: Callable[[int], dict] = fetch_appdetails,
) -> BatchReport:
    """Enriquece las próximas ``limit`` filas pendientes, por prioridad.

    Committea fila por fila: a 1 pedido/segundo el costo es despreciable y
    un corte en cualquier punto (Ctrl+C, caída, bloqueo) no pierde nada de
    lo ya procesado. ``sleeper`` y ``fetch`` son inyectables para que los
    tests corran sin red y sin esperas reales.
    """
    report = BatchReport()

    rows = list(
        db.scalars(
            select(SteamSpySync)
            .where(SteamSpySync.status == SYNC_PENDING)
            .order_by(
                SteamSpySync.priority.desc(),
                SteamSpySync.attempts.asc(),
                SteamSpySync.steam_app_id.asc(),
            )
            .limit(limit)
        )
    )

    consecutive_failures = 0
    for position, row in enumerate(rows):
        if position > 0 and sleep_seconds > 0:
            sleeper(sleep_seconds)

        report.processed += 1
        row.attempts += 1

        try:
            payload = fetch(row.steam_app_id)
        except SteamSpyUnavailable as error:
            # Invariante: fallo de transporte => la fila SIGUE pendiente y el
            # juego no se toca. Sólo queda anotado el intento y su motivo.
            row.last_error = str(error)
            report.unavailable += 1
            report.errors.append(f"{row.steam_app_id}: {error}")
            db.commit()

            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                report.aborted = True
                break
            continue

        consecutive_failures = 0
        row.raw = payload
        row.last_error = None
        row.synced_at = datetime.now(timezone.utc)

        if is_empty_entry(payload):
            # SteamSpy respondió, y respondió "de esto no tengo nada": no es
            # un juego (DLC, banda sonora, software). La fila se cierra como
            # ``skipped``; la ficha en ``games`` queda como esté — retirarla
            # del catálogo es una decisión de política, no de transporte.
            row.status = SYNC_SKIPPED
            report.skipped += 1
            db.commit()
            continue

        game = steam_service.get_game_by_steam_app_id(db, row.steam_app_id)
        if game is None:
            # La cola puede sobrevivir a una ficha borrada a mano: se recrea
            # el stub y se enriquece igual, que es lo que pediría el índice.
            name = (payload.get("name") or row.name).strip()
            slug = steam_service.slugify(name)
            if db.scalar(select(Game.id).where(Game.slug == slug)):
                slug = f"{slug}-{row.steam_app_id}"
            game = Game(steam_app_id=row.steam_app_id, slug=slug, name=name)
            db.add(game)
            db.flush()

        apply_appdetails(db, game, payload)
        row.status = SYNC_DONE
        report.enriched += 1
        db.commit()

    return report


def pending_count(db: Session) -> int:
    from sqlalchemy import func as sa_func

    return (
        db.scalar(
            select(sa_func.count())
            .select_from(SteamSpySync)
            .where(SteamSpySync.status == SYNC_PENDING)
        )
        or 0
    )
