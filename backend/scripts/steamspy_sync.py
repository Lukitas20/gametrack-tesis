#!/usr/bin/env python
"""Worker de ingesta de SteamSpy: índice masivo (nivel 0) y rasgos (nivel 1).

Nivel 0 (``--index``): consulta el índice de SteamSpy (``request=all``), crea
las fichas pendientes que falten, actualiza positivos/negativos/dueños de
las entradas recibidas y encola cada AppID para enriquecer, priorizado por
dueños. SteamSpy es una fuente complementaria: no garantiza la cobertura
del índice oficial de Steam. Los juegos sin rasgos todavía no participan
del recomendador.

Nivel 1 (``--enrich N``): consume la cola de a lotes contra el endpoint por
juego (~1 pedido/segundo): etiquetas comunitarias con votos, género y señal
de calidad. Incremental y reanudable: el estado vive en la tabla
``steamspy_sync``, cortar con Ctrl+C no pierde nada, y ante
``--max-failures`` fallos de transporte consecutivos el lote se aborta solo
en lugar de acumular timeouts toda la noche.

Invariante (heredado de ``steam_service.SteamUnavailable``): un fallo de red
deja la fila pendiente con el intento anotado; SOLO una respuesta válida de
SteamSpy sin datos marca ``skipped``, y ni siquiera eso borra la ficha del
catálogo.

Uso:
    python scripts/steamspy_sync.py --index                  # nivel 0 completo
    python scripts/steamspy_sync.py --index --max-pages 2    # prueba rápida
    python scripts/steamspy_sync.py --enrich 500             # top 500 pendientes
    python scripts/steamspy_sync.py --enrich 20000 --sleep 1 # una noche de worker
    python scripts/steamspy_sync.py --status                 # estado de la cola
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import truststore

# Usa el almacén de certificados del sistema operativo (ver app/main.py).
truststore.inject_into_ssl()

# El catálogo real de Steam tiene nombres en japonés, chino y cirílico que la
# consola de Windows (cp1252) no sabe imprimir: sin esto, mostrar el progreso
# corta el proceso entero con UnicodeEncodeError.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.db.base import SessionLocal, init_db  # noqa: E402
from app.models.steamspy import SteamSpySync  # noqa: E402
from app.services import steam_service, steamspy_service  # noqa: E402

BATCH_SIZE = 100  # tamaño de lote de nivel 1: reporta progreso cada ~100 s


def run_index(db, args) -> int:
    print("Pidiendo el índice masivo a SteamSpy (steamspy.com, no es un servicio de Valve)...")
    apps = steam_service.get_app_list(delay=args.index_delay, max_pages=args.max_pages)
    if not apps:
        print("No se pudo contactar a SteamSpy. Reintentá en unos minutos.")
        return 1
    print(f"{len(apps)} entradas recibidas. Volcando sobre la base...")

    report = steamspy_service.ingest_index_entries(db, apps, limit=args.limit)
    print()
    print("Nivel 0 (índice) completado:")
    print(f"  fichas pendientes nuevas:            {report.created}")
    print(f"  juegos existentes con señal fresca:  {report.updated}")
    print(f"  encolados para enriquecer:           {report.queued}")
    print(f"  re-priorizados en la cola:           {report.reprioritized}")
    print(f"  entradas inválidas del índice:       {report.invalid}")
    print(f"  pendientes totales en la cola:       {steamspy_service.pending_count(db)}")
    return 0


def run_enrich(db, args) -> int:
    remaining = args.enrich
    totals = steamspy_service.BatchReport()

    while remaining > 0:
        batch = steamspy_service.enrich_next_batch(
            db,
            limit=min(remaining, BATCH_SIZE),
            max_consecutive_failures=args.max_failures,
            sleep_seconds=args.sleep,
        )
        totals.processed += batch.processed
        totals.enriched += batch.enriched
        totals.skipped += batch.skipped
        totals.unavailable += batch.unavailable

        pending = steamspy_service.pending_count(db)
        print(
            f"  lote: {batch.enriched} enriquecidos, {batch.skipped} sin datos, "
            f"{batch.unavailable} indisponibles | pendientes: {pending}"
        )

        if batch.aborted:
            print()
            print(
                f"Lote abortado tras {args.max_failures} fallos de transporte "
                "consecutivos: SteamSpy parece caído o está limitando esta IP."
            )
            print("Nada se perdió: volvé a correr el mismo comando más tarde y retoma.")
            break
        if batch.processed == 0:
            print("No quedan filas pendientes en la cola.")
            break
        remaining -= batch.processed

    print()
    print("Nivel 1 (rasgos) — total de esta corrida:")
    print(f"  procesados:     {totals.processed}")
    print(f"  enriquecidos:   {totals.enriched}")
    print(f"  sin datos:      {totals.skipped}")
    print(f"  indisponibles:  {totals.unavailable}")
    return 0


def run_status(db) -> int:
    rows = db.execute(
        select(SteamSpySync.status, func.count()).group_by(SteamSpySync.status)
    ).all()
    if not rows:
        print("La cola está vacía: corré primero `--index`.")
        return 0
    print("Estado de la cola de SteamSpy:")
    for status, count in sorted(rows):
        print(f"  {status:<8} {count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--index", action="store_true", help="nivel 0: índice masivo + cola")
    parser.add_argument("--enrich", type=int, metavar="N", help="nivel 1: enriquecer N pendientes")
    parser.add_argument("--status", action="store_true", help="mostrar el estado de la cola")
    parser.add_argument("--limit", type=int, default=None, help="tope de fichas nuevas (pruebas)")
    parser.add_argument("--max-pages", type=int, default=None, help="tope de páginas del índice (pruebas)")
    parser.add_argument("--index-delay", type=float, default=1.5, help="segundos entre páginas del índice")
    parser.add_argument("--sleep", type=float, default=steamspy_service.DEFAULT_SLEEP_SECONDS, help="segundos entre pedidos por juego")
    parser.add_argument(
        "--max-failures",
        type=int,
        default=steamspy_service.DEFAULT_MAX_CONSECUTIVE_FAILURES,
        help="fallos de transporte consecutivos que abortan el lote",
    )
    args = parser.parse_args()

    if not (args.index or args.enrich or args.status):
        parser.error("indicá al menos una acción: --index, --enrich N o --status")

    db = SessionLocal()
    try:
        init_db()
        exit_code = 0
        if args.index:
            exit_code = run_index(db, args)
        if exit_code == 0 and args.enrich:
            exit_code = run_enrich(db, args)
        if exit_code == 0 and args.status:
            exit_code = run_status(db)
        return exit_code
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
