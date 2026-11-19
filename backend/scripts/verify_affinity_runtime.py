"""Read-only production check using existing real profiles, without network.

Run explicitly in a Cloud Run job using the web image and its existing DB and
model settings. This script does not import app.main, run migrations, train a
model, sync Steam, create accounts or write any data. Its only output is one
aggregate JSON object; no profile IDs, names, hours or database URL are emitted.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import or_, select, text

from app.db.database import SessionLocal
from app.db.real_data import synthetic_user_ids
from app.ml.recommender import get_engine
from app.models import (
    Game, PlayFeedback, Rating, Review, SteamIdentity, User, UserPreference, UserRole,
)
from app.services.gametrack_score_service import compatibility_score, score_context, score_game
from app.services.personal_history_service import own_history_affinity
from app.services.steam_affinity_service import load_affinity_model

MAX_PROFILES = 5
CANDIDATES_PER_PROFILE = 50
OWN_HISTORY_CHECKS_PER_PROFILE = 20


class VerificationError(RuntimeError):
    """Static error codes only: never wrap driver errors or personal values."""


def begin_read_only(db):
    """This must be the first SQL statement of the production transaction."""
    bind = db.get_bind()
    # Importing the configured SQLAlchemy engine does not open a connection.
    # Refuse SQLite before connecting (its normal connect hook enables WAL).
    if bind.dialect.name != "postgresql":
        raise VerificationError("production_check_requires_postgresql")
    bind.echo = False
    db.autoflush = False
    db.execute(text("SET TRANSACTION READ ONLY"))


def confirm_read_only(db):
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        enabled = str(db.scalar(text("SHOW transaction_read_only"))).lower() == "on"
    elif dialect == "sqlite":
        # Only for an already prepared isolated test DB. main() never permits
        # SQLite; this does not switch an existing connection's write mode.
        enabled = db.scalar(text("PRAGMA query_only")) == 1
    else:
        enabled = False
    if not enabled:
        raise VerificationError("read_only_transaction_not_confirmed")
    if db.new or db.dirty or db.deleted:
        raise VerificationError("session_contains_pending_writes")


def selected_profiles(db, limit=MAX_PROFILES):
    """Deterministic sample, selected without evaluating model outputs."""
    if type(limit) is not int or not 1 <= limit <= MAX_PROFILES:
        raise VerificationError("profile_limit_out_of_range")
    verified_steam = select(SteamIdentity.user_id).where(
        SteamIdentity.user_id == User.id, SteamIdentity.steam_id == User.steam_id).exists()
    personal_signal = or_(
        verified_steam,
        select(UserPreference.id).where(UserPreference.user_id == User.id).exists(),
        select(Rating.id).where(Rating.user_id == User.id).exists(),
        select(Review.id).where(Review.user_id == User.id, Review.source == "user",
                                Review.is_recommended.is_not(None)).exists(),
        select(PlayFeedback.id).where(PlayFeedback.user_id == User.id,
                                      PlayFeedback.taste_at.is_not(None)).exists(),
    )
    return list(db.scalars(select(User).where(
        User.is_active.is_(True), User.role == UserRole.PLAYER,
        ~User.id.in_(synthetic_user_ids()), personal_signal,
    ).order_by(User.id).limit(limit)))


def validate_score(score, *, own_rating=None, own_signal=None):
    if score.value is None:
        if score.weights or score.components or score.learning.used:
            raise VerificationError("score_without_evidence_has_components")
        return
    if (not score.weights or abs(sum(score.weights.values()) - 1.) > 1e-7
            or any(type(weight) not in (int, float) or not math.isfinite(weight) or not 0 <= weight <= 1
                   for weight in score.weights.values())
            or any(key not in score.components or not math.isfinite(score.components[key])
                   or not 0 <= score.components[key] <= 1 for key in score.weights)):
        raise VerificationError("invalid_component_weights")
    expected_base = round(sum(score.components[key] * weight for key, weight in score.weights.items()) * 100)
    actual_base = score.base_value if score.base_value is not None else score.value
    if actual_base != expected_base:
        raise VerificationError("base_value_disagrees_with_components")
    expected_display = compatibility_score(expected_base) if score.calibration == "compatibility" else expected_base
    if score.value != expected_display:
        raise VerificationError("displayed_score_disagrees_with_calibration")
    local_weight = score.weights.get("steam_affinity", 0.)
    personal_weight = score.weights.get("affinity", 0.) + local_weight
    if (local_weight > .25 + 1e-9 or local_weight > .25 * personal_weight + 1e-9
            or score.learning.used != (local_weight > 0)
            or abs(score.learning.weight - local_weight) > 1e-9):
        raise VerificationError("invalid_learning_contribution")
    if own_rating is not None:
        if (score.value != round((own_rating - 1) / 4 * 100)
                or score.weights != {"own_rating": 1.} or score.learning.used):
            raise VerificationError("own_rating_was_not_preserved")
    elif own_signal is not None:
        if (score.affinity != round(own_history_affinity(own_signal) * 100)
                or local_weight or score.learning.used):
            raise VerificationError("own_experience_was_not_preserved")


def inspect_read_only_session(db, *, limit=MAX_PROFILES):
    """Read-only core; callers must already have enabled DB-enforced read only."""
    confirm_read_only(db)
    model, status = load_affinity_model()
    model_status = status.get("status", "invalid")
    if model is None or model_status in {"invalid", "missing"}:
        raise VerificationError("model_missing_or_invalid")
    version = str(status.get("model_version", "unknown"))
    version = version if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", version) else "unrecognized"
    result = dict(
        result="inconclusive", read_only=True, external_http_calls=0,
        model_status=model_status, model_version=version,
        profiles_sampled=0, profiles_with_comparable_history=0,
        profiles_with_model_contribution=0, candidates_checked=0,
        candidates_with_model_contribution=0, own_rating_checks=0,
        own_history_checks=0, integrity_failures=0,
    )
    users = selected_profiles(db, limit)
    result["profiles_sampled"] = len(users)
    if not users:
        result["reason"] = "no_existing_real_profiles_in_deterministic_sample"
        return result
    engine = get_engine(db)
    for user in users:
        context = score_context(db, user, engine=engine)
        history = context.get("affinity_status", {}).get("history_examples", 0)
        result["profiles_with_comparable_history"] += int(history > 0)
        excluded = set(context["ratings"]) | set(context.get("own_signals", {})) | set(context.get("owned", set()))
        # The same first bounded catalog slice is examined for every profile;
        # no adaptive search for games whose model result looks favourable.
        candidate_ids = [gid for gid in engine.game_ids if gid not in excluded][:CANDIDATES_PER_PROFILE]
        candidates = list(db.scalars(select(Game).where(Game.id.in_(candidate_ids)).order_by(Game.id))) if candidate_ids else []
        contributed = False
        for game in candidates:
            score = score_game(game, context)
            validate_score(score)
            result["candidates_checked"] += 1
            used = score.weights.get("steam_affinity", 0.) > 0
            result["candidates_with_model_contribution"] += int(used)
            contributed |= used
        result["profiles_with_model_contribution"] += int(contributed)
        rated_ids = sorted(context["ratings"])
        for start in range(0, len(rated_ids), 500):
            for game in db.scalars(select(Game).where(Game.id.in_(rated_ids[start:start + 500]))):
                validate_score(score_game(game, context), own_rating=context["ratings"][game.id])
                result["own_rating_checks"] += 1
        own_ids = sorted(set(context.get("own_signals", {})) - set(rated_ids))[:OWN_HISTORY_CHECKS_PER_PROFILE]
        if own_ids:
            for game in db.scalars(select(Game).where(Game.id.in_(own_ids))):
                validate_score(score_game(game, context), own_signal=context["own_signals"][game.id])
                result["own_history_checks"] += 1
    confirm_read_only(db)
    if model_status != "ready":
        result["reason"] = "model_has_no_validated_head"
    elif not result["profiles_with_comparable_history"]:
        result["reason"] = "no_comparable_history_in_existing_sample"
    elif not result["candidates_checked"]:
        result["reason"] = "no_unplayed_candidates_in_bounded_sample"
    elif not result["candidates_with_model_contribution"]:
        result["reason"] = "model_abstained_for_bounded_existing_sample"
    else:
        result["result"] = "verified"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-profiles", type=int, choices=range(1, MAX_PROFILES + 1), default=MAX_PROFILES)
    args = parser.parse_args()
    try:
        with SessionLocal() as db:
            begin_read_only(db)
            try:
                result = inspect_read_only_session(db, limit=args.max_profiles)
            finally:
                db.rollback()
    except Exception as exc:
        # Deliberately omit exception messages/tracebacks: SQL drivers can
        # include credentials, SQL parameters or profile fields in them.
        print(json.dumps({"result": "error", "error_type": type(exc).__name__, "integrity_failures": 1,
                          "error_code": str(exc) if isinstance(exc, VerificationError) else "unexpected_verification_failure"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
