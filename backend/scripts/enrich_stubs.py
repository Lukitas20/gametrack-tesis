#!/usr/bin/env python
"""Procesa fichas de Steam pendientes o vencidas usando la cola persistente.

Prioriza visitas, respeta reintentos y comparte el bloqueo con la aplicación.
El orden de AppID del índice oficial no representa popularidad.
"""
import argparse
import json
import sys
from pathlib import Path

import truststore
truststore.inject_into_ssl()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.db.base import SessionLocal, init_db
from app.services.steam_catalog_worker import process_details


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=50, help="máximo de fichas a intentar")
    parser.add_argument("--delay", type=float, default=2, help="pausa entre fichas, al menos un segundo")
    args = parser.parse_args()
    if args.count < 1 or not 1 <= args.delay <= 300:
        parser.error("count debe ser positivo y delay debe estar entre 1 y 300")
    settings.STEAM_CATALOG_REQUEST_DELAY_SECONDS = args.delay
    init_db()
    attempted = 0
    while attempted < args.count:
        with SessionLocal() as db:
            result = process_details(db, limit=min(20, args.count - attempted))
        print(json.dumps(result, ensure_ascii=False))
        if result["busy"]:
            print("Otro proceso está actualizando el catálogo. Reintentá cuando finalice la tanda.")
            return 2
        attempted += result["attempted"]
        if not result["attempted"] or result.get("retry_after_seconds"):
            break
    print(f"Fichas intentadas: {attempted}. Las pendientes se conservan para otra tanda.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
