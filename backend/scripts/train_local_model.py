"""Entrenar IA local: python scripts/train_local_model.py --data-label demo.

Usar --data-label observed sólo con notas de participantes reales. La etiqueta
documenta procedencia declarada; no convierte datos sintéticos en datos reales.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.db.base import SessionLocal
from app.ml.local_model import train_and_save


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-label", required=True, choices=("demo", "observed"))
    parser.add_argument("--factors", type=int, default=12)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path(settings.LOCAL_MODEL_PATH))
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            report = train_and_save(db, args.output, data_label=args.data_label,
                                    factors=args.factors, epochs=args.epochs)
        except ValueError as error:
            parser.error(str(error))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
