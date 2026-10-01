"""Biblioteca y amigos reales de la identidad Steam verificada del usuario."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import lru_cache
import re
from threading import Lock
import time
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models import Game, Rating, SteamIdentity, SteamProfileCache, User, UserRole
from app.services import friendship_service, steam_service

TTL = 300
RETRY_DELAY = 30


@lru_cache(maxsize=1024)
def _user_lock(user_id):
    return Lock()


def _integer(value, default=None):
    return value if type(value) is int and value >= 0 else default


def _image(value):
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        return value if url.scheme == "https" and (host.endswith(".steamstatic.com") or host.endswith(".steamusercontent.com") or host.endswith(".akamaihd.net")) else None
    except ValueError:
        return None


def _request(path, params):
    """No se propagan URLs de errores HTTP: podrían contener la API key."""
    if not settings.STEAM_API_KEY:
        return "not_configured", None
    try:
        response = steam_service._http_client().get(
            settings.STEAM_API_BASE + path,
            params={"key": settings.STEAM_API_KEY, **params}, timeout=8.0,
        )
        if response.status_code == 401 and "GetFriendList" in path:
            return "private", None
        if response.status_code in {401, 403}:
            return "not_configured", None
        if response.status_code == 400 and "GetPlayerAchievements" in path:
            body = response.json()
            return ("ok", body) if isinstance(body, dict) else ("unavailable", None)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return "unavailable", None
        return "ok", data
    except (httpx.HTTPError, ValueError):
        return "unavailable", None


def fetch_library(steam_id):
    status, data = _request("/IPlayerService/GetOwnedGames/v1/", {
        "steamid": steam_id, "include_appinfo": 1, "include_played_free_games": 1,
    })
    if status != "ok":
        return {"status": status}
    body = data.get("response")
    if not isinstance(body, dict):
        return {"status": "unavailable"}
    # {} indica datos no compartidos. game_count=0 sí es una biblioteca vacía.
    if "game_count" not in body:
        return {"status": "private"}
    if type(body["game_count"]) is not int or body["game_count"] < 0:
        return {"status": "unavailable"}
    raw_games = body.get("games", [] if body["game_count"] == 0 else None)
    if not isinstance(raw_games, list):
        return {"status": "unavailable"}
    games = {}
    for raw in raw_games:
        if not isinstance(raw, dict) or not _integer(raw.get("appid")):
            return {"status": "unavailable"}
        appid = raw["appid"]
        games[appid] = {
            "appid": appid, "name": str(raw.get("name") or f"Juego de Steam {appid}")[:250],
            "minutes": _integer(raw.get("playtime_forever")),
            "recent_minutes": _integer(raw.get("playtime_2weeks")),
            "last_played": _integer(raw.get("rtime_last_played")),
            "cover": f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/header.jpg",
        }
    if body["game_count"] != len(games):
        return {"status": "unavailable"}
    return {"status": "ok", "items": list(games.values()), "updated_at": int(time.time())}


def fetch_friends(steam_id):
    status, data = _request("/ISteamUser/GetFriendList/v1/", {"steamid": steam_id, "relationship": "friend"})
    if status != "ok":
        return {"status": status}
    body = data.get("friendslist")
    if not isinstance(body, dict) or not isinstance(body.get("friends"), list):
        return {"status": "unavailable"}
    friends = {}
    for raw in body["friends"]:
        if not isinstance(raw, dict) or not re.fullmatch(r"\d{17}", str(raw.get("steamid", ""))):
            return {"status": "unavailable"}
        sid = raw["steamid"]
        if sid == steam_id:
            continue
        friends[sid] = {"steam_id": sid, "name": "Jugador de Steam", "avatar": None,
                        "friend_since": _integer(raw.get("friend_since")), "online": None, "playing": None}
    # Steam limita cada consulta de perfiles a 100 IDs, sin truncar la lista.
    ids = list(friends)
    partial = False
    for start in range(0, len(ids), 100):
        status, profiles = _request("/ISteamUser/GetPlayerSummaries/v2/", {"steamids": ",".join(ids[start:start+100])})
        response_body = (profiles or {}).get("response") if status == "ok" else None
        players = response_body.get("players", []) if isinstance(response_body, dict) else []
        if not isinstance(players, list):
            players = []
        found = set()
        for player in players:
            if not isinstance(player, dict) or player.get("steamid") not in friends:
                continue
            sid = player["steamid"]; found.add(sid)
            friends[sid].update(name=str(player.get("personaname") or "Jugador de Steam")[:100],
                avatar=_image(player.get("avatarfull")), online=player.get("personastate") != 0 if type(player.get("personastate")) is int else None,
                playing=str(player["gameextrainfo"])[:200] if player.get("gameextrainfo") else None)
        partial = partial or len(found) < len(ids[start:start+100])
    return {"status": "ok", "items": list(friends.values()), "updated_at": int(time.time()), "partial_profiles": partial}


def _merge(previous, fresh):
    if fresh["status"] == "unavailable":
        return {**deepcopy(previous or {}), "status": "unavailable"}
    # Al volverse privado, se elimina el detalle cacheado de esa sección.
    return fresh


def sync_profile(db, user, force=False):
    identity = db.scalar(select(SteamIdentity).where(SteamIdentity.user_id == user.id))
    if not identity or identity.steam_id != user.steam_id:
        raise HTTPException(403, "Primero verificá tu cuenta con Steam desde tu perfil")
    with _user_lock(user.id):
        cache = db.get(SteamProfileCache, user.id, populate_existing=True)
        now = int(time.time())
        interval = RETRY_DELAY if force else TTL
        if cache and cache.steam_id == identity.steam_id and now - cache.checked_at < interval:
            return cache
        if not cache:
            cache = SteamProfileCache(user_id=user.id, steam_id=identity.steam_id, checked_at=0, library={}, friends={})
            db.add(cache)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                cache = db.get(SteamProfileCache, user.id)
        if cache.steam_id != identity.steam_id:
            cache.steam_id = identity.steam_id; cache.library = {}; cache.friends = {}
        # Red en paralelo, fuera del hilo del evento y sin compartir la sesión SQL.
        with ThreadPoolExecutor(max_workers=2) as pool:
            library_future = pool.submit(fetch_library, identity.steam_id)
            friends_future = pool.submit(fetch_friends, identity.steam_id)
            library, friends = library_future.result(), friends_future.result()
        if library["status"] == "ok":
            owned = {str(game["appid"]) for game in library.get("items", [])}
            progress = (cache.library or {}).get("achievement_progress", {})
            library["achievement_progress"] = {key: value for key, value in progress.items() if key in owned}
        cache.library = _merge(cache.library, library)
        cache.friends = _merge(cache.friends, friends)
        cache.checked_at = now
        db.commit(); db.refresh(cache)
        return cache


def profile_payload(db, user, cache):
    library = deepcopy(cache.library)
    progress = library.pop("achievement_progress", {})
    friends = deepcopy(cache.friends)
    games = library.get("items", [])
    appids = [game["appid"] for game in games]
    local = {}
    # Consultas por lote; no un SELECT por juego y compatible con SQLite.
    for start in range(0, len(appids), 500):
        rows = db.execute(select(Game.id, Game.steam_app_id, Rating.score).outerjoin(
            Rating, (Rating.game_id == Game.id) & (Rating.user_id == user.id)
        ).where(Game.steam_app_id.in_(appids[start:start+500])))
        local.update({row.steam_app_id: row for row in rows})
    for game in games:
        row = local.get(game["appid"])
        game["game_id"] = row.id if row else None
        game["rating"] = row.score if row else None
        achievement = progress.get(str(game["appid"]))
        game["achievements"] = {key: achievement.get(key) for key in (
            "status", "unlocked", "total", "percentage", "checked_at", "updated_at")} if achievement else None
    games.sort(key=lambda game: (-(game["minutes"] or 0), game["name"].casefold()))
    library["items"] = games
    library["total_hours"] = round(sum(game["minutes"] or 0 for game in games) / 60, 1)
    library["hours_complete"] = all(game["minutes"] is not None for game in games)
    library["played_count"] = sum((game["minutes"] or 0) > 0 for game in games)
    library["rated_count"] = sum(game["rating"] is not None for game in games)
    known = {}
    ids = [friend["steam_id"] for friend in friends.get("items", [])]
    for start in range(0, len(ids), 500):
        rows = db.execute(select(SteamIdentity.steam_id, User.id, User.username).join(User, SteamIdentity.user_id == User.id).where(
            SteamIdentity.steam_id.in_(ids[start:start+500]), User.steam_id == SteamIdentity.steam_id,
            User.is_active.is_(True), User.role == UserRole.PLAYER))
        known.update({row.steam_id: {"id": row.id, "username": row.username} for row in rows})
    relations = {}
    if user.role == UserRole.PLAYER:
        links = friendship_service.list_friends(db, user)
        relations.update({friend.id: {"state": "accepted"} for friend in links.friends})
        relations.update({req.user.id: {"state": "incoming", "request_id": req.id} for req in links.incoming})
        relations.update({req.user.id: {"state": "outgoing", "request_id": req.id} for req in links.outgoing})
    for friend in friends.get("items", []):
        account = known.get(friend["steam_id"])
        friend["account"] = account
        friend["relationship"] = relations.get(account["id"], {"state": "none"}) if account else {"state": "none"}
        friend["profile_url"] = f"https://steamcommunity.com/profiles/{friend['steam_id']}/"
    friends["items"] = sorted(friends.get("items", []), key=lambda friend: (not bool(friend["account"]), friend["name"].casefold(), friend["steam_id"]))
    return {"steam_id": cache.steam_id, "checked_at": cache.checked_at,
            "next_refresh_at": cache.checked_at + RETRY_DELAY,
            "profile_url": f"https://steamcommunity.com/profiles/{cache.steam_id}/",
            "reviews_url": f"https://steamcommunity.com/profiles/{cache.steam_id}/recommended/",
            "library": library, "friends": friends}
