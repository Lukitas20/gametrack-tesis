"""GameTrackScore v1: escala fija, explicable y sin popularidad disfrazada de gustos.

La afinidad reutiliza el perfil personal del motor (notas, géneros y modelo
local validado). Metascore sólo modifica la selección, nunca el score personal.
Las horas de Steam no se convierten en opiniones ni sesiones compartidas.
"""
from math import isfinite
from fastapi import HTTPException
from sqlalchemy import select
from app.ml.recommender import get_engine, _recommendable_game_filter
from app.ml.quiz_vocab import COMPANY_FILTERS
from app.models import Game, Rating
from app.schemas.game import GameSummary
from app.schemas.gametrack_score import DiscoveryItem, DiscoveryResponse, GameTrackScore
from app.services.friendship_service import require_player, resolve_group_members
from app.services import steam_profile_service


def score_context(db, user):
    engine = get_engine(db)
    ratings = dict(db.execute(select(Rating.game_id, Rating.score).where(Rating.user_id == user.id)).all())
    genres = [pref.genre.slug for pref in user.preferences]
    values, basis = engine.personal_scores(user.id, genres)
    # Un perfil sin notas ni preferencias no recibe un número neutral ficticio.
    personal = bool(ratings or genres) and basis != "popularidad"
    scores = dict(zip(engine.game_ids, values)) if personal else {}
    return {"ratings": ratings, "genres": set(genres), "scores": scores, "basis": basis,
            "history_size": len(ratings), "personal": personal}


def score_game(game, context):
    own_rating = context["ratings"].get(game.id)
    value = (own_rating - 1) / 4 if own_rating is not None else context["scores"].get(game.id)
    if value is None or not isfinite(float(value)):
        return GameTrackScore(evidence="sin_datos", reasons=[
            "Elegí tus géneros o valorá algunos juegos para calcular tu afinidad."
            if not context["personal"] else "Este juego todavía no tiene suficientes rasgos en el catálogo."])
    reasons = []
    if own_rating is not None:
        evidence = "valoracion_propia"
        reasons.append(f"Ya lo valoraste con {own_rating:g}/5; el índice refleja esa valoración.")
    else:
        count = context["history_size"]
        evidence = "amplia" if count >= 10 else "en_desarrollo" if count >= 3 else "inicial"
        matches = [genre.name for genre in game.genres if genre.slug in context["genres"]]
        if matches:
            reasons.append("Coincide con tus géneros: " + ", ".join(matches[:3]) + ".")
        if count:
            reasons.append(f"Compara géneros y etiquetas con tus {count} valoraciones, incluidas las negativas.")
        elif not matches:
            reasons.append("Compara este juego con los géneros que elegiste.")
        if context["basis"] == "ia_local":
            reasons.append("Incluye patrones del modelo local cuando hay evidencia disponible.")
        if evidence == "inicial":
            reasons.append("Perfil inicial: el índice se ajustará cuando valores más juegos.")
    return GameTrackScore(value=max(0, min(100, round(float(value) * 100))), evidence=evidence, reasons=reasons)


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
    friend = None
    if friend_id is not None:
        friend = resolve_group_members(db, user, [friend_id])[1]
    if mode == "friends" and friend is None:
        raise HTTPException(422, "Elegí un amigo para buscar juegos juntos")
    context = score_context(db, user)
    # El universo del motor ya excluye fichas sin rasgos y DLC/software.
    games = list(db.scalars(select(Game).where(_recommendable_game_filter())))
    friend_ratings, owned, library_status = {}, set(), None
    friend_name = None
    if mode == "friends":
        friend_name = (friend.steam_username if friend.steam_verified else None) or friend.full_name or friend.username
        friend_ratings = dict(db.execute(select(Rating.game_id, Rating.score).where(Rating.user_id == friend.id)).all())
        # Consulta pública actual y acotada a UN amigo verificado. Nunca lee
        # la caché privada del perfil ajeno ni devuelve sus horas de juego.
        library = steam_profile_service.fetch_library(friend.steam_id) if friend.steam_verified else {"status": "not_linked"}
        library_status = library["status"]
        if library_status == "ok":
            owned = {item["appid"] for item in library.get("items", [])}
    multiplayer_tags = COMPANY_FILTERS["amigos"] | COMPANY_FILTERS["en-linea"]
    ranked = []
    for game in games:
        multiplayer = bool({tag.slug for tag in game.tags} & multiplayer_tags)
        friend_rating = friend_ratings.get(game.id)
        friend_owns = game.steam_app_id in owned
        # En modo social se incluyen títulos ya jugados por vos para repetir
        # una partida juntos, salvo los que calificaste negativamente.
        if mode == "friends":
            if not multiplayer or not (friend_owns or (friend_rating is not None and friend_rating >= 4)):
                continue
            if context["ratings"].get(game.id, 5) <= 2 or (friend_rating is not None and friend_rating <= 2):
                continue
        elif game.id in context["ratings"]:
            continue
        if mode == "critics" and (game.metacritic is None or not 0 <= game.metacritic <= 100):
            continue
        score = score_game(game, context)
        personal = score.value / 100 if score.value is not None else None
        quality = max(0, min(1, (game.popularity_score or 0) / 5))
        reasons = []
        if mode == "critics":
            critical = game.metacritic / 100
            rank = .65 * personal + .35 * critical if personal is not None else critical
            reasons.append(f"Metascore {game.metacritic}/100 según el catálogo de Metacritic.")
            if personal is not None: reasons.append("La selección combina 65% afinidad y 35% crítica.")
        elif mode == "friends":
            social = .6 * int(friend_owns) + .4 * ((friend_rating - 1) / 4 if friend_rating is not None else 0)
            rank = .75 * personal + .25 * social if personal is not None else social
            if friend_owns: reasons.append(f"{friend_name} lo tiene en su biblioteca pública de Steam.")
            if friend_rating is not None: reasons.append(f"{friend_name} lo valoró con {friend_rating:g}/5 en GameTrack.")
            reasons.append("Tiene una modalidad multijugador; revisá compatibilidad y plataformas en su ficha.")
        else:
            rank = personal if personal is not None else quality
            if personal is None: reasons.append("Sugerencia general por valoración; todavía no es personalizada.")
        ranked.append((rank, quality, game.id, (game, score, reasons, friend_owns, friend_rating, multiplayer)))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    note = "GameTrackScore mide afinidad estimada, no calidad ni probabilidad de que te guste."
    if not context["personal"]: note = "Todavía falta tu perfil de gustos. Elegí géneros o valorá juegos para activar tu GameTrackScore."
    if mode == "critics": note += " Sólo aparecen juegos con Metascore disponible; no se inventan notas faltantes."
    if mode == "friends":
        note += " Elegís a tu compañero: no medimos con quién jugás más seguido."
        if library_status != "ok": note += " No pudimos consultar una biblioteca pública de Steam; usamos sus valoraciones de GameTrack disponibles."
    return DiscoveryResponse(mode=mode, items=[DiscoveryItem(game=GameSummary.model_validate(game), gametrack_score=score, reasons=reasons,
            friend_owns=owns, friend_rating=rating, multiplayer=multi)
            for _, _, _, (game, score, reasons, owns, rating, multi) in ranked[:limit]], note=note,
        friend_name=friend_name, library_status=library_status, history_size=context["history_size"], personal_data=context["personal"])
