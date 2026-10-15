"""Recupera aliases públicos de autores Steam; sólo imprime contadores.

Reanudable, sin reseñas nuevas ni reentrenamiento. --audit no modifica datos
ni consulta Steam. El resto requiere la STEAM_API_KEY ya configurada.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, or_, select
from app.core.config import settings
from app.db.database import SessionLocal
from app.models import Game, Review
from app.services.steam_review_authors import UNKNOWN_NAMES, repair_game_authors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--game-id", type=int)
    args = parser.parse_args()
    if not (1 <= args.limit <= 10000 and 1 <= args.max_pages <= 100):
        parser.error("limit debe ser 1..10000 y max-pages 1..100")
    pending = (Review.source == "steam", or_(Review.author_name.is_(None),
               Review.author_name.in_(UNKNOWN_NAMES), Review.author_name == ""))
    report = {"operation": "steam_review_authors", "audit": args.audit,
              "games_processed": 0, "examined": 0, "names_recovered": 0,
              "identities_recovered": 0, "pages": 0}
    try:
        with SessionLocal() as db:
            report["missing_before"] = db.scalar(select(func.count(Review.id)).where(*pending)) or 0
            report["games_missing_before"] = db.scalar(select(func.count(func.distinct(Review.game_id))).where(*pending)) or 0
            if not args.audit:
                if not settings.STEAM_API_KEY:
                    print(json.dumps({**report, "result": "steam_key_unavailable"}), flush=True)
                    return 1
                query = select(Game).where(Game.steam_app_id.is_not(None),
                    Game.id.in_(select(Review.game_id).where(*pending))).order_by(Game.ratings_count.desc(), Game.id)
                if args.game_id:
                    query = query.where(Game.id == args.game_id)
                games = list(db.scalars(query.limit(args.limit)))
                for game in games:
                    result = repair_game_authors(db, game, max_pages=args.max_pages)
                    db.commit()
                    for key in ("examined", "names_recovered", "identities_recovered", "pages"):
                        report[key] += result[key]
                    report["games_processed"] += 1
                    if report["games_processed"] % 10 == 0:
                        print(json.dumps({**report, "result": "progress"}), flush=True)
                    time.sleep(1)
            report["missing_after"] = db.scalar(select(func.count(Review.id)).where(*pending)) or 0
            print(json.dumps({**report, "result": "complete"}), flush=True)
            return 0
    except Exception:
        # Excepciones de drivers HTTP/SQL pueden incluir claves o identificadores.
        print(json.dumps({**report, "result": "failed_no_private_details"}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
