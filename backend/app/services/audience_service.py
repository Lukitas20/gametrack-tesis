"""Segmentos descriptivos agregados. No estima compradores ni une identidades."""
from sqlalchemy import func, select, union

from app.db.real_data import real_review_filter, synthetic_user_ids
from app.models import (Game, GameList, GameListItem, Genre, PlayFeedback, Rating,
                        Review, User, UserPreference, UserRole, game_genres)

MIN_GROUP = 5


def audience_report(db, *, game_id=None, studio=None):
    games = select(Game.id)
    if game_id is not None:
        games = games.where(Game.id == game_id)
    elif studio and studio.strip():
        games = games.where(func.lower(func.trim(Game.developer)) == studio.strip().lower())
    else:
        raise ValueError("Se requiere un juego o estudio")
    players = select(User.id).where(User.role == UserRole.PLAYER, User.is_active.is_(True),
                                   ~User.id.in_(synthetic_user_ids()))
    ratings = select(Rating.user_id).where(Rating.game_id.in_(games), Rating.user_id.in_(players))
    reviews = select(Review.user_id).where(Review.game_id.in_(games), Review.source == "user",
                                          real_review_filter(), Review.user_id.in_(players))
    feedback = select(PlayFeedback.user_id).where(PlayFeedback.game_id.in_(games), PlayFeedback.user_id.in_(players))
    # Las listas privadas y las bibliotecas Steam privadas no se usan en este panel.
    lists = select(GameList.user_id).join(GameListItem).where(
        GameListItem.game_id.in_(games), GameList.is_public.is_(True), GameList.user_id.in_(players))
    cohort = union(ratings, reviews, feedback, lists).subquery()
    cohort_ids = select(cohort.c.user_id)
    n = db.scalar(select(func.count()).select_from(cohort)) or 0
    groups = []

    def group(key, label, statement, criterion, denominator, source, unit="usuarios"):
        size = db.scalar(select(func.count()).select_from(statement.distinct().subquery())) or 0
        groups.append(dict(key=key, label=label, size=size if size >= MIN_GROUP else None,
                           suppressed=size < MIN_GROUP, criterion=criterion, denominator=denominator,
                           source=source, unit=unit))

    coverage = {}
    for key, statement in (("ratings", ratings), ("reviews", reviews), ("feedback", feedback), ("public_lists", lists)):
        coverage[key] = db.scalar(select(func.count()).select_from(statement.distinct().subquery())) or 0
    preferences = select(UserPreference.user_id).where(UserPreference.user_id.in_(cohort_ids))
    coverage["preferences"] = db.scalar(select(func.count()).select_from(preferences.distinct().subquery())) or 0
    for key, label, condition in (("rating_high", "Valoraron con 4–5 estrellas", Rating.score >= 4),
                                   ("rating_middle", "Valoraron de 3 a menos de 4 estrellas", (Rating.score >= 3) & (Rating.score < 4)),
                                   ("rating_low", "Valoraron con menos de 3 estrellas", Rating.score < 3)):
        group(key, label, ratings.where(condition), "Al menos una nota explícita en ese rango para los juegos analizados.", n, "Valoraciones GameTrack")
    group("feedback", "Registraron una experiencia", feedback,
          "Devolución de uso guardada para algún juego analizado; no implica satisfacción.", n, "Experiencias GameTrack")
    group("public_lists", "Incluyeron juegos en listas públicas", lists,
          "Al menos un juego analizado en una lista pública; no demuestra compra ni intención de comprar.", n, "Listas públicas GameTrack")
    # Géneros del conjunto analizado, separando elección manual de inferencia Steam.
    relevant_genres = select(game_genres.c.genre_id).where(game_genres.c.game_id.in_(games))
    for source, label in (("manual", "Preferencia elegida"), ("steam", "Interés inferido de Steam")):
        rows = db.execute(select(Genre.name, func.count(func.distinct(UserPreference.user_id)))
                          .join(UserPreference).join(User, User.id == UserPreference.user_id)
                          .where(UserPreference.user_id.in_(cohort_ids), Genre.id.in_(relevant_genres),
                                 User.preferences_source == source, UserPreference.weight > 0)
                          .group_by(Genre.id, Genre.name).having(func.count(func.distinct(UserPreference.user_id)) >= MIN_GROUP)
                          .order_by(func.count(func.distinct(UserPreference.user_id)).desc(), Genre.name).limit(20))
        for genre, size in rows:
            groups.append(dict(key=f"preference_{source}_{genre}", label=f"{label}: {genre}", size=size,
                               suppressed=False, criterion="Miembros de la muestra con este género del conjunto analizado en sus preferencias.",
                               denominator=n, source="Preferencias manuales GameTrack" if source == "manual" else "Preferencias inferidas de horas Steam; interés, no satisfacción",
                               unit="usuarios"))
    steam = select(Review.id).where(Review.game_id.in_(games), Review.source == "steam", real_review_filter())
    steam_n = db.scalar(select(func.count()).select_from(steam.subquery())) or 0
    for value, key, label in ((True, "steam_yes", "Reseñas que recomiendan"), (False, "steam_no", "Reseñas que no recomiendan"),
                               (None, "steam_unknown", "Reseñas sin voto guardado")):
        group(key, label, steam.where(Review.is_recommended.is_(value)),
              "Voto declarado por el autor en las reseñas Steam guardadas, en todos los idiomas disponibles.",
              steam_n, "Reseñas públicas importadas de Steam", "reseñas")
    return dict(scope={"game_id": game_id, "studio": studio}, games=db.scalar(select(func.count()).select_from(games.subquery())) or 0,
                gametrack_users=n, steam_reviews=steam_n, coverage=coverage, groups=groups, minimum_group=MIN_GROUP,
                limitations=["Muestra observada de estos juegos; no representa todo el mercado ni predice compradores potenciales.",
                             "GameTrack cuenta usuarios distintos; Steam cuenta reseñas, no cuentas GameTrack. No se cruzan identidades.",
                             "Los grupos pueden superponerse. La cobertura se refiere sólo a usuarios con interacciones en el conjunto analizado.",
                             "Grupos con menos de cinco observaciones no muestran su tamaño. Se muestran hasta veinte géneros por procedencia.",
                             "Sin interacciones suficientes no hay segmentación publicable. Las horas indican interés y no se convierten en estrellas."])
