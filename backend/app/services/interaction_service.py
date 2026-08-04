"""Alta y actualización de ratings y reseñas."""

import math

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.ml.analytics import analyze_review, apply_analysis
from app.ml.recommender import UNCERTAINTY_WEIGHT, invalidate_engine
from app.models import Game, Rating, Review, User
from app.schemas.interaction import RatingCreate, ReviewCreate


def recompute_game_aggregates(db: Session, game_id: int) -> None:
    """Recalcula los contadores desnormalizados de un juego.

    Combina dos fuentes de evidencia en vez de que una pise a la otra: las
    valoraciones explícitas de GameTrack (escala 1-5) y el "recomendado / no
    recomendado" de reseñas reales importadas de Steam (traducido a esa misma
    escala). Si una sola pisara a la otra, la primera valoración real de un
    juego con cientos de reseñas de Steam le borraría toda esa evidencia de
    un plumazo — que es justo lo que pasaba antes de este fix.
    """
    game = db.get(Game, game_id)
    if game is None:
        return

    rating_average, rating_count = db.execute(
        select(func.avg(Rating.score), func.count(Rating.id)).where(
            Rating.game_id == game_id
        )
    ).one()
    rating_count = int(rating_count or 0)

    recommended, steam_count = db.execute(
        select(
            func.sum(case((Review.is_recommended.is_(True), 1), else_=0)),
            func.count(Review.id),
        ).where(Review.game_id == game_id, Review.is_recommended.is_not(None))
    ).one()
    steam_count = int(steam_count or 0)
    steam_average = 1 + 4 * (recommended / steam_count) if steam_count else None

    if rating_count and steam_count:
        total = rating_count + steam_count
        game.avg_rating = round(
            (float(rating_average) * rating_count + steam_average * steam_count) / total, 2
        )
        game.ratings_count = total
    elif rating_count:
        game.avg_rating = round(float(rating_average), 2)
        game.ratings_count = rating_count
    elif steam_count:
        game.avg_rating = round(steam_average, 2)
        game.ratings_count = steam_count
    else:
        game.avg_rating = 0.0
        game.ratings_count = 0

    # Mismo criterio que usa el recomendador para su piso de popularidad
    # (ver app/ml/recommender.py): penaliza por incertidumbre en vez de sólo
    # mostrar el promedio crudo, para que el catálogo no ordene "mejor
    # valorados" con dos reseñas perfectas por delante de cien muy buenas.
    game.popularity_score = round(
        game.avg_rating - UNCERTAINTY_WEIGHT / math.sqrt(max(game.ratings_count, 1)), 4
    )

    game.reviews_count = (
        db.scalar(select(func.count(Review.id)).where(Review.game_id == game_id)) or 0
    )


def upsert_rating(db: Session, user: User, data: RatingCreate) -> Rating:
    """Crea o actualiza la valoración de un usuario sobre un juego."""
    rating = db.scalar(
        select(Rating).where(Rating.user_id == user.id, Rating.game_id == data.game_id)
    )
    if rating is None:
        rating = Rating(user_id=user.id, game_id=data.game_id)
        db.add(rating)

    rating.score = data.score
    rating.hours_played = data.hours_played
    rating.status = data.status

    db.flush()
    recompute_game_aggregates(db, data.game_id)
    db.commit()
    db.refresh(rating)

    # Actualizar una valoración existente no cambia la cantidad de filas, así
    # que la huella del motor no lo detectaría: hay que invalidar a mano.
    invalidate_engine()
    return rating


def create_review(db: Session, user: User, data: ReviewCreate) -> Review:
    """Crea una reseña y la analiza en el momento.

    El análisis es sincrónico porque tarda milisegundos y así el usuario ve el
    resultado apenas publica. Con un modelo más pesado convendría encolarlo y
    dejar la reseña con ``is_analyzed = False``, que es justamente para lo que
    existe ese flag.
    """
    review = db.scalar(
        select(Review).where(Review.user_id == user.id, Review.game_id == data.game_id)
    )
    if review is None:
        review = Review(user_id=user.id, game_id=data.game_id)
        db.add(review)

    review.title = data.title
    review.content = data.content
    review.is_recommended = data.is_recommended
    review.hours_at_review = data.hours_at_review
    review.author_name = user.username
    db.flush()

    apply_analysis(db, review, analyze_review(review))
    recompute_game_aggregates(db, data.game_id)
    db.commit()
    db.refresh(review)
    return review


def list_user_ratings(db: Session, user: User) -> list[Rating]:
    return list(
        db.scalars(
            select(Rating)
            .where(Rating.user_id == user.id)
            .order_by(Rating.score.desc(), Rating.created_at.desc())
        )
    )
