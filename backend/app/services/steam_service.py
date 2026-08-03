"""Integración con Steam: importar fichas de juegos y vincular cuentas.

La ficha pública de un juego se obtiene de la API de la tienda y no necesita
clave. Consultar la biblioteca de un usuario (``GetOwnedGames``) sí requiere
``STEAM_API_KEY``.

A diferencia de la versión anterior, el cliente HTTP es **sincrónico**: el
resto de la aplicación lo es, y un endpoint declarado ``async`` que después
hace consultas bloqueantes a la base terminaría bloqueando el bucle de
eventos. Siendo sincrónico, FastAPI lo ejecuta en su pool de hilos.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Game, Genre, Tag, User

TIMEOUT = 15.0

# Steam devuelve los géneros en inglés; el catálogo los maneja en español.
# Lo que no esté acá se incorpora con su nombre original.
GENRE_TRANSLATIONS = {
    "action": "Acción",
    "adventure": "Aventura",
    "casual": "Casual",
    "indie": "Indie",
    "massively multiplayer": "Multijugador masivo",
    "racing": "Carreras",
    "rpg": "RPG",
    "simulation": "Simulación",
    "sports": "Deportes",
    "strategy": "Estrategia",
    "early access": "Acceso anticipado",
    "free to play": "Free to play",
}

PLATFORM_NAMES = {"windows": "PC", "mac": "Mac", "linux": "Linux"}


def slugify(text: str) -> str:
    """Convierte un nombre en un slug ASCII apto para URL."""
    normalized = unicodedata.normalize("NFKD", text.lower())
    ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    return slug or "juego"


def _parse_release_date(raw: str) -> date | None:
    """Steam no tiene un formato único de fecha; se prueban los habituales."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for pattern in ("%d %b, %Y", "%b %d, %Y", "%d %B, %Y", "%B %d, %Y", "%Y"):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Llamadas a Steam
# ---------------------------------------------------------------------------


