"""Consultas sobre el catálogo de juegos."""

from datetime import date
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Game, Genre, Review, ReviewAspect, Tag, game_genres, game_tags

SORT_FIELDS = {
    "rating": Game.popularity_score.desc(),
    "popularidad": Game.ratings_count.desc(),
    "metacritic": Game.metacritic.desc().nulls_last(),
    "nombre": Game.name.asc(),
    "lanzamiento": Game.released.desc().nulls_last(),
}


def visible_catalog_clause():
    """Oculta sólo productos confirmados no-juego, conservando los datos."""
    from app.models.steam_catalog import SteamCatalogEntry
    return ~select(SteamCatalogEntry.appid).where(
        SteamCatalogEntry.appid == Game.steam_app_id,
        SteamCatalogEntry.status == "non_game",
    ).exists()


def _enriched_only(statement: Select) -> Select:
    """Sólo juegos con ficha completa (ver ``Game.is_enriched``).

    Las secciones curadas de la portada muestran géneros, imagen y reseñas:
    una ficha pendiente (sólo AppID + nombre) se vería rota ahí. En el
    catálogo y el buscador sí aparecen, a propósito.
    """
    return statement.where(
        visible_catalog_clause(),
        or_(Game.steam_app_id.is_(None), Game.steam_synced_at.is_not(None)),
        # Importaciones antiguas pueden conservar software como ficha completa.
        Game.id.notin_(select(game_genres.c.game_id).join(Genre).where(Genre.slug.in_(
            ["utilities", "utilidades", "animation-modeling", "animacion-y-modelado",
             "design-illustration", "diseno-e-ilustracion", "photo-editing", "edicion-fotografica",
             "video-production", "produccion-de-video", "software-training", "web-publishing"]
        ))),
    )


def get_game(db: Session, game_id: int) -> Game | None:
    return db.get(Game, game_id)


def _apply_filters(
    statement: Select,
    search: str | None,
    genre: str | None,
    tag: str | None,
    min_rating: float | None,
    max_playtime: int | None,
) -> Select:
    statement = statement.where(visible_catalog_clause())
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


def list_home_section(db: Session, sort: str, limit: int = 8) -> list[Game]:
    """Una fila curada de la portada (populares, mejor valorados, recientes)."""
    ordering = SORT_FIELDS.get(sort, SORT_FIELDS["popularidad"])
    statement = _enriched_only(select(Game)).where(
        or_(Game.released.is_(None), Game.released <= date.today())
    )
    if sort == "metacritic":
        statement = statement.where(Game.metacritic >= 70)
    statement = statement.options(selectinload(Game.genres)).order_by(ordering, Game.id).limit(limit)
    return list(db.scalars(statement))


def list_home_collections(db: Session):
    """Categorías navegables: mismo slug que el filtro de catálogo y cantidades reales."""
    definitions = [
        ("accion", "Acción", "Subí el ritmo", ["accion"]),
        ("aventura", "Aventura", "Un mundo por explorar", ["aventura"]),
        ("rol", "Rol", "Construí tu propia historia", ["rol", "rpg"]),
        ("indie", "Indie", "Ideas con personalidad", ["indie"]),
        ("estrategia", "Estrategia", "Tu siguiente movimiento", ["estrategia"]),
        ("carreras", "Carreras", "En la línea de salida", ["carreras"]),
    ]
    collections = []
    for key, title, caption, slugs in definitions:
        for slug in slugs:
            genre_ids = select(game_genres.c.game_id).join(Genre).where(Genre.slug == slug)
            conditions = [Game.id.in_(genre_ids), or_(Game.released.is_(None), Game.released <= date.today())]
            count = db.scalar(_enriched_only(select(func.count(Game.id))).where(*conditions)) or 0
            if not count:
                continue
            games = list(db.scalars(_enriched_only(select(Game)).where(*conditions)
                .options(selectinload(Game.genres)).order_by(Game.ratings_count.desc(), Game.id).limit(6)))
            collections.append({"key": key, "title": title, "caption": caption, "slug": slug,
                "count": count, "games": games})
            break
    return collections


def list_featured(db: Session, limit: int = 8) -> list[Game]:
    """"Destacados": no hay un campo de "juego destacado" en la base, así que
    el criterio es de facto: mejor valorados entre los que además tienen nota
    de Metacritic (una segunda señal de calidad, no sólo volumen de reseñas
    de Steam). Completa con los más populares si no alcanzan para el límite.
    """
    with_metacritic = list(
        db.scalars(
            _enriched_only(select(Game))
            .where(or_(Game.released.is_(None), Game.released <= date.today()))
            .where(Game.metacritic.is_not(None))
            .order_by(Game.popularity_score.desc())
            .limit(limit)
        )
    )
    if len(with_metacritic) >= limit:
        return with_metacritic

    exclude = [g.id for g in with_metacritic]
    statement = _enriched_only(select(Game)).where(
        or_(Game.released.is_(None), Game.released <= date.today())
    ).order_by(Game.popularity_score.desc())
    if exclude:
        statement = statement.where(Game.id.notin_(exclude))
    fallback = list(db.scalars(statement.limit(limit - len(with_metacritic))))
    return with_metacritic + fallback


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
    """Resenas de un juego, con las mas informativas primero.

    El primer criterio es si el modulo ABSA encontro algun aspecto en la
    resena. No es un adorno: `helpful_count` viene en cero en el 71 % del
    corpus importado de Steam, asi que ordenar solo por ese campo degrada casi
    de inmediato a "las mas nuevas", y arriba terminan los comentarios de tres
    palabras ("es de disparo") en lugar de los que dicen algo del juego.

    Poner adelante lo que el analisis pudo leer alinea las dos cosas que
    queremos: al lector le sirve mas, y la evidencia que respalda los puntajes
    por aspecto queda a la vista en vez de enterrada.
    """
    aspectos = (
        select(func.count(ReviewAspect.id))
        .where(ReviewAspect.review_id == Review.id)
        .correlate(Review)
        .scalar_subquery()
    )
    return list(
        db.scalars(
            select(Review)
            .where(Review.game_id == game_id, Review.source.in_(["user", "steam"]))
            .order_by(
                (aspectos > 0).desc(),
                Review.helpful_count.desc(),
                Review.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
