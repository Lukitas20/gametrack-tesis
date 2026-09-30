#!/usr/bin/env python
"""Mide cobertura y acuerdo de las fuentes de horas acumuladas de juego.

Compara la mediana de ``Review.hours_at_review`` con ``Game.playtime_hours``
de RAWG sobre la base configurada. Ambas son referencias de tiempo acumulado,
no duraciones de campaña ni de una sesión. Este diagnóstico permite revisar:

1. Cobertura: cuántos juegos con reseñas importadas tienen suficientes
   muestras de horas (>= --min-samples) como para que la mediana signifique
   algo, y cuántos quedan cubiertos sólo por RAWG, o por nadie.
2. Sesgo: los reseñadores juegan más que el promedio. Para dimensionarlo, en
   los juegos que tienen AMBAS fuentes se compara la clasificación por
   franjas del asistente (una tarde <=15 h / un finde <=40 h / sin apuro) y
   se reporta el acuerdo. Si el acuerdo por franja es alto, el sesgo en
   horas absolutas no importa: el filtro trabaja por franja, no por hora.

No consulta Steam ni RAWG: sólo lee la base configurada.

Uso:
    python scripts/diagnostico_playtime.py
    python scripts/diagnostico_playtime.py --min-samples 10
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from statistics import median

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import func, select  # noqa: E402

from app.db.base import SessionLocal  # noqa: E402
from app.models import Game, Review  # noqa: E402

# Franjas del asistente (frontend/js/views/quiz.js).
BUCKETS = [("una tarde (<=15 h)", 15), ("un finde (<=40 h)", 40), ("sin apuro", None)]


def bucket(hours: float) -> str:
    for label, top in BUCKETS:
        if top is None or hours <= top:
            return label
    return BUCKETS[-1][0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--min-samples",
        type=int,
        default=5,
        help="reseñas con horas necesarias para confiar en la mediana (default: 5)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        total_games = db.scalar(select(func.count(Game.id))) or 0
        games_with_reviews = {
            row[0]
            for row in db.execute(select(Review.game_id).distinct())
        }

        # Horas por juego, sólo muestras utilizables (> 0).
        hours_by_game: dict[int, list[float]] = {}
        for game_id, hours in db.execute(
            select(Review.game_id, Review.hours_at_review).where(
                Review.hours_at_review.is_not(None), Review.hours_at_review > 0
            )
        ):
            hours_by_game.setdefault(game_id, []).append(float(hours))

        medians = {
            game_id: median(values)
            for game_id, values in hours_by_game.items()
            if len(values) >= args.min_samples
        }

        rawg = {
            game_id: float(hours)
            for game_id, hours in db.execute(
                select(Game.id, Game.playtime_hours).where(
                    Game.playtime_hours.is_not(None), Game.playtime_hours > 0
                )
            )
        }

        print("=" * 64)
        print("Cobertura de fuentes de duración")
        print("=" * 64)
        print(f"Juegos en el catálogo:                        {total_games}")
        print(f"Juegos con reseñas importadas:                {len(games_with_reviews)}")
        print(f"  ... con alguna reseña con horas (>0):       {len(hours_by_game)}")
        print(
            f"  ... con mediana confiable (>= {args.min_samples} muestras):   "
            f"{len(medians)}"
        )
        print(f"Juegos con playtime de RAWG:                  {len(rawg)}")

        with_reviews = set(games_with_reviews)
        only_median = set(medians) - set(rawg)
        only_rawg = (set(rawg) & with_reviews) - set(medians)
        both = set(medians) & set(rawg)
        neither = with_reviews - set(medians) - set(rawg)
        print()
        print("Sobre los juegos con reseñas importadas (el universo del ABSA):")
        print(f"  cubiertos por mediana de reseñadores solo:  {len(only_median)}")
        print(f"  cubiertos por RAWG solo:                    {len(only_rawg)}")
        print(f"  cubiertos por ambas fuentes:                {len(both)}")
        print(f"  sin ninguna fuente de duración:             {len(neither)}")

        if hours_by_game:
            samples = sorted(len(v) for v in hours_by_game.values())
            print()
            print(
                "Muestras de horas por juego (mín/mediana/máx): "
                f"{samples[0]} / {samples[len(samples) // 2]} / {samples[-1]}"
            )

        if both:
            print()
            print("=" * 64)
            print(f"Acuerdo por franja del asistente ({len(both)} juegos con ambas fuentes)")
            print("=" * 64)
            agreements = 0
            confusion: Counter[tuple[str, str]] = Counter()
            worst: list[tuple[float, float, int]] = []
            for game_id in both:
                by_median = bucket(medians[game_id])
                by_rawg = bucket(rawg[game_id])
                confusion[(by_rawg, by_median)] += 1
                if by_median == by_rawg:
                    agreements += 1
                else:
                    worst.append((rawg[game_id], medians[game_id], game_id))

            print(f"Acuerdo: {agreements}/{len(both)} ({100 * agreements / len(both):.0f}%)")
            print()
            print(f"{'RAWG \\ mediana reseñadores':<28}", end="")
            labels = [label for label, _ in BUCKETS]
            for label in labels:
                print(f"{label:>20}", end="")
            print()
            for row_label in labels:
                print(f"{row_label:<28}", end="")
                for col_label in labels:
                    print(f"{confusion.get((row_label, col_label), 0):>20}", end="")
                print()

            if worst:
                print()
                print("Mayores desacuerdos (RAWG h -> mediana h):")
                worst.sort(key=lambda item: abs(item[0] - item[1]), reverse=True)
                # ``dict(result)`` no sirve: ``Result`` tiene ``.keys()`` y
                # dict() lo confunde con un mapping. Materializar primero.
                names = dict(
                    db.execute(
                        select(Game.id, Game.name).where(
                            Game.id.in_([g for _, _, g in worst[:8]])
                        )
                    ).all()
                )
                for rawg_hours, median_hours, game_id in worst[:8]:
                    print(
                        f"  {names.get(game_id, game_id)}: "
                        f"{rawg_hours:.0f} h -> {median_hours:.0f} h"
                    )

        print()
        print("Lectura sugerida: si 'sin ninguna fuente' es chico y el acuerdo")
        print("por franja es alto, la propuesta mediana-de-reseñadores + RAWG")
        print("de respaldo se sostiene; si no, hay que replantear la pregunta")
        print("de tiempo hacia algo con dato detrás (p. ej. etiquetas Short/")
        print("Story Rich) antes de comprometer el diseño.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
