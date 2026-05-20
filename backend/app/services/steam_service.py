import httpx
from app.core.config import settings


STEAM_API_BASE = "https://api.steampowered.com"
STEAM_STORE_BASE = "https://store.steampowered.com/api"


async def get_app_details(steam_app_id: int) -> dict | None:
    url = f"{STEAM_STORE_BASE}/appdetails"
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params={"appids": steam_app_id, "l": "english"})
        if response.status_code != 200:
            return None
        data = response.json()
        app_data = data.get(str(steam_app_id), {})
        if not app_data.get("success"):
            return None
        return app_data.get("data")


async def get_owned_games(steam_id: str) -> list:
    url = f"{STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/"
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params={
            "key": settings.STEAM_API_KEY,
            "steamid": steam_id,
            "include_appinfo": True,
            "include_played_free_games": True,
        })
        if response.status_code != 200:
            return []
        data = response.json()
        return data.get("response", {}).get("games", [])


def parse_steam_game(data: dict) -> dict:
    genres = [g["description"] for g in data.get("genres", [])]
    tags = list(data.get("categories", [{}]))
    platforms_raw = data.get("platforms", {})
    platforms = [p for p, active in platforms_raw.items() if active]

    release_date_raw = data.get("release_date", {}).get("date", "")

    price_data = data.get("price_overview", {})
    price_usd = price_data.get("final", 0) / 100 if price_data else None
    is_free = data.get("is_free", False)

    return {
        "title": data.get("name", ""),
        "description": data.get("detailed_description", ""),
        "short_description": data.get("short_description", ""),
        "developer": ", ".join(data.get("developers", [])),
        "publisher": ", ".join(data.get("publishers", [])),
        "genres": genres,
        "platforms": platforms,
        "price_usd": price_usd,
        "is_free": is_free,
        "cover_image_url": data.get("capsule_image", ""),
        "header_image_url": data.get("header_image", ""),
        "metacritic_score": data.get("metacritic", {}).get("score"),
        "steam_positive_reviews": data.get("recommendations", {}).get("total", 0),
    }