"""Comprobación agregada de sólo lectura sobre datos existentes, sin HTTP.

Sin --sqlite exige PostgreSQL y SET TRANSACTION READ ONLY. No importa app.main,
no migra, no sincroniza, no entrena, no crea sesiones de usuario ni perfiles.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.core.config import settings
from app.db.real_data import real_review_filter
from app.ml.analytics import game_analytics
from app.ml.trained_reviews import load_model, artifact_path
from app.models import Game, Review
from app.schemas.analytics import GameAnalyticsOut
from app.services.audience_service import audience_report
from app.services.trained_ai_service import review_model_report
from scripts.verify_affinity_runtime import begin_read_only, confirm_read_only, inspect_read_only_session
from scripts.verify_review_model import verify_artifact
from scripts.verify_steam_affinity_model import verify_artifact as verify_affinity_artifact


def inspect(db):
    confirm_read_only(db)
    review_integrity=verify_artifact(artifact_path())
    affinity_integrity=verify_affinity_artifact(Path(settings.STEAM_AFFINITY_MODEL_PATH))
    _, status=load_model()
    result=dict(read_only=True, external_http_calls=0, review_artifact=review_integrity,
                affinity_artifact=affinity_integrity, review_status=status["status"],
                affinity=inspect_read_only_session(db), scopes_checked=0, reviews_sampled=0,
                scopes_using_review_model=0, audience_groups_published=0, audience_users_by_scope=[])
    ids=db.scalars(select(Review.game_id).where(real_review_filter(),Review.source=="steam",Review.language=="es")
                   .group_by(Review.game_id).order_by(Review.game_id).limit(3))
    for gid in ids:
        game=db.get(Game,gid)
        report=game_analytics(db,game)
        report["trained_ai"]=review_model_report(db,game_id=gid,limit=100)
        report["audience"]=audience_report(db,game_id=gid)
        GameAnalyticsOut.model_validate(report)
        result["scopes_checked"]+=1
        result["reviews_sampled"]+=report["trained_ai"]["sampled"]
        result["scopes_using_review_model"]+=int(report["trained_ai"]["used"])
        result["audience_groups_published"]+=sum(not group["suppressed"] for group in report["audience"]["groups"])
        result["audience_groups_published"]+=len(report["audience"]["steam_histories"]["groups"])
        result["audience_users_by_scope"].append(report["audience"]["gametrack_users"])
    confirm_read_only(db)
    result["result"]="verified" if result["scopes_using_review_model"] and result["affinity"]["result"]=="verified" else "inconclusive"
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite",type=Path)
    args=parser.parse_args()
    engine=None
    try:
        if args.sqlite:
            from scripts.copy_sqlite_to_postgres import readonly_sqlite_engine
            engine=readonly_sqlite_engine(args.sqlite)
            session=Session(engine,autoflush=False)
        else:
            session=SessionLocal()
        with session as db:
            if not args.sqlite:
                begin_read_only(db)
            try:
                result=inspect(db)
            finally:
                db.rollback()
    except Exception as exc:
        # Nunca volcar URLs, parámetros SQL, reseñas o identidades en logs.
        print(json.dumps(dict(result="error",error_type=type(exc).__name__)))
        return 2
    finally:
        if engine:
            engine.dispose()
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
