#!/usr/bin/env python
"""Ejecuta el módulo NLP sobre las reseñas y, opcionalmente, lo evalúa.

Uso:
    python scripts/analyze_reviews.py                 # procesa las pendientes
    python scripts/analyze_reviews.py --reanalyze     # reprocesa todas
    python scripts/analyze_reviews.py --evaluate      # además, mide precisión

La evaluación compara la salida contra ``data/generated/reviews_ground_truth.json``,
que el seed genera con las etiquetas reales de cada reseña sintética.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.config import DATA_DIR  # noqa: E402
from app.db.base import SessionLocal  # noqa: E402
from app.ml.analytics import analyze_pending_reviews  # noqa: E402
from app.models import Review, ReviewAspect, Sentiment  # noqa: E402

GROUND_TRUTH_PATH = DATA_DIR / "generated" / "reviews_ground_truth.json"
LABELS = [s.value for s in Sentiment]


def _prf(true_positives: int, false_positives: int, false_negatives: int) -> tuple[float, float, float]:
    precision = true_positives / (true_positives + false_positives) if true_positives + false_positives else 0.0
    recall = true_positives / (true_positives + false_negatives) if true_positives + false_negatives else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def evaluate() -> None:
    if not GROUND_TRUTH_PATH.exists():
        print(f"No se encontró el ground truth en {GROUND_TRUTH_PATH}.")
        print("Generalo corriendo scripts/seed_data.py --reset")
        return

    truth = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    truth_by_review = {row["review_id"]: row for row in truth}

    with SessionLocal() as db:
        reviews = {
            review.id: review
            for review in db.scalars(select(Review).where(Review.is_analyzed.is_(True)))
        }
        predicted_aspects: dict[int, dict[str, str]] = defaultdict(dict)
        for row in db.scalars(select(ReviewAspect)):
            predicted_aspects[row.review_id][row.aspect.value] = row.sentiment.value

        evaluated = [rid for rid in truth_by_review if rid in reviews]
        if not evaluated:
            print("No hay reseñas analizadas que coincidan con el ground truth.")
            print("Corré este script sin --evaluate primero.")
            return

        # --- Sentimiento global -------------------------------------------
        # Se evalúa contra la polaridad que expresa el *texto*, que es lo único
        # que el módulo puede leer. La banda derivada del puntaje se compara
        # aparte, más abajo.
        confusion = {real: {pred: 0 for pred in LABELS} for real in LABELS}
        for review_id in evaluated:
            entry = truth_by_review[review_id]
            real = entry.get("sentimiento_texto") or entry["sentimiento_por_puntaje"]
            pred = reviews[review_id].sentiment.value
            confusion[real][pred] += 1

        total = len(evaluated)
        correct = sum(confusion[label][label] for label in LABELS)

        print("=" * 62)
        print(f"SENTIMIENTO GLOBAL  ({total} reseñas)")
        print("=" * 62)
        print(f"Exactitud: {correct / total:.1%}\n")
        print("Matriz de confusión (filas = real, columnas = predicho):")
        print(f"{'':>10}" + "".join(f"{label:>11}" for label in LABELS))
        for real in LABELS:
            row = "".join(f"{confusion[real][pred]:>11}" for pred in LABELS)
            print(f"{real:>10}{row}")

        print()
        macro_f1 = 0.0
        for label in LABELS:
            tp = confusion[label][label]
            fp = sum(confusion[other][label] for other in LABELS if other != label)
            fn = sum(confusion[label][other] for other in LABELS if other != label)
            precision, recall, f1 = _prf(tp, fp, fn)
            macro_f1 += f1 / len(LABELS)
            print(f"  {label:>10}  P={precision:.3f}  R={recall:.3f}  F1={f1:.3f}")
        print(f"\n  F1 macro: {macro_f1:.3f}")

        # --- Detección de aspectos (ABSA) ---------------------------------
        tp = fp = fn = 0
        polarity_ok = 0
        per_aspect = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "pol_ok": 0})

        for review_id in evaluated:
            real = {a["aspecto"]: a["sentimiento"] for a in truth_by_review[review_id]["aspectos"]}
            pred = predicted_aspects.get(review_id, {})

            for aspect in real.keys() | pred.keys():
                if aspect in real and aspect in pred:
                    tp += 1
                    per_aspect[aspect]["tp"] += 1
                    if real[aspect] == pred[aspect]:
                        polarity_ok += 1
                        per_aspect[aspect]["pol_ok"] += 1
                elif aspect in pred:
                    fp += 1
                    per_aspect[aspect]["fp"] += 1
                else:
                    fn += 1
                    per_aspect[aspect]["fn"] += 1

        precision, recall, f1 = _prf(tp, fp, fn)
        print()
        print("=" * 62)
        print("ABSA — DETECCIÓN DE ASPECTOS")
        print("=" * 62)
        print(f"Precisión={precision:.3f}  Recall={recall:.3f}  F1={f1:.3f}")
        print(f"(VP={tp}  FP={fp}  FN={fn})")
        print(
            f"\nExactitud de polaridad sobre aspectos detectados: "
            f"{polarity_ok / tp:.1%}" if tp else ""
        )

        print("\nPor aspecto:")
        print(f"{'aspecto':>14}{'P':>8}{'R':>8}{'F1':>8}{'polaridad':>12}")
        for aspect in sorted(per_aspect):
            stats = per_aspect[aspect]
            p, r, a_f1 = _prf(stats["tp"], stats["fp"], stats["fn"])
            polarity = stats["pol_ok"] / stats["tp"] if stats["tp"] else 0.0
            print(f"{aspect:>14}{p:>8.3f}{r:>8.3f}{a_f1:>8.3f}{polarity:>11.1%}")

        # --- Texto contra puntaje -----------------------------------------
        # No mide al clasificador: mide cuánta gente escribe algo distinto de
        # lo que puntúa. Es el techo real de cualquier intento de deducir la
        # nota a partir del texto.
        agreements = sum(
            1
            for review_id in evaluated
            if truth_by_review[review_id].get("sentimiento_texto")
            == truth_by_review[review_id]["sentimiento_por_puntaje"]
        )
        print()
        print("=" * 62)
        print("REFERENCIA: TEXTO vs. PUNTAJE")
        print("=" * 62)
        print(
            f"El texto coincide con la banda del puntaje en {agreements / total:.1%} "
            "de las reseñas."
        )
        print(
            "La diferencia es intrínseca a los datos, no un error del módulo:\n"
            "hay quien puntúa 3 y escribe elogios, o puntúa alto y sólo se queja."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Analiza las reseñas con el módulo NLP.")
    parser.add_argument(
        "--reanalyze", action="store_true", help="Reprocesa también las ya analizadas."
    )
    parser.add_argument(
        "--evaluate", action="store_true", help="Compara la salida contra el ground truth."
    )
    parser.add_argument("--limit", type=int, help="Máximo de reseñas a procesar.")
    args = parser.parse_args()

    with SessionLocal() as db:
        processed = analyze_pending_reviews(
            db, limit=args.limit, reanalyze=args.reanalyze
        )
    print(f"Reseñas procesadas: {processed}\n")

    if args.evaluate:
        evaluate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
