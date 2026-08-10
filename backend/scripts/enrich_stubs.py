#!/usr/bin/env python
"""Completa fichas pendientes en lote, de la más popular a la menos.

``import_steam_appindex.py`` deja el índice de Steam como fichas pendientes:
sólo AppID y nombre. Cada una se completa sola la primera vez que alguien la
abre, lo que alcanza para navegar el catálogo — pero **no** para el
recomendador ni el asistente "¿Qué jugamos hoy?", que excluyen las fichas
sin enriquecer (sin géneros, etiquetas ni reseñas no hay nada que comparar).

El efecto práctico es que los juegos más jugados de Steam (Counter-Strike,
Apex, Rainbow Six Siege...) no pueden ser recomendados nunca hasta que
alguien los abra a mano. Esto los enriquece de antemano.

El orden importa: SteamSpy devuelve su índice ordenado por cantidad de
dueños, e ``import_stub_catalog`` respeta ese orden al insertar, así que los
``id`` más bajos son los juegos más populares. Enriquecer los primeros N es
enriquecer el top N de Steam.

Uso:
    python scripts/enrich_stubs.py --count 500        # los 500 más jugados
    python scripts/enrich_stubs.py --count 500 --delay 2   # más pausa

Es incremental e interrumpible: lo ya enriquecido se saltea, así que si
Steam corta a mitad de camino alcanza con volver a correrlo.
"""

from __future__ import annotations

import argparse
import sys
import time
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

from app.db.base import SessionLocal  # noqa: E402
from app.models import Game  # noqa: E402
from app.services import steam_service  # noqa: E402

DEFAULT_COUNT = 500
DEFAULT_DELAY = 1.5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="cuántas fichas completar")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="segundos entre pedidos")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        pending = list(
            db.scalars(
                select(Game)
                .where(Game.steam_app_id.is_not(None), Game.steam_synced_at.is_(None))
                .order_by(Game.id)
                .limit(args.count)
            )
        )
        if not pending:
            print("No quedan fichas pendientes.")
            return 0

        print(f"Completando {len(pending)} fichas ({args.delay}s entre pedidos)...")
        done = dropped = failed = 0
        for index, game in enumerate(pending, start=1):
            name = game.name
            try:
                alive = steam_service.refresh_game(db, game)
            except steam_service.SteamUnavailable as error:
                # Sin Steam no hay nada que completar: seguir sólo acumula
                # timeouts. Es incremental, así que se retoma más tarde.
                db.rollback()
                print(f"  [{index}/{len(pending)}] {name}: Steam no responde ({error}).")
                print("Se corta acá. Reintentá cuando Steam vuelva a responder.")
                break
            except Exception as error:  # noqa: BLE001 - una ficha rota no corta el lote
                db.rollback()
                failed += 1
                print(f"  [{index}/{len(pending)}] {name}: error ({error})")
            else:
                if alive:
                    done += 1
                    print(f"  [{index}/{len(pending)}] {name}")
                else:
                    dropped += 1
                    print(f"  [{index}/{len(pending)}] {name}: no era un juego, se descarta")
            if index < len(pending):
                time.sleep(args.delay)

        enriched = db.scalar(
            select(func.count(Game.id)).where(Game.steam_synced_at.is_not(None))
        )
        print()
        print(f"Completadas: {done} · descartadas: {dropped} · con error: {failed}")
        print(f"Juegos enriquecidos en total: {enriched}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
