#!/usr/bin/env python
"""Backfill de ``median_review_hours`` sobre las reseñas ya importadas.

El recálculo corre solo al importar reseñas nuevas (``import_reviews``); este
script lo aplica una vez sobre todo lo que ya está en la base. No toca la
red. Idempotente: correrlo de nuevo recalcula lo mismo.

Uso:
    python scripts/backfill_duracion.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import func, select  # noqa: E402

from app.db.base import SessionLocal  # noqa: E402
from app.models import Game, Review  # noqa: E402
from app.services.interaction_service import recompute_median_review_hours  # noqa: E402


def main() -> int:
    db = SessionLocal()
    try:
        game_ids = [
            row[0]
            for row in db.execute(select(Review.game_id).distinct())
        ]
        print(f"Recalculando la mediana de horas de {len(game_ids)} juegos con reseñas...")
        for index, game_id in enumerate(game_ids, start=1):
            recompute_median_review_hours(db, game_id)
            if index % 200 == 0:
                db.commit()
                print(f"  {index}...")
        db.commit()

        with_median = db.scalar(
            select(func.count(Game.id)).where(Game.median_review_hours.is_not(None))
        )
        print(f"Listo: {with_median} juegos con mediana de duración cargada.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
