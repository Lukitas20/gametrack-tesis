"""Historial propio ya sincronizado; sin red ni opiniones de otras cuentas."""
from math import log1p
from datetime import timezone

import numpy as np
from sqlalchemy import select

from app.models import Game, SteamProfileCache, PlayFeedback, Rating, Review

SOFTWARE_GENRES = {"software", "utilities", "utilidades", "audio-production", "produccion-de-audio",
                   "video-production", "produccion-de-video", "animation-modeling", "design-illustration",
                   "photo-editing", "software-training", "game-development", "accounting"}


def playtime_strength(minutes):
    """Interés creciente y acotado: 2 h son débiles; 1500 h, fuertes.

    El logaritmo evita que miles de horas crezcan sin límite. El techo .9
    conserva incertidumbre: no convierte tiempo abierto en una opinión.
    """
    if type(minutes) is not int or minutes < 120:
        return 0.
    return .9 * min(1., log1p(minutes / 60) / log1p(1500))


def own_history_affinity(signal):
    """La experiencia en el propio juego no depende de otros géneros.

    Una opinión explícita sustituye el interés por horas en personal_history;
    por eso ni siquiera 1500 h pueden levantar un rechazo propio.
    """
    if signal["source"] == "steam_playtime":
        return .15 + .85 * playtime_strength(signal["minutes"])
    return .5 + .5 * signal["weight"]


def personal_history(db, user, ratings):
    signals = {gid: {"source": "rating", "weight": (score - 3) / 2,
                     "rating": score} for gid, score in ratings.items()}
    rating_times={gid:(updated or created) for gid,updated,created in db.execute(
        select(Rating.game_id,Rating.updated_at,Rating.created_at).where(Rating.user_id==user.id))}
    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value
    for feedback in db.scalars(select(PlayFeedback).where(PlayFeedback.user_id==user.id,PlayFeedback.taste_at.is_not(None))):
        rated_at=rating_times.get(feedback.game_id)
        if rated_at is None or utc(feedback.taste_at)>utc(rated_at):
            signals[feedback.game_id]={'source':'play_feedback','weight':feedback.taste_weight or 0.}
    # Aprender sólo de la recomendación explícita del autor. El sentimiento
    # automático del texto nunca se transforma en una valoración inventada.
    for review in db.scalars(select(Review).where(Review.user_id==user.id, Review.source=="user", Review.is_recommended.is_not(None))):
        if review.game_id not in signals:
            signals[review.game_id]={"source":"gametrack_review", "weight":1. if review.is_recommended else -1.,
                                     "recommended":review.is_recommended, "review_id":review.id}
    owned, reviews = {}, {}
    cache = db.get(SteamProfileCache, user.id) if user.steam_verified else None
    if cache and cache.steam_id == user.steam_id:
        library = cache.library or {}
        if library.get("status") == "ok":
            owned = {item["appid"]: item for item in library.get("items", [])}
        # Las páginas privadas/no disponibles no se convierten en evidencia.
        for page in library.get("user_reviews", {}).values():
            if page.get("status") == "ok":
                for item in page.get("items", []):
                    if type(item.get("is_recommended")) is bool:
                        reviews[item["appid"]] = item
    appids = set(owned) | set(reviews)
    games = []
    appids = sorted(appids)
    for start in range(0, len(appids), 500):
        games.extend(db.scalars(select(Game).where(Game.steam_app_id.in_(appids[start:start + 500]))).all())
    by_app = {game.steam_app_id: game for game in games
              if not ({genre.slug for genre in game.genres} & SOFTWARE_GENRES)}
    for appid, review in reviews.items():
        game = by_app.get(appid)
        if game and game.id not in signals:
            signals[game.id] = {"source": "steam_review", "weight": 1. if review["is_recommended"] else -1.,
                               "recommended": review["is_recommended"], "appid": appid}
    played = [item for item in owned.values() if type(item.get("minutes")) is int and item["minutes"] >= 120
              and item["appid"] in by_app and by_app[item["appid"]].id not in signals]
    played.sort(key=lambda item: (-item["minutes"], item["appid"]))
    # Conserva evidencia propia de toda la biblioteca; sólo las comparaciones
    # se limitan a 40 juegos para evitar una matriz innecesariamente grande.
    for item in played:
        game = by_app[item["appid"]]
        signals[game.id] = {"source": "steam_playtime", "minutes": item["minutes"], "appid": item["appid"],
                           "weight": playtime_strength(item["minutes"])}
    for appid, item in owned.items():
        game = by_app.get(appid)
        if game and game.id in signals and type(item.get("minutes")) is int and item["minutes"] >= 0:
            signals[game.id]["minutes"] = item["minutes"]
    return signals, {game.id for game in games if game.steam_app_id in owned}, len(reviews)