def get_app_details(steam_app_id: int) -> dict | None:
    """Ficha de un juego en la tienda de Steam. ``None`` si no existe."""
    try:
        response = httpx.get(
            f"{settings.STEAM_STORE_BASE}/appdetails",
            params={"appids": steam_app_id, "l": "spanish"},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError:
        return None

    if response.status_code != 200:
        return None

    payload = response.json().get(str(steam_app_id), {})
    if not payload.get("success"):
        return None
    return payload.get("data")


def get_owned_games(steam_id: str) -> list[dict]:
    """Biblioteca de un usuario. Lista vacía si no hay clave o falla."""
    if not settings.STEAM_API_KEY:
        return []
    try:
        response = httpx.get(
            f"{settings.STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/",
            params={
                "key": settings.STEAM_API_KEY,
                "steamid": steam_id,
                "include_appinfo": True,
                "include_played_free_games": True,
            },
            timeout=TIMEOUT,
        )
    except httpx.HTTPError:
        return []

    if response.status_code != 200:
        return []
    return response.json().get("response", {}).get("games", [])


def get_player_summary(steam_id: str) -> dict | None:
    """Perfil público de un usuario, para completar nombre y avatar."""
    if not settings.STEAM_API_KEY:
        return None
    try:
        response = httpx.get(
            f"{settings.STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/",
            params={"key": settings.STEAM_API_KEY, "steamids": steam_id},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError:
        return None

    if response.status_code != 200:
        return None
    players = response.json().get("response", {}).get("players", [])
    return players[0] if players else None


# ---------------------------------------------------------------------------
# Traducción al modelo de datos
# ---------------------------------------------------------------------------


def parse_steam_game(data: dict) -> dict:
    """Traduce la ficha de Steam a los campos de ``Game``.

    Los campos que el modelo no tiene (precio, cantidad de reseñas en Steam)
    se descartan a propósito en lugar de agregar columnas que nada consume.
    """
    platforms = [
        PLATFORM_NAMES[key]
        for key, active in (data.get("platforms") or {}).items()
        if active and key in PLATFORM_NAMES
    ]

    genres = []
    for entry in data.get("genres") or []:
        name = (entry.get("description") or "").strip()
        if name:
            genres.append(GENRE_TRANSLATIONS.get(name.lower(), name))

    # Las categorías de Steam ("Un jugador", "Cooperativo") encajan con lo que
    # el catálogo llama etiquetas.
    tags = [
        (entry.get("description") or "").strip()
        for entry in data.get("categories") or []
        if (entry.get("description") or "").strip()
    ]

    name = (data.get("name") or "").strip()
    return {
        "name": name,
        "slug": slugify(name),
        # La descripción corta viene sin HTML; la larga lo trae y ensuciaría
        # tanto la vista como el corpus TF-IDF del recomendador.
        "description": (data.get("short_description") or "").strip() or None,
        "released": _parse_release_date((data.get("release_date") or {}).get("date", "")),
        "developer": ", ".join(data.get("developers") or []) or None,
        "publisher": ", ".join(data.get("publishers") or []) or None,
        "platforms": platforms,
        "background_image": data.get("header_image") or None,
        "metacritic": (data.get("metacritic") or {}).get("score"),
        "genres": genres,
        "tags": tags[:8],
    }


def _get_or_create(db: Session, model, name: str):
    """Busca un género o etiqueta por slug y lo crea si no existe."""
    slug = slugify(name)
    existing = db.scalar(select(model).where(model.slug == slug))
    if existing:
        return existing
    created = model(slug=slug, name=name)
    db.add(created)
    db.flush()
    return created


def get_game_by_steam_app_id(db: Session, steam_app_id: int) -> Game | None:
    return db.scalar(select(Game).where(Game.steam_app_id == steam_app_id))


def import_game(db: Session, steam_app_id: int) -> Game | None:
    """Importa un juego de Steam al catálogo.

    Si ya estaba importado lo devuelve sin volver a pedirlo. Devuelve ``None``
    cuando Steam no reconoce el AppID.
    """
    existing = get_game_by_steam_app_id(db, steam_app_id)
    if existing:
        return existing

    data = get_app_details(steam_app_id)
    if not data:
        return None

    parsed = parse_steam_game(data)
    genres = parsed.pop("genres", [])
    tags = parsed.pop("tags", [])

    # El slug es único: si el juego ya está en el catálogo por otra fuente
    # (el dataset local, RAWG) se le adosa el AppID en lugar de fallar.
    if db.scalar(select(Game).where(Game.slug == parsed["slug"])):
        parsed["slug"] = f"{parsed['slug']}-{steam_app_id}"

    game = Game(**parsed, steam_app_id=steam_app_id)
    # El juego entra a la sesión antes de asociarle géneros y etiquetas:
    # `_get_or_create` hace flush, y si el Game todavía estuviera fuera de la
    # sesión, SQLAlchemy descartaría silenciosamente la asociación.
    db.add(game)
    game.genres = [_get_or_create(db, Genre, name) for name in genres]
    game.tags = [_get_or_create(db, Tag, name) for name in tags]

    db.commit()
    db.refresh(game)
    return game


def link_steam_account(
    db: Session, user: User, steam_id: str, fetch_profile: bool = True
) -> User:
    """Vincula una cuenta de Steam a un usuario ya existente."""
    taken = db.scalar(
        select(User).where(User.steam_id == steam_id, User.id != user.id)
    )
    if taken is not None:
        raise ValueError("Esa cuenta de Steam ya está vinculada a otro usuario")

    user.steam_id = steam_id
    if fetch_profile:
        profile = get_player_summary(steam_id)
        if profile:
            user.steam_username = profile.get("personaname")
            user.steam_avatar_url = profile.get("avatarfull")

    db.commit()
    db.refresh(user)
    return user
