"""GameTrackScore 1.1: gustos, crítica y evidencia pública en una escala fija.

Las reseñas públicas acreditan alcance, no jugadores únicos ni sesiones juntos.
Los umbrales no se relajan para rellenar una lista sin candidatos adecuados.
"""
from math import isfinite, log10, sqrt
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from app.ml.recommender import get_engine, _recommendable_game_filter
from app.ml.quiz_vocab import COMPANY_FILTERS
from app.models import Game, Rating
from app.schemas.game import GameSummary
from app.schemas.gametrack_score import DiscoveryItem, DiscoveryResponse, GameTrackScore
from app.services.friendship_service import require_player, resolve_group_members
from app.services import steam_profile_service


def _count(value):
    return value if type(value) is int and value >= 0 else 0


def public_evidence(game):
    total, positive, source = 0, 0, "Steam"
    official = _count(game.steam_total_reviews)
    if official and type(game.steam_positive_reviews) is int and 0 <= game.steam_positive_reviews <= official:
        total, positive = official, game.steam_positive_reviews
    elif all(type(v) is int and v >= 0 for v in (game.steamspy_positive, game.steamspy_negative)):
        positive = game.steamspy_positive
        total = positive + game.steamspy_negative
        source = "Steam vía SteamSpy"
    # Límite inferior de Wilson: una muestra pequeña no equivale a miles de reseñas.
    community = None
    if total:
        p, z = positive / total, 1.96
        community = (p + z*z/(2*total) - z*sqrt(p*(1-p)/total + z*z/(4*total*total))) / (1 + z*z/total)
    meta = game.metacritic if type(game.metacritic) is int and 0 <= game.metacritic <= 100 else None
    return {"total": total, "source": source, "community": community, "meta": meta,
            "owners": _count(game.steamspy_owners), "reach": min(1., log10(total + 1)/6)}


def reputable(game, public):
    if not game.is_enriched or not game.background_image or public["community"] is None:
        return False
    n, owners, quality, meta = public["total"], public["owners"], public["community"], public["meta"]
    if meta is not None:
        return meta >= 70 and quality >= .65 and (n >= 1000 or (owners >= 50000 and n >= 200))
    return quality >= .80 and (n >= 5000 or (owners >= 100000 and n >= 1000))


def score_context(db, user):
    engine = get_engine(db)
    ratings = dict(db.execute(select(Rating.game_id, Rating.score).where(Rating.user_id == user.id)).all())
    genres = {pref.genre.slug for pref in user.preferences}
    liked = db.scalars(select(Game).join(Rating).where(Rating.user_id == user.id, Rating.score >= 4)).all()
    target_genres = genres or {genre.slug for game in liked for genre in game.genres}
    values, basis = engine.personal_scores(user.id, list(genres))
    personal = bool(ratings or genres) and basis != "popularidad"
    return {"ratings": ratings, "genres": genres, "target_genres": target_genres,
            "scores": dict(zip(engine.game_ids, values)) if personal else {}, "basis": basis,
            "history_size": len(ratings), "personal": personal}


def score_game(game, context):
    public = public_evidence(game)
    metadata = dict(metascore=public["meta"], community=round(public["community"]*100) if public["community"] is not None else None,
                    review_count=public["total"])
    own_rating = context["ratings"].get(game.id)
    value = (own_rating - 1) / 4 if own_rating is not None else context["scores"].get(game.id)
    if value is None or not isfinite(float(value)):
        return GameTrackScore(evidence="sin_datos", reasons=[
            "Elegí tus géneros o valorá algunos juegos para calcular tu GameTrackScore."
            if not context["personal"] else "Este juego todavía no tiene suficientes rasgos en el catálogo."], **metadata)
    if own_rating is not None:
        return GameTrackScore(value=round(value*100), affinity=round(value*100), evidence="valoracion_propia",
            components={"own_rating": value}, weights={"own_rating": 1.},
            reasons=[f"Ya lo valoraste con {own_rating:g}/5; se conserva tu opinión personal."],
            explanation="Cuando ya valoraste el juego, el índice refleja tu propia opinión.", **metadata)
    reasons = []
    matches = [genre.name for genre in game.genres if genre.slug in context["genres"]]
    value = max(0., min(1., float(value)))
    if context["genres"]:
        value = .85*value + .15 if matches else .65*value
    if matches:
        reasons.append("Coincide con tus géneros: " + ", ".join(matches[:3]) + ".")
    count = context["history_size"]
    if count:
        reasons.append(f"Tiene en cuenta tus {count} valoraciones, incluidas las negativas.")
    components = {"affinity": value}
    if public["meta"] is not None:
        components["metacritic"] = public["meta"]/100
    if public["community"] is not None:
        components.update(community=public["community"], reach=public["reach"])
        weights = {"affinity": .60, "metacritic": .25, "community": .10, "reach": .05} if public["meta"] is not None else {"affinity": .70, "community": .25, "reach": .05}
    else:
        weights = {"affinity": .75, "metacritic": .25} if public["meta"] is not None else {"affinity": 1.}
        reasons.append("Faltan reseñas públicas verificables; no se recomienda en Descubrí.")
    if public["meta"] is not None:
        reasons.append(f"Metascore {public['meta']}/100: aporta {round(weights['metacritic']*100)}% del índice.")
    else:
        reasons.append("Sin Metascore disponible: usamos tus gustos y las reseñas públicas, sin inventar una nota crítica.")
    if public["total"]:
        reasons.append(f"Respaldado por {public['total']:,} reseñas de {public['source']}.")
    evidence = "amplia" if count >= 10 else "en_desarrollo" if count >= 3 else "inicial"
    if evidence == "inicial":
        reasons.append("Perfil inicial: se ajustará cuando valores más juegos.")
    return GameTrackScore(value=round(sum(components[key]*weight for key, weight in weights.items())*100),
        affinity=round(value*100), evidence=evidence, reasons=reasons, components=components, weights=weights, **metadata)


