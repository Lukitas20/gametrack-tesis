"""Política de procedencia: excluir demostraciones sin borrar su histórico."""
from sqlalchemy import and_, not_, or_, select

DATA_POLICY = "observed-only-v1"


def synthetic_user_ids():
    from app.models import User, Review
    users, reviews = User.__table__.c, Review.__table__.c
    return select(users.id).where(or_(
        users.username.in_(("jugador.demo", "nuevo.demo")),
        users.bio.like("Perfil de gusto:%"),
        users.id.in_(select(reviews.user_id).where(
            reviews.user_id.is_not(None), reviews.source.in_(("demo", "seed", "synthetic")))),
    ))


def real_review_filter(columns=None):
    from app.models import Review
    r = Review if columns is None else columns
    return or_(r.source == "steam", and_(r.source == "user", r.user_id.is_not(None),
                                        not_(r.user_id.in_(synthetic_user_ids()))))


def install_real_data_policy(statement):
    from sqlalchemy.orm import with_loader_criteria
    from app.models import Rating, Review, ReviewAspect, UserPreference
    from app.models.play_feedback import PlayFeedback
    real_reviews = select(Review.__table__.c.id).where(real_review_filter(Review.__table__.c))
    return statement.options(
        with_loader_criteria(Rating, not_(Rating.user_id.in_(synthetic_user_ids())), include_aliases=True),
        with_loader_criteria(UserPreference, not_(UserPreference.user_id.in_(synthetic_user_ids())), include_aliases=True),
        with_loader_criteria(PlayFeedback, not_(PlayFeedback.user_id.in_(synthetic_user_ids())), include_aliases=True),
        with_loader_criteria(Review, real_review_filter(), include_aliases=True),
        with_loader_criteria(ReviewAspect, ReviewAspect.review_id.in_(real_reviews), include_aliases=True),
    )


def is_synthetic_profile(user):
    return user.username in {"jugador.demo", "nuevo.demo"} or (user.bio or "").startswith("Perfil de gusto:")


def refresh_legacy_aggregates(db):
    """Retira aportes demo de contadores derivados; conserva filas originales."""
    from app.models import Rating, Review
    from app.services.interaction_service import recompute_game_aggregates, recompute_median_review_hours
    ratings, reviews = Rating.__table__.c, Review.__table__.c
    ids = set(db.scalars(select(ratings.game_id).where(ratings.user_id.in_(synthetic_user_ids()))))
    ids.update(db.scalars(select(reviews.game_id).where(or_(
        reviews.source.in_(("demo", "seed", "synthetic")), reviews.user_id.in_(synthetic_user_ids())))))
    for game_id in sorted(ids):
        recompute_game_aggregates(db, game_id)
        recompute_median_review_hours(db, game_id)
    db.commit()
    return len(ids)
