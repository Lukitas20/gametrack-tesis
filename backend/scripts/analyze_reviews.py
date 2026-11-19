#!/usr/bin/env python
"""Analiza exclusivamente las reseñas reales guardadas en GameTrack."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.base import SessionLocal
from app.ml.analytics import analyze_pending_reviews


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reanalyze', action='store_true')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--evaluate', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.evaluate:
        parser.error('La evaluación con ground truth sintético fue retirada. Se requiere un corpus real anotado independiente.')
    with SessionLocal() as db:
        processed = analyze_pending_reviews(db, limit=args.limit, reanalyze=args.reanalyze)
    print(f'Reseñas reales procesadas: {processed}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
