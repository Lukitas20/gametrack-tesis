"""Explorar evidencia pública del mismo juego o estudio que el informe."""
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import and_, case, func, select

from app.models import Game, Review
from app.schemas.interaction import ReviewOut


def review_publication_date(review):
    return review.published_at if review.source == "steam" else review.created_at


def explore_reviews(db, user, *, studio=None, game_id=None, search="", aspect=None,
                    sentiment=None, source=None, date_from=None, date_to=None,
                    sort="newest", limit=20, offset=0):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "La fecha inicial debe ser anterior o igual a la final")
    query = select(Review).join(Game, Review.game_id == Game.id)
    if game_id is not None:
        game = db.get(Game, game_id)
        if game is None:
            raise HTTPException(404, "El juego no existe")
        query = query.where(Game.id == game_id)
        if studio and (game.developer or "").strip().lower() != studio.strip().lower():
            raise HTTPException(400, "El juego no pertenece al estudio seleccionado")
    target = (studio or (user.studio if game_id is None else "") or "").strip()
    if target:
        query = query.where(func.lower(func.trim(Game.developer)) == target.lower())
    elif game_id is None:
        raise HTTPException(400, "Elegí un juego o un estudio para explorar las reseñas")
    if search.strip():
        query = query.where(Review.content.icontains(search.strip(), autoescape=True))
    if source:
        query = query.where(Review.source == source)
    if aspect:
        # Una reseña globalmente positiva puede criticar el rendimiento:
        # al elegir un aspecto, el sentimiento corresponde a ese aspecto.
        from app.models.interaction import ReviewAspect
        conditions = [ReviewAspect.aspect == aspect]
        if sentiment:
            conditions.append(ReviewAspect.sentiment == sentiment)
        query = query.where(Review.aspects.any(and_(*conditions)))
    elif sentiment:
        query = query.where(Review.sentiment == sentiment)
    published = case((Review.source == "steam", Review.published_at), else_=Review.created_at)
    undated = db.scalar(select(func.count()).select_from(
        query.where(published.is_(None)).subquery())) or 0
    if date_from:
        query = query.where(published >= datetime.combine(date_from, time.min, timezone.utc))
    if date_to:
        # Límite exclusivo: incluye el día completo, sin depender de la precisión SQL.
        if date_to == date.max:
            raise HTTPException(422, "La fecha final está fuera del rango permitido")
        query = query.where(published < datetime.combine(date_to + timedelta(days=1), time.min, timezone.utc))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    ordering = published.asc().nulls_last() if sort == "oldest" else published.desc().nulls_last()
    if sort == "helpful":
        query = query.order_by(Review.helpful_count.desc())
    rows = list(db.scalars(query.order_by(ordering, Review.id.desc()).limit(limit).offset(offset)))
    game_names = dict(db.execute(select(Game.id, Game.name).where(Game.id.in_({r.game_id for r in rows}))).all())
    items = []
    for review in rows:
        item = ReviewOut.model_validate(review).model_dump()
        item.update(game_name=game_names[review.game_id], publication_date=review_publication_date(review))
        items.append(item)
    return dict(items=items, total=total, limit=limit, offset=offset, undated=undated)
