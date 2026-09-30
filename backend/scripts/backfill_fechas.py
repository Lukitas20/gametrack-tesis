#!/usr/bin/env python
"""Rellena ``Game.released`` en los juegos ya enriquecidos desde Steam.

Repara fichas importadas antes de que el parser reconociera fechas en
español (por ejemplo, "21 AGO 2012"), sin esperar al refresco periódico.
Procesa primero las fichas más populares.

Es reanudable y puramente aditivo: sólo toca ``released``, y sólo donde
está vacío.

Uso:
    python scripts/backfill_fechas.py              # todo lo enriquecido
    python scripts/backfill_fechas.py --limit 300  # los 300 mas populares
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import truststore  # noqa: E402

# Usa el almacen de certificados del sistema operativo (ver app/main.py).
# Sin esto httpx valida contra certifi y Steam falla con
# CERTIFICATE_VERIFY_FAILED, que el script leeria como "Steam esta caido".
truststore.inject_into_ssl()

from sqlalchemy import select  # noqa: E402

from app.db.base import SessionLocal  # noqa: E402
from app.models import Game  # noqa: E402
from app.services import steam_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--pausa", type=float, default=0.35, help="segundos entre pedidos")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        consulta = (
            select(Game)
            .where(
                Game.released.is_(None),
                Game.steam_app_id.is_not(None),
                Game.steam_synced_at.is_not(None),
            )
            .order_by(Game.ratings_count.desc())
        )
        if args.limit:
            consulta = consulta.limit(args.limit)
        juegos = list(db.scalars(consulta))
        print(f"{len(juegos)} juegos enriquecidos sin fecha de lanzamiento.")

        puestas = sin_dato = caidas = 0
        for indice, juego in enumerate(juegos, start=1):
            try:
                data = steam_service.get_app_details(juego.steam_app_id)
            except steam_service.SteamUnavailable:
                # Steam no contestó: NO es "este juego no tiene fecha".
                caidas += 1
                if caidas >= 20:
                    print("  demasiados fallos seguidos de Steam, corto acá.")
                    break
                continue
            caidas = 0
            crudo = ((data or {}).get("release_date") or {}).get("date", "")
            fecha = steam_service._parse_release_date(crudo)
            if fecha is None:
                sin_dato += 1
            else:
                juego.released = fecha
                puestas += 1
            if indice % 50 == 0:
                db.commit()
                print(f"  {indice}/{len(juegos)}  con fecha: {puestas}  sin dato: {sin_dato}")
            time.sleep(args.pausa)

        db.commit()
        print(f"\nListo. Fechas puestas: {puestas} | sin dato en Steam: {sin_dato}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
