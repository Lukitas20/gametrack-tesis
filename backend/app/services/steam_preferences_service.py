"""Gustos iniciales a partir de los juegos más jugados de Steam."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from math import log1p
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import Game, Genre, UserRole
from app.services import steam_service
from app.services.user_service import replace_preferences

MAX_PLAYED_GAMES = 20
MAX_GENRES = 4
MAX_METADATA_LOOKUPS = 5
METADATA_RETRY_SECONDS = 300
IGNORED_GENRES = {"free-to-play", "free-to-play-gratis", "gratuito", "gratis", "early-access",
                  "acceso-anticipado", "utilidades", "utilities", "software", "audio-production",
                  "produccion-de-audio", "video-production", "produccion-de-video"}


def fetch_game_genres(appids):
    """Completa pocas fichas faltantes, sin importar reseñas ni generar notas."""
    def fetch(appid):
        try:
            response = steam_service._http_client().get(
                f"{settings.STEAM_STORE_BASE}/appdetails",
                params={"appids": appid, "l": "spanish"}, timeout=4.0,
            )
            response.raise_for_status()
            payload = response.json().get(str(appid), {})
            if payload.get("success") is not True or not isinstance(payload.get("data"), dict):
                return appid, None
            data = payload["data"]
            if data.get("type") != "game":
                return appid, []
            names = [item.get("description", "").strip() for item in data.get("genres", []) if isinstance(item, dict)]
            return appid, [steam_service.GENRE_TRANSLATIONS.get(name.lower(), name) for name in names if name]
        except (httpx.HTTPError, ValueError, AttributeError, TypeError):
            return appid, None
    with ThreadPoolExecutor(max_workers=4) as pool:
        return {appid: names for appid, names in pool.map(fetch, appids) if names is not None}


def assign_preferences(db, user, library, *, force=False, refresh=False):
    """Se invoca bajo el lock del perfil. Una edición manual desactiva la inferencia."""
    if user.role != UserRole.PLAYER or library.get("status") != "ok":
        return False
    db.refresh(user, attribute_names=["preferences", "preferences_source"])
    if not force:
        if user.preferences_source == "manual" or (user.preferences and user.preferences_source is None):
            return False
        if user.preferences_source == "steam" and not refresh:
            return False
    played = sorted((game for game in library.get("items", [])
                     if type(game.get("minutes")) is int and game["minutes"] > 0),
                    key=lambda game: (-game["minutes"], game["appid"]))[:MAX_PLAYED_GAMES]
    if not played:
        return False
    appids = [game["appid"] for game in played]
    games = db.scalars(select(Game).where(Game.steam_app_id.in_(appids)).options(selectinload(Game.genres))).all()
    genre_by_app = {game.steam_app_id: [genre for genre in game.genres if genre.slug not in IGNORED_GENRES] for game in games}
    hints = deepcopy(library.get("genre_hints", {"checked_at": 0, "apps": {}}))
    missing = [appid for appid in appids if not genre_by_app.get(appid) and str(appid) not in hints["apps"]][:MAX_METADATA_LOOKUPS]
    now = int(time.time())
    if missing and (force or now - hints["checked_at"] >= METADATA_RETRY_SECONDS):
        hints["apps"].update({str(appid): names for appid, names in fetch_game_genres(missing).items()})
        hints["checked_at"] = now
        library["genre_hints"] = hints
    for appid in appids:
        if not genre_by_app.get(appid):
            genre_by_app[appid] = [steam_service._get_or_create(db, Genre, name)
                                   for name in hints["apps"].get(str(appid), [])
                                   if steam_service.slugify(name) not in IGNORED_GENRES]
    scores = defaultdict(float)
    genres = {}
    for game in played:
        candidates = {genre.id: genre for genre in genre_by_app.get(game["appid"], [])}
        if not candidates:
            continue
        # Atenúa miles de horas en un único título y divide el aporte entre
        # sus géneros, para no favorecer juegos con más categorías.
        weight = log1p(game["minutes"] / 60) / len(candidates)
        for genre in candidates.values():
            scores[genre.id] += weight
            genres[genre.id] = genre
    if not scores:
        return False
    strongest = max(scores.values())
    ranked = sorted(scores, key=lambda genre_id: (-scores[genre_id], genres[genre_id].slug))
    selected = [(genres[genre_id], round(scores[genre_id] / strongest, 4))
                for genre_id in ranked[:MAX_GENRES] if scores[genre_id] >= strongest * 0.2]
    replace_preferences(user, selected)
    user.preferences_source = "steam"
    return True
