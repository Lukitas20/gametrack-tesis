"""GameTrackScore 2.2: historial propio primero, respaldo público secundario.

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
from app.services.personal_evidence_service import confidence_for
from app.services.personal_history_service import personal_history, history_affinities, history_reasons, own_history_affinity


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


def score_context(db, user, engine=None):
    engine = engine or get_engine(db)
    ratings = dict(db.execute(select(Rating.game_id, Rating.score).where(Rating.user_id == user.id)).all())
    genres = {pref.genre.slug for pref in user.preferences}
    liked = db.scalars(select(Game).join(Rating).where(Rating.user_id == user.id, Rating.score >= 4)).all()
    target_genres = genres or {genre.slug for game in liked for genre in game.genres}
    values, basis = engine.personal_scores(user.id, list(genres))
    signals, owned, imported_reviews = personal_history(db, user, ratings)
    history_values, used, similarities = history_affinities(engine, signals)
    has_history = history_values is not None or any(gid in engine._game_index and signal["weight"] != 0
                                                  for gid, signal in signals.items())
    if has_history:
        if history_values is None:
            # Una ficha sin etiquetas puede tener horas propias verificadas,
            # pero no permite inferir interés fuerte por otros juegos.
            history_values = values * 0 + .15
        # Los gustos manuales acompañan; los inferidos del mismo historial no
        # se cuentan dos veces. No restringen la exploración a un género amplio.
        if user.preferences_source == "manual" and genres and basis != "popularidad":
            genre_values, _ = engine.personal_scores(None, list(genres))
            history_values = .95 * history_values + .05 * genre_values
        values, basis = history_values, "historial_personal"
        values = values.copy()
        for gid, signal in signals.items():
            index = engine._game_index.get(gid)
            if index is not None:
                values[index] = own_history_affinity(signal)
    personal = has_history or (bool(ratings or genres) and basis != "popularidad")
    steam_used = {gid: signal for gid, signal in used.items() if signal["source"].startswith("steam_")}
    history_games = {game.id: game for game in db.scalars(select(Game).where(Game.id.in_(used))).all()}
    specific_count = sum(bool(engine._community_terms_by_game.get(gid, set()) & engine.personal_specific_terms)
                         for gid in history_games) if used else 0
    return {"ratings": ratings, "genres": genres, "target_genres": target_genres,
            "scores": dict(zip(engine.game_ids, values)) if personal else {}, "basis": basis,
            "history_size": len(set(ratings) | set(used)), "rating_count": len(ratings), "personal": personal,
            "has_positive_history": any(signal["weight"] > 0 for signal in used.values()), "specific_count": specific_count,
            "has_history": has_history, "signals": used, "own_signals": signals, "similarities": similarities, "engine": engine,
            "history_games": history_games,
            "feedback_count":sum(signal['source']=='play_feedback' for signal in used.values()),
            "gametrack_review_count":sum(signal["source"]=="gametrack_review" for signal in used.values()),
            "steam_count": len(steam_used), "steam_review_count": sum(s["source"] == "steam_review" for s in steam_used.values()),
            "steam_played_count": sum(s["source"] == "steam_playtime" for s in steam_used.values()),
            "imported_review_count": imported_reviews, "owned": owned}


def score_game(game, context):
    public = public_evidence(game)
    metadata = dict(metascore=public["meta"], community=round(public["community"]*100) if public["community"] is not None else None,
                    review_count=public["total"], **confidence_for(game, context))
    own_rating = context["ratings"].get(game.id)
    own_signal = context.get("own_signals", context["signals"]).get(game.id)
    value = (own_rating - 1) / 4 if own_rating is not None else own_history_affinity(own_signal) if own_signal else context["scores"].get(game.id)
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
    if own_signal and own_signal["source"] == "steam_playtime":
        strength = "fuerte de interés sostenido" if value >= .65 else "moderada de interés" if value >= .45 else "inicial de interés"
        reasons.append(f"Jugaste {own_signal['minutes'] / 60:.1f} h a este juego en Steam: es una señal {strength}. Las horas pesan de forma gradual y con un límite; no se convierten en estrellas.")
    elif own_signal and own_signal["source"] == "gametrack_review":
        reasons.append("Tu reseña de GameTrack recomienda este juego; aprendemos de esa opinión explícita." if own_signal["recommended"] else "Tu reseña de GameTrack no recomienda este juego; ese rechazo prevalece sobre el tiempo jugado.")
    elif own_signal and own_signal["source"] == "play_feedback":
        reasons.append("Tu respuesta después de jugar indica que te gustó; priorizamos esa experiencia sobre las horas." if own_signal["weight"] > 0 else "Tu respuesta después de jugar indica que no te gustó; las horas no anulan esa opinión.")
    matches = [genre.name for genre in game.genres if genre.slug in context["genres"]]
    value = max(0., min(1., float(value)))
    if context["genres"] and not context["has_history"]:
        value = .85*value + .15 if matches else .65*value
    if not context["has_history"]:
        value = min(.55, value)  # Géneros solos no acreditan afinidad fuerte.
    reasons.extend(item[3] for item in history_reasons(game, context))
    if matches and not context["has_history"]:
        reasons.append("Coincidencia amplia de géneros: " + ", ".join(matches[:3]) + ". Es una señal provisional, no demuestra tu gusto por este juego.")
    count = context["history_size"]
    if context["steam_count"]:
        reasons.append(f"Usa {context['steam_review_count']} reseñas tuyas de Steam y {context['steam_played_count']} juegos con horas comparables en el catálogo. Las horas indican interés; tus reseñas expresan tu opinión.")
        if context["imported_review_count"] > context["steam_review_count"]:
            reasons.append("Algunas reseñas importadas aún no tienen una ficha comparable, o ya cuentan con tu nota de GameTrack; no duplicamos esas opiniones.")
    if context["rating_count"]:
        reasons.append(f"Tiene en cuenta tus {context['rating_count']} valoraciones de GameTrack, incluidas las negativas.")
    if context.get("gametrack_review_count"):
        reasons.append(f"También usa {context['gametrack_review_count']} reseñas propias de GameTrack. No utiliza opiniones de otras personas como si fueran tuyas.")
    if context.get('feedback_count'):
        reasons.append(f"También usa {context['feedback_count']} devoluciones propias después de jugar, sin convertir interrupciones en rechazos.")
    if context["has_history"] and not context["specific_count"]:
        reasons.append("A los juegos comparados les faltan etiquetas específicas: la estimación sigue siendo limitada aunque tu historial esté cargado.")
    elif context["has_history"] and not (context["engine"]._community_terms_by_game.get(game.id, set()) & context["engine"].personal_specific_terms):
        reasons.append("A esta ficha le faltan etiquetas específicas; el historial está cargado, pero la comparación con este juego es limitada.")
    components = {"affinity": value}
    if public["meta"] is not None:
        components["metacritic"] = public["meta"]/100
    if public["community"] is not None:
        components.update(community=public["community"])
        weights = {"affinity": .85, "metacritic": .10, "community": .05} if public["meta"] is not None else {"affinity": .95, "community": .05}
    else:
        weights = {"affinity": .90, "metacritic": .10} if public["meta"] is not None else {"affinity": 1.}
        reasons.append("Faltan reseñas públicas verificables; no se recomienda en Descubrí.")
    if public["meta"] is not None:
        reasons.append(f"Metascore {public['meta']}/100: aporta {round(weights['metacritic']*100)}% del índice.")
    else:
        reasons.append("Sin Metascore disponible: usamos tus gustos y las reseñas públicas, sin inventar una nota crítica.")
    if public["total"]:
        reasons.append(f"Recepción pública: {public['total']:,} reseñas de {public['source']}; su cantidad no aumenta tu afinidad.")
    if own_signal and own_signal["source"] == "steam_review":
        reasons.insert(0, "Ya lo recomendaste en Steam; esa opinión forma parte del perfil." if own_signal["recommended"] else "Ya lo marcaste como no recomendado en Steam; esa opinión reduce la afinidad.")
    evidence = "steam" if context["steam_count"] or (own_signal and own_signal["source"].startswith("steam_")) else "amplia" if count >= 10 else "en_desarrollo" if count >= 3 else "inicial"
    if evidence == "inicial":
        reasons.append("Todavía hay poca evidencia personal comparable; los géneros solos dan una estimación provisional.")
    elif context["steam_count"] and not context["steam_review_count"]:
        reasons.append("El historial aporta señales de interés, pero aún no tenemos reseñas tuyas comparables para distinguir qué te gustó y qué no.")
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
        if not context["has_history"] and context["target_genres"] and not ({g.slug for g in game.genres} & context["target_genres"]):
            continue
        multiplayer = bool({tag.slug for tag in game.tags} & multiplayer_tags)
        friend_rating, friend_owns = friend_ratings.get(game.id), game.steam_app_id in owned
        if mode == "friends":
            if not multiplayer or not (friend_owns or (friend_rating is not None and friend_rating >= 4)):
                continue
            if context["ratings"].get(game.id, 5) <= 2 or context["signals"].get(game.id, {}).get("weight", 0) < 0 or (friend_rating is not None and friend_rating <= 2):
                continue
        elif game.id in context["ratings"] or game.id in context["signals"] or game.id in context["owned"]:
            continue
        if mode == "critics" and public["meta"] is None:
            continue
        score = score_game(game, context)
        if context["has_history"] and not context["has_positive_history"] and not context["genres"]:
            continue  # Los rechazos solos no permiten afirmar afinidad positiva.
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
