"""Cliente de la API de RAWG para importar juegos reales.

Es opcional: el seed usa el dataset local curado cuando no hay clave de API.
La respuesta de RAWG se normaliza al mismo formato de diccionario que
produce ``data/games_seed.json``, así que el resto del seed no distingue el
origen de los datos.
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any

import httpx

from app.core.config import settings

# RAWG devuelve los géneros en inglés; los mapeamos a nuestros slugs.
GENRE_SLUG_MAP = {
    "action": "accion",
    "adventure": "aventura",
    "role-playing-games-rpg": "rpg",
    "rpg": "rpg",
    "shooter": "shooter",
    "indie": "indie",
    "strategy": "estrategia",
    "simulation": "simulacion",
    "puzzle": "puzzle",
    "platformer": "plataformas",
    "racing": "carreras",
    "sports": "deportes",
    "fighting": "lucha",
    "massively-multiplayer": "multijugador-masivo",
    "card": "cartas",
    "casual": "casual",
    "board-games": "mesa",
    "educational": "educativo",
    "family": "familiar",
    "arcade": "arcade",
}


def slugify(value: str) -> str:
    """Convierte un texto arbitrario en un slug ASCII con guiones."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return cleaned or "sin-nombre"


def _clean_description(raw: str | None) -> str:
    """Quita el HTML que RAWG incluye en las descripciones."""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    # Las descripciones de RAWG suelen incluir la traducción al español a
    # continuación del texto en inglés, separada por este marcador.
    marker = "Español"
    if marker in text:
        text = text.split(marker, 1)[1].strip()
    return text[:1200]


def _aspect_profile_from_metacritic(metacritic: int | None) -> dict[str, float]:
    """Perfil por aspecto neutro derivado del puntaje global.

    RAWG no expone valoraciones por aspecto. Para que el generador de reseñas
    del seed funcione igual con datos importados, se deriva un perfil plano a
    partir de Metacritic. No pretende ser una medición real: los datos
    importados sirven para poblar el catálogo, no para evaluar el ABSA.
    """
    base = (metacritic or 72) / 100
    return {
        "jugabilidad": base,
        "graficos": base,
        "historia": base,
        "optimizacion": base,
    }


def _map_game(detail: dict[str, Any]) -> dict[str, Any]:
    """Normaliza un juego de RAWG al formato del dataset local."""
    developers = detail.get("developers") or []
    publishers = detail.get("publishers") or []
    platforms = {
        (p.get("platform") or {}).get("name", "")
        for p in (detail.get("platforms") or [])
    }

    genres = []
    for genre in detail.get("genres") or []:
        slug = GENRE_SLUG_MAP.get(genre.get("slug", ""), slugify(genre.get("name", "")))
        if slug not in genres:
            genres.append(slug)

    tags = []
    for tag in (detail.get("tags") or [])[:12]:
        # RAWG mezcla etiquetas en varios idiomas; nos quedamos con las de inglés
        # porque son las que tienen slug estable.
        if tag.get("language") != "eng":
            continue
        slug = slugify(tag.get("name", ""))
        if slug and slug not in tags:
            tags.append(slug)

    return {
        "external_id": detail.get("id"),
        "slug": detail.get("slug") or slugify(detail.get("name", "")),
        "name": detail.get("name", "Sin nombre"),
        "description": _clean_description(detail.get("description")),
        "released": detail.get("released"),
        "developer": developers[0]["name"] if developers else None,
        "publisher": publishers[0]["name"] if publishers else None,
        "platforms": sorted(p for p in platforms if p),
        "background_image": detail.get("background_image"),
        "metacritic": detail.get("metacritic"),
        "external_rating": detail.get("rating"),
        "playtime_hours": detail.get("playtime"),
        "aspect_profile": _aspect_profile_from_metacritic(detail.get("metacritic")),
    }


def fetch_games(pages: int = 2, page_size: int = 40, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Descarga los juegos más populares de RAWG y los normaliza.

    Hace una llamada al listado por página y una llamada al detalle por juego,
    porque el endpoint de listado no devuelve la descripción.

    Raises:
        RuntimeError: si no hay ``RAWG_API_KEY`` configurada.
    """
    if not settings.RAWG_API_KEY:
        raise RuntimeError(
            "Falta RAWG_API_KEY. Configurala en backend/.env o usá --source local."
        )

    games: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout, base_url=settings.RAWG_BASE_URL) as client:
        for page in range(1, pages + 1):
            listing = client.get(
                "/games",
                params={
                    "key": settings.RAWG_API_KEY,
                    "page": page,
                    "page_size": page_size,
                    "ordering": "-added",
                },
            )
            listing.raise_for_status()
            results = listing.json().get("results", [])
            if not results:
                break

            for item in results:
                try:
                    detail = client.get(
                        f"/games/{item['id']}", params={"key": settings.RAWG_API_KEY}
                    )
                    detail.raise_for_status()
                    games.append(_map_game(detail.json()))
                except httpx.HTTPError as exc:
                    print(f"  ! No se pudo importar {item.get('name')}: {exc}")

            print(f"  · Página {page}: {len(games)} juegos acumulados")

    return games
