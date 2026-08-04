"""Consultas sobre el catálogo de juegos."""

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.models import Game, Genre, Review, Tag, game_genres, game_tags

SORT_FIELDS = {
    "rating": Game.popularity_score.desc(),
    "popularidad": Game.ratings_count.desc(),
    "metacritic": Game.metacritic.desc().nulls_last(),
    "nombre": Game.name.asc(),
    "lanzamiento": Game.released.desc().nulls_last(),
}


def get_game(db: Session, game_id: int) -> Game | None:
    return db.get(Game, game_id)


def get_game_by_slug(db: Session, slug: str) -> Game | None:
    return db.scalar(select(Game).where(Game.slug == slug))


def _apply_filters(
    statement: Select,
    search: str | None,
    genre: str | None,
    tag: str | None,
    min_rating: float | None,
    max_playtime: int | None,
) -> Select:
    if search:
        pattern = f"%{search.lower()}%"
        statement = statement.where(
            or_(
                func.lower(Game.name).like(pattern),
                func.lower(Game.developer).like(pattern),
            )
        )
    if genre:
        statement = statement.where(
            Game.id.in_(
                select(game_genres.c.game_id)
                .join(Genre, Genre.id == game_genres.c.genre_id)
                .where(Genre.slug == genre)
            )
        )
    if tag:
        statement = statement.where(
            Game.id.in_(
                select(game_tags.c.game_id)
                .join(Tag, Tag.id == game_tags.c.tag_id)
                .where(Tag.slug == tag)
            )
        )
    if min_rating is not None:
        statement = statement.where(Game.avg_rating >= min_rating)
    if max_playtime is not None:
        # Los juegos sin duración conocida no se descartan: no hay evidencia
        # de que excedan el límite.
        statement = statement.where(
            or_(Game.playtime_hours.is_(None), Game.playtime_hours <= max_playtime)
        )
    return statement


def list_games(
    db: Session,
    search: str | None = None,
    genre: str | None = None,
    tag: str | None = None,
    min_rating: float | None = None,
    max_playtime: int | None = None,
    sort: str = "popularidad",
    limit: int = 20,
    offset: int = 0,
) -> tuple[int, list[Game]]:
    """Listado paginado con búsqueda y filtros.

    Returns:
        El total de coincidencias (para paginar) y la página pedida.
    """
    filters = (search, genre, tag, min_rating, max_playtime)
    base = _apply_filters(select(Game), *filters)
    total = db.scalar(_apply_filters(select(func.count(Game.id)), *filters))
    ordering = SORT_FIELDS.get(sort, SORT_FIELDS["popularidad"])
    games = list(db.scalars(base.order_by(ordering).limit(limit).offset(offset)))
    return total or 0, games


def list_genres(db: Session) -> list[Genre]:
    return list(db.scalars(select(Genre).order_by(Genre.name)))


def list_tags(db: Session, min_games: int = 2) -> list[Tag]:
    """Etiquetas del catálogo, de las más usadas a las menos.

    Se descartan las que aparecen en muy pocos juegos: como filtro de interfaz
    no aportan y llenan la pantalla de opciones que devuelven un solo
    resultado.
    """
    counted = (
        select(Tag, func.count(game_tags.c.game_id).label("total"))
        .join(game_tags, game_tags.c.tag_id == Tag.id)
        .group_by(Tag.id)
        .having(func.count(game_tags.c.game_id) >= min_games)
        .order_by(func.count(game_tags.c.game_id).desc(), Tag.name)
    )
    return [row[0] for row in db.execute(counted).all()]


def list_reviews(db: Session, game_id: int, limit: int = 20, offset: int = 0) -> list[Review]:
    return list(
        db.scalars(
            select(Review)
            .where(Review.game_id == game_id)
            .order_by(Review.helpful_count.desc(), Review.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