def game_score(db, user, game_id):
    require_player(user)
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(404, "El juego no existe")
    return score_game(game, score_context(db, user))


def discovery(db, user, mode="affinity", limit=8, friend_id=None):
    require_player(user)
    if mode not in {"affinity", "critics", "friends"}:
        raise HTTPException(422, "Modo desconocido")
    friend = resolve_group_members(db, user, [friend_id])[1] if friend_id is not None else None
    if mode == "friends" and friend is None:
        raise HTTPException(422, "Elegí un amigo para buscar juegos juntos")
    context = score_context(db, user)
    games = db.scalars(select(Game).where(_recommendable_game_filter(), Game.background_image.is_not(None),
        or_(Game.steam_app_id.is_(None), Game.steam_synced_at.is_not(None)),
        or_(Game.steam_total_reviews >= 1000,
            func.coalesce(Game.steamspy_positive, 0) + func.coalesce(Game.steamspy_negative, 0) >= 1000,
            Game.steamspy_owners >= 50000))).all()
    friend_ratings, owned, library_status, friend_name = {}, set(), None, None
    if mode == "friends":
        friend_name = (friend.steam_username if friend.steam_verified else None) or friend.full_name or friend.username
        friend_ratings = dict(db.execute(select(Rating.game_id, Rating.score).where(Rating.user_id == friend.id)).all())
        # Sólo la biblioteca pública actual del amigo autorizado; nunca caché privada ni horas.
        library = steam_profile_service.fetch_library(friend.steam_id) if friend.steam_verified else {"status": "not_linked"}
        library_status = library["status"]
        if library_status == "ok":
            owned = {item["appid"] for item in library.get("items", [])}
    multiplayer_tags = COMPANY_FILTERS["amigos"] | COMPANY_FILTERS["en-linea"]
    ranked = []
    for game in games:
        public = public_evidence(game)
        if not reputable(game, public):
            continue
        if context["target_genres"] and not ({g.slug for g in game.genres} & context["target_genres"]):
            continue
        multiplayer = bool({tag.slug for tag in game.tags} & multiplayer_tags)
        friend_rating, friend_owns = friend_ratings.get(game.id), game.steam_app_id in owned
        if mode == "friends":
            if not multiplayer or not (friend_owns or (friend_rating is not None and friend_rating >= 4)):
                continue
            if context["ratings"].get(game.id, 5) <= 2 or (friend_rating is not None and friend_rating <= 2):
                continue
        elif game.id in context["ratings"]:
            continue
        if mode == "critics" and public["meta"] is None:
            continue
        score = score_game(game, context)
        if context["personal"] and (score.affinity is None or score.affinity < 45):
            continue
        quality = .7*public["meta"]/100 + .3*public["community"] if public["meta"] is not None else public["community"]
        base = score.value/100 if score.value is not None else .95*quality + .05*public["reach"]
        reasons = []
        if mode == "critics":
            rank = .8*base + .2*public["meta"]/100
            reasons.append("Prioriza la crítica entre juegos afines y con respaldo público.")
        elif mode == "friends":
            social = .6*int(friend_owns) + .4*((friend_rating - 1)/4 if friend_rating is not None else 0)
            rank = .85*base + .15*social
            if friend_owns: reasons.append(f"{friend_name} lo tiene en su biblioteca pública de Steam.")
            if friend_rating is not None: reasons.append(f"{friend_name} lo valoró con {friend_rating:g}/5 en GameTrack.")
            reasons.append("Tiene una modalidad multijugador; revisá compatibilidad y plataformas en su ficha.")
        else:
            rank = base
        if score.value is None:
            reasons.append("Sugerencia general por crítica y reseñas; todavía no es personalizada.")
        ranked.append((rank, public["total"], game.id, (game, score, reasons, friend_owns, friend_rating, multiplayer)))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    note = "Tus gustos tienen el mayor peso. Sólo sugerimos fichas completas con suficiente respaldo público; Metacritic influye cuando está disponible."
    if not context["personal"]:
        note = "Elegí géneros o valorá juegos para personalizar. Mientras tanto, mostramos títulos con buena crítica y suficientes reseñas públicas."
    if mode == "critics": note += " En esta vista todos tienen Metascore."
    if mode == "friends" and library_status != "ok":
        note += " Sin biblioteca pública disponible: usamos las valoraciones de GameTrack de tu amigo."
    if not ranked:
        note += " No hay juegos que cumplan estos criterios; no rellenamos con títulos al azar."
    return DiscoveryResponse(mode=mode, items=[DiscoveryItem(game=GameSummary.model_validate(game), gametrack_score=score,
        reasons=reasons, friend_owns=owns, friend_rating=rating, multiplayer=multi)
        for _, _, _, (game, score, reasons, owns, rating, multi) in ranked[:limit]], note=note,
        friend_name=friend_name, library_status=library_status, history_size=context["history_size"], personal_data=context["personal"])
