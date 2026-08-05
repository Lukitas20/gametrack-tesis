#!/usr/bin/env python
"""Sincroniza el indice completo de Steam y enriquece fichas por lotes.

Ejemplos:
    python scripts/sync_steam_catalog.py --catalog
    python scripts/sync_steam_catalog.py --catalog --incremental
    python scripts/sync_steam_catalog.py --enrich --limit 5000 --batch-size 500
    python scripts/sync_steam_catalog.py --reviews 300 --limit 5000 --batch-size 50

El indice oficial requiere ``STEAM_API_KEY``. El enriquecimiento usa la ficha
publica de la tienda, no trae resenas y puede reanudarse sin duplicar datos.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import truststore

truststore.inject_into_ssl()

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import delete, func, select  # noqa: E402

from app.db.base import SessionLocal, init_db  # noqa: E402
from app.models import (  # noqa: E402
    Game,
    SteamCatalogEntry,
    SteamCatalogTarget,
    SteamEnrichmentState,
)
from app.services import steam_service  # noqa: E402


def sync_catalog(db, *, incremental: bool, page_size: int) -> int:
    state = steam_service.get_sync_state(db)

    if state.last_appid:
        cursor = state.last_appid
        since = state.if_modified_since
        print(f"Reanudando desde AppID {cursor}...")
    else:
        cursor = 0
        completed_at = state.completed_at
        if completed_at is not None and completed_at.tzinfo is None:
            # SQLite no conserva el huso de DateTime aunque el modelo lo
            # declare; el valor se guardo siempre en UTC.
            completed_at = completed_at.replace(tzinfo=UTC)
        since = int(completed_at.timestamp()) if incremental and completed_at else 0
        state.if_modified_since = since
        db.commit()

    processed = 0
    while True:
        page = steam_service.get_store_catalog_page(
            last_appid=cursor,
            if_modified_since=since,
            max_results=page_size,
        )
        apps = page["apps"]
        stats = steam_service.upsert_store_catalog_page(db, apps)
        processed += len(apps)
        cursor = page["last_appid"]
        complete = not page["have_more_results"]
        steam_service.mark_catalog_page_synced(
            db,
            state,
            last_appid=cursor,
            if_modified_since=since,
            complete=complete,
        )
        print(
            f"  {processed} recibidos | {stats['created']} nuevos | "
            f"{stats['linked']} vinculados | {stats['updated']} actualizados"
        )
        if complete:
            break

    total = db.scalar(select(func.count(Game.id))) or 0
    pending = db.scalar(
        select(func.count(SteamCatalogEntry.steam_app_id)).where(
            SteamCatalogEntry.metadata_status != "complete"
        )
    ) or 0
    print(f"Catalogo sincronizado: {total} juegos; {pending} fichas por enriquecer.")
    return processed


def ensure_popular_plan(
    db,
    *,
    limit: int,
    selection_delay: float,
    refresh: bool = False,
) -> tuple[str, list[SteamCatalogTarget]]:
    """Crea una selección estable de los juegos más relevantes de Steam."""
    plan_key = f"popular-{limit}"
    existing = list(
        db.scalars(
            select(SteamCatalogTarget)
            .where(SteamCatalogTarget.plan_key == plan_key)
            .order_by(SteamCatalogTarget.rank)
        )
    )
    if existing and not refresh:
        print(f"Plan {plan_key} recuperado: {len(existing)} juegos seleccionados.")
        return plan_key, existing

    if refresh:
        db.execute(
            delete(SteamCatalogTarget).where(
                SteamCatalogTarget.plan_key == plan_key
            )
        )
        db.commit()

    print(f"Seleccionando los {limit} juegos más relevantes de Steam...")
    appids = steam_service.get_top_seller_appids(limit, delay=selection_delay)
    catalog_appids = set(
        db.scalars(
            select(SteamCatalogEntry.steam_app_id).where(
                SteamCatalogEntry.steam_app_id.in_(appids)
            )
        )
    )
    missing = len(appids) - len(catalog_appids)
    if missing:
        print(
            f"Aviso: {missing} AppIDs no están en el índice local; "
            "ejecutá --catalog para incorporarlos."
        )

    targets = [
        SteamCatalogTarget(plan_key=plan_key, steam_app_id=appid, rank=rank)
        for rank, appid in enumerate(appids, start=1)
        if appid in catalog_appids
    ]
    db.add_all(targets)
    db.commit()
    print(f"Plan {plan_key} guardado en SQLite: {len(targets)} juegos.")
    return plan_key, targets


def _batch_limit(query, batch_size: int):
    return query.limit(batch_size) if batch_size else query


def enrich_pending(
    db,
    *,
    limit: int,
    batch_size: int,
    delay: float,
    strategy: str,
    selection_delay: float,
    refresh_selection: bool,
) -> tuple[int, int]:
    if strategy == "popular":
        plan_key, targets = ensure_popular_plan(
            db,
            limit=limit,
            selection_delay=selection_delay,
            refresh=refresh_selection,
        )
        target_count = len(targets)
        query = (
            select(SteamCatalogEntry, SteamCatalogTarget.rank)
            .join(
                SteamCatalogTarget,
                SteamCatalogTarget.steam_app_id
                == SteamCatalogEntry.steam_app_id,
            )
            .where(
                SteamCatalogTarget.plan_key == plan_key,
                SteamCatalogEntry.metadata_status != "complete",
            )
            .order_by(SteamCatalogTarget.rank)
        )
        rows = list(db.execute(_batch_limit(query, batch_size)))
    else:
        target_count = limit
        effective_limit = min(limit, batch_size) if batch_size else limit
        query = (
            select(SteamCatalogEntry)
            .where(SteamCatalogEntry.metadata_status != "complete")
            .order_by(SteamCatalogEntry.last_modified.desc().nulls_last())
            .limit(effective_limit)
        )
        entries = list(db.scalars(query))
        rows = [(entry, index) for index, entry in enumerate(entries, start=1)]

    if not rows:
        print("No hay fichas pendientes en esta selección.")
        return 0, 0

    completed = 0
    failed = 0
    for batch_index, (entry, rank) in enumerate(rows, start=1):
        game = steam_service.import_game(
            db, entry.steam_app_id, include_reviews=False, refresh=True
        )
        if game is None:
            failed += 1
            print(
                f"  [{rank}/{target_count}] AppID {entry.steam_app_id}: no disponible"
            )
        else:
            completed += 1
            print(f"  [{rank}/{target_count}] {game.name}")
        if batch_index < len(rows) and delay > 0:
            time.sleep(delay)

    remaining = db.scalar(
        select(func.count(SteamCatalogTarget.steam_app_id))
        .join(
            SteamCatalogEntry,
            SteamCatalogEntry.steam_app_id == SteamCatalogTarget.steam_app_id,
        )
        .where(
            SteamCatalogTarget.plan_key == f"popular-{limit}",
            SteamCatalogEntry.metadata_status != "complete",
        )
    ) if strategy == "popular" else None
    suffix = f"; quedan {remaining} pendientes" if remaining is not None else ""
    print(
        f"Lote terminado: {completed} completas; {failed} fallidas{suffix}."
    )
    return completed, failed


def import_popular_reviews(
    db,
    *,
    plan_limit: int,
    reviews_limit: int,
    batch_size: int,
    delay: float,
    selection_delay: float,
) -> tuple[int, int]:
    """Importa reseñas sólo para la cabecera del plan popular."""
    plan_key, _ = ensure_popular_plan(
        db, limit=plan_limit, selection_delay=selection_delay
    )
    rows = list(
        db.execute(
            _batch_limit(
                select(Game, SteamCatalogTarget.rank)
                .join(
                    SteamCatalogEntry,
                    SteamCatalogEntry.game_id == Game.id,
                )
                .join(
                    SteamCatalogTarget,
                    SteamCatalogTarget.steam_app_id
                    == SteamCatalogEntry.steam_app_id,
                )
                .outerjoin(
                    SteamEnrichmentState,
                    SteamEnrichmentState.game_id == Game.id,
                )
                .where(
                    SteamCatalogTarget.plan_key == plan_key,
                    SteamCatalogTarget.rank <= reviews_limit,
                    (
                        SteamEnrichmentState.status.is_(None)
                        | (SteamEnrichmentState.status != "complete")
                    ),
                )
                .order_by(SteamCatalogTarget.rank),
                batch_size,
            )
        )
    )
    if not rows:
        print("No hay reseñas pendientes en esta selección.")
        return 0, 0

    completed = 0
    failed = 0
    for batch_index, (game, rank) in enumerate(rows, start=1):
        state_row = db.get(SteamEnrichmentState, game.id)
        if state_row is None:
            state_row = SteamEnrichmentState(game_id=game.id)
            db.add(state_row)
        state_row.status = "running"
        state_row.started_at = datetime.now(UTC)
        state_row.last_error = None
        db.commit()
        try:
            imported = steam_service.enrich_catalog_game(db, game.id)
            state_row = db.get(SteamEnrichmentState, game.id)
            state_row.status = "complete"
            state_row.reviews_imported = imported
            state_row.completed_at = datetime.now(UTC)
            state_row.last_error = None
            db.commit()
            completed += 1
            print(f"  [{rank}/{reviews_limit}] {game.name}: {imported} reseñas")
        except Exception as exc:
            db.rollback()
            state_row = db.get(SteamEnrichmentState, game.id)
            state_row.status = "failed"
            state_row.last_error = str(exc)[:500]
            state_row.completed_at = datetime.now(UTC)
            db.commit()
            failed += 1
            print(f"  [{rank}/{reviews_limit}] {game.name}: error")
        if batch_index < len(rows) and delay > 0:
            time.sleep(delay)

    print(f"Lote de reseñas terminado: {completed} completas; {failed} fallidas.")
    return completed, failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sincroniza el catalogo completo de Steam de forma progresiva."
    )
    parser.add_argument("--catalog", action="store_true", help="sincroniza AppIDs y nombres")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="con --catalog, pide solo cambios desde la ultima sincronizacion",
    )
    parser.add_argument("--enrich", action="store_true", help="completa fichas pendientes")
    parser.add_argument(
        "--reviews",
        type=int,
        default=0,
        metavar="N",
        help="trae reseñas para los primeros N juegos del plan popular",
    )
    parser.add_argument(
        "--strategy",
        choices=("popular", "recent"),
        default="popular",
        help="cómo priorizar las fichas (default: popular)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="tamaño objetivo del catálogo enriquecido (default: 5000)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="máximo a procesar en esta ejecución; 0 procesa todos",
    )
    parser.add_argument("--delay", type=float, default=1.0, help="pausa entre fichas")
    parser.add_argument(
        "--selection-delay",
        type=float,
        default=1.0,
        help="pausa entre páginas al construir la selección popular",
    )
    parser.add_argument(
        "--refresh-selection",
        action="store_true",
        help="vuelve a calcular el ranking popular guardado",
    )
    parser.add_argument(
        "--page-size", type=int, default=50_000, help="resultados por pagina del indice"
    )
    args = parser.parse_args()

    if not args.catalog and not args.enrich and not args.reviews:
        parser.error("elegí --catalog, --enrich, --reviews N o una combinación")
    if args.incremental and not args.catalog:
        parser.error("--incremental requiere --catalog")
    if (
        args.limit < 1
        or args.page_size < 1
        or args.batch_size < 0
        or args.reviews < 0
        or args.delay < 0
        or args.selection_delay < 0
    ):
        parser.error("los límites deben ser positivos y las pausas no negativas")
    if args.reviews > args.limit:
        parser.error("--reviews no puede superar --limit")
    if args.reviews and args.strategy != "popular":
        parser.error("--reviews requiere --strategy popular")

    init_db()
    with SessionLocal() as db:
        try:
            if args.catalog:
                sync_catalog(db, incremental=args.incremental, page_size=args.page_size)
            if args.enrich:
                enrich_pending(
                    db,
                    limit=args.limit,
                    batch_size=args.batch_size,
                    delay=args.delay,
                    strategy=args.strategy,
                    selection_delay=args.selection_delay,
                    refresh_selection=args.refresh_selection,
                )
            if args.reviews:
                import_popular_reviews(
                    db,
                    plan_limit=args.limit,
                    reviews_limit=args.reviews,
                    batch_size=args.batch_size,
                    delay=args.delay,
                    selection_delay=args.selection_delay,
                )
        except steam_service.SteamCatalogError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
