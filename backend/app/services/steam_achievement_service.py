"""Logros del usuario verificado, bajo demanda y con caché privada acotada."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import time
from fastapi import HTTPException
from app.models import SteamProfileCache
from app.services import steam_profile_service as profiles

TTL = 600
RETRY_DELAY = 30
MAX_CACHED_GAMES = 64


def fetch_achievements(steam_id, appid):
    with ThreadPoolExecutor(max_workers=2) as pool:
        player_future = pool.submit(profiles._request, "/ISteamUserStats/GetPlayerAchievements/v1/",
            {"steamid": steam_id, "appid": appid, "l": "spanish"})
        schema_future = pool.submit(profiles._request, "/ISteamUserStats/GetSchemaForGame/v2/",
            {"appid": appid, "l": "spanish"})
        player_status, player = player_future.result()
        schema_status, schema = schema_future.result()
    if player_status != "ok":
        return {"status": player_status}
    stats = (player or {}).get("playerstats")
    if not isinstance(stats, dict):
        return {"status": "unavailable"}
    if stats.get("steamID") is not None and str(stats["steamID"]) != steam_id:
        return {"status": "unavailable"}
    if stats.get("success") is not True:
        error = str(stats.get("error", "")).casefold()
        if "private" in error or "not public" in error:
            return {"status": "private"}
        if "no stats" in error or "no achievements" in error:
            return {"status": "unsupported"}
        return {"status": "unavailable"}
    game = (schema or {}).get("game") if schema_status == "ok" else None
    definitions = game.get("availableGameStats", {}).get("achievements") if isinstance(game, dict) and isinstance(game.get("availableGameStats", {}), dict) else None
    if definitions == []:
        return {"status": "unsupported"}
    raw = stats.get("achievements")
    if not isinstance(raw, list) or not isinstance(definitions, list):
        return {"status": "unavailable"}
    states, catalog = {}, {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("apiname"), str) or type(item.get("achieved")) is not int or item["achieved"] not in (0, 1):
            return {"status": "unavailable"}
        if item["apiname"] in states:
            return {"status": "unavailable"}
        states[item["apiname"]] = item
    for definition in definitions:
        if not isinstance(definition, dict) or not isinstance(definition.get("name"), str) or definition["name"] in catalog:
            return {"status": "unavailable"}
        catalog[definition["name"]] = definition
    # Nunca asumimos que un logro ausente esté bloqueado ni inventamos un total.
    if not catalog or set(catalog) != set(states):
        return {"status": "unavailable"}
    items = []
    for name, definition in catalog.items():
        achievement = states[name]
        unlocked = achievement["achieved"] == 1
        hidden = str(definition.get("hidden", 0)) == "1" and not unlocked
        items.append({"id": name, "name": "Logro oculto" if hidden else str(definition.get("displayName") or name)[:250],
            "description": None if hidden else str(definition.get("description") or "")[:1500],
            "icon": None if hidden else profiles._image(definition.get("icon") if unlocked else definition.get("icongray")),
            "unlocked": unlocked, "hidden": hidden,
            "unlocked_at": profiles._integer(achievement.get("unlocktime")) if unlocked else None})
    items.sort(key=lambda item: (not item["unlocked"], -(item["unlocked_at"] or 0), item["name"].casefold()))
    unlocked = sum(item["unlocked"] for item in items)
    return {"status": "ok", "items": items, "unlocked": unlocked, "total": len(items),
            "percentage": round(unlocked/len(items)*100, 1), "updated_at": int(time.time())}


def achievements(db, user, appid, force=False):
    cache = profiles.sync_profile(db, user)
    if not any(game["appid"] == appid for game in cache.library.get("items", [])):
        if cache.library.get("status") == "private":
            return _payload(appid, {"status": "private", "checked_at": int(time.time())})
        raise HTTPException(404, "Este juego no está disponible en tu biblioteca de Steam")
    with profiles._user_lock(user.id):
        cache = db.get(SteamProfileCache, user.id, populate_existing=True)
        library = deepcopy(cache.library)
        progress = library.get("achievement_progress", {})
        previous = progress.get(str(appid))
        now = int(time.time())
        # Los errores transitorios no deben bloquear la carga automática
        # durante los diez minutos reservados a datos válidos cacheados.
        ttl = RETRY_DELAY if force or (previous and previous.get("status") == "unavailable") else TTL
        if previous and now - previous["checked_at"] < ttl:
            return _payload(appid, previous)
        fresh = fetch_achievements(cache.steam_id, appid)
        if fresh["status"] == "unavailable" and previous:
            fresh = {**deepcopy(previous), "status": "unavailable"}
        fresh["checked_at"] = now
        progress[str(appid)] = fresh
        progress = dict(sorted(progress.items(), key=lambda item: -item[1].get("checked_at", 0))[:MAX_CACHED_GAMES])
        library["achievement_progress"] = progress
        cache.library = library
        db.commit()
        return _payload(appid, fresh)


def _payload(appid, value):
    return {"appid": appid, "items": [], "unlocked": None, "total": None, "percentage": None,
            **deepcopy(value), "next_refresh_at": value["checked_at"] + RETRY_DELAY,
            "community_url": f"https://steamcommunity.com/my/stats/{appid}/?tab=achievements"}
