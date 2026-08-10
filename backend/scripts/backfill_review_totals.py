#!/usr/bin/env python
"""Trae de Steam la cantidad REAL de reseñas de cada juego ya enriquecido.

Sin esto, la popularidad de un juego se medía contando las reseñas que
llegamos a importar — y ese número está capado en
``STEAM_REVIEWS_IMPORT_LIMIT``. El resultado era que todos los juegos
parecían igual de populares (~70 reseñas cada uno) y ganaba siempre el que
tuviera la muestra más positiva: un indie con 556 reseñas al 98 % le pasaba
por encima a Counter-Strike, con 9,7 millones al 86 %.

Es un pedido liviano por juego (``num_per_page=1``: sólo interesa el
``query_summary``) y es idempotente: se puede cortar y retomar.

Uso:
    python scripts/backfill_review_totals.py            # todos los que falten
    python scripts/backfill_review_totals.py --limit 200
    python scripts/backfill_review_totals.py --delay 2  # más pausa
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

from sqlalchemy import select  # noqa: E402

from app.db.base import SessionLocal  # noqa: E402
from app.models import Game  # noqa: E402
from app.services import steam_service  # noqa: E402
from app.services.interaction_service import recompute_game_aggregates  # noqa: E402

DEFAULT_DELAY = 1.0
# Cuántos fallos seguidos se toleran antes de asumir que Steam dejó de
# responder: uno suelto puede ser un juego sin ficha de reseñas.
MAX_CONSECUTIVE_FAILURES = 5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=None, help="cuántos juegos procesar")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="segundos entre pedidos")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        statement = (
            select(Game)
            .where(
                Game.steam_app_id.is_not(None),
                Game.steam_synced_at.is_not(None),
                Game.steam_total_reviews.is_(None),
            )
            .order_by(Game.id)
        )
        if args.limit:
            statement = statement.limit(args.limit)
        games = list(db.scalars(statement))

        if not games:
            print("Todos los juegos enriquecidos ya tienen sus totales.")
            return 0

        print(f"Consultando totales de {len(games)} juegos ({args.delay}s entre pedidos)...")
        done = 0
        consecutive_failures = 0
        for index, game in enumerate(games, start=1):
            totals = steam_service.get_review_totals(game.steam_app_id)
            if totals is None:
                consecutive_failures += 1
                print(f"  [{index}/{len(games)}] {game.name}: sin datos")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(
                        f"\n{MAX_CONSECUTIVE_FAILURES} fallos seguidos: Steam dejó de "
                        "responder. Se corta acá; volvé a correrlo más tarde."
                    )
                    break
            else:
                consecutive_failures = 0
                game.steam_total_reviews, game.steam_positive_reviews = totals
                db.commit()
                # Rehace avg_rating/ratings_count/popularity_score con el
                # total real en lugar del tamaño de la muestra importada.
                recompute_game_aggregates(db, game.id)
                db.commit()
                done += 1
                print(f"  [{index}/{len(games)}] {game.name}: {totals[0]:,} reseñas")
            if index < len(games):
                time.sleep(args.delay)

        print()
        print(f"Actualizados: {done}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