def history_affinities(engine, signals):
    explicit = {gid: s for gid, s in signals.items() if s["source"] != "steam_playtime" and s["weight"] != 0}
    played = sorted(((gid, s) for gid, s in signals.items() if s["source"] == "steam_playtime"
                     and gid in engine._game_index), key=lambda item: (-item[1]["minutes"], item[0]))[:40]
    comparable = {**explicit, **dict(played)}
    similarities = engine.personal_history_similarities(list(comparable))
    return combine_history_affinities(similarities, comparable, len(engine.game_ids))


def combine_history_affinities(similarities, signals, candidate_count):
    """Combina opiniones e interés igual para catálogo y candidatos anunciados."""
    used = {gid: signal for gid, signal in signals.items() if gid in similarities and signal["weight"] != 0}
    if not used:
        return None, {}, {}
    # Opiniones explícitas e interés por tiempo son canales distintos. Cuarenta
    # juegos con horas no pueden superar una reseña negativa por acumulación.
    explicit = [gid for gid, signal in used.items() if signal["source"] != "steam_playtime" and signal["weight"] > 0]
    implicit = [gid for gid, signal in used.items() if signal["source"] == "steam_playtime"]
    negative = [gid for gid, signal in used.items() if signal["weight"] < 0]

    def average(ids):
        weights = np.array([abs(used[gid]["weight"]) for gid in ids])
        # La media conserva la fuerza absoluta: dos horas no pasan a ser
        # evidencia máxima sólo por ser el único juego del historial.
        return sum(similarities[gid] * weight * weight for gid, weight in zip(ids, weights)) / weights.sum()

    # Máximo + promedio preserva distintos intereses sin depender de un único
    # juego. Los pesos absolutos moderan las notas de 4/5 y las horas escasas.
    def interest(ids):
        return .85 * np.maximum.reduce([similarities[gid] * abs(used[gid]["weight"]) for gid in ids]) + .15 * average(ids)

    positive = interest(explicit) if explicit else np.zeros(candidate_count)
    if implicit:
        played = interest(implicit)
        # Una reseña de otro género no apaga el interés por cientos de horas
        # de disparos. Dos canales positivos no se suman ni duplican evidencia.
        positive = np.maximum(positive, played)
    penalty = np.maximum.reduce([similarities[gid] * abs(used[gid]["weight"]) for gid in negative]) if negative else 0.
    baseline = .15 if explicit or implicit else .5
    values = np.clip(baseline + .85 * positive - .75 * penalty, 0., 1.)
    return values, used, similarities


def history_reasons(game, context):
    """Mismas comparaciones que usa la afinidad, con fuentes contrastables."""
    index = context["engine"]._game_index.get(game.id)
    if index is None:
        return []
    comparisons = []
    terms = context["engine"]._community_terms_by_game.get(game.id, set())
    specific = {tag.slug: tag.name for tag in game.tags if tag.slug in terms
                and tag.slug in getattr(context["engine"], "personal_specific_terms", set())}
    for gid, signal in context["signals"].items():
        if gid == game.id:
            continue
        similarity = float(context["similarities"][gid][index])
        previous = context["history_games"].get(gid)
        if previous is None or similarity < .12:
            continue
        previous_terms = context["engine"]._community_terms_by_game.get(gid, set())
        shared = [specific[tag.slug] for tag in previous.tags if tag.slug in specific and tag.slug in previous_terms]
        # Géneros amplios solos nunca se presentan como comparación fuerte.
        if not shared:
            continue
        if signal["source"] == "rating":
            opinion = f"valoraste {signal['rating']:g}/5 en GameTrack"
        elif signal["source"] == "gametrack_review":
            opinion = "recomendaste en tu reseña de GameTrack" if signal["recommended"] else "no recomendaste en tu reseña de GameTrack"
        elif signal["source"] == "steam_review":
            opinion = "recomendaste en Steam" if signal["recommended"] else "no recomendaste en Steam"
        elif signal["source"] == "play_feedback":
            opinion = "marcaste como una experiencia que te gustó" if signal['weight']>0 else "marcaste como una experiencia que no te gustó"
        else:
            opinion = f"jugaste {signal['minutes'] / 60:.1f} h en Steam (interés, no una valoración)"
        text = f"Comparte {', '.join(shared[:3])} con {previous.name}, que {opinion}."
        comparisons.append((similarity * abs(signal["weight"]), gid, signal["weight"] < 0, text))
    comparisons.sort(key=lambda item: (-item[0], item[1]))
    # Incluye al menos una señal negativa si existe, aunque haya muchos positivos.
    return ([item for item in comparisons if not item[2]][:2]
            + [item for item in comparisons if item[2]][:1])
