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
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.ml.analytics import analyze_review, apply_analysis
from app.ml.recommender import invalidate_engine
from app.models import Game, Genre, Review, Tag, User
from app.services.interaction_service import (
    recompute_game_aggregates,
    recompute_median_review_hours,
)

TIMEOUT = 15.0

# Un cliente HTTP compartido en vez de `httpx.get()` suelto en cada función:
# cada llamada suelta crea (y en teoría cierra) su propio contexto TLS, pero
# en Windows con `truststore` cada uno abre handles al almacén de certificados
# del sistema que no se liberan al ritmo en que se piden; en una importación
# masiva (cientos de juegos, varios pedidos cada uno) eso agota los file
# descriptors del proceso. Un cliente único crea ese contexto una sola vez.
_client: httpx.Client | None = None


def _http_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=TIMEOUT)
    return _client

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


def _fit_slug(slug: str, limit: int) -> str:
    """Shorten a slug without merging distinct names sharing a long prefix."""
    if len(slug) <= limit:
        return slug
    suffix = "-" + sha256(slug.encode("utf-8")).hexdigest()[:12]
    return slug[:limit - len(suffix)].rstrip("-") + suffix


def _dedupe_names(names: list[str]) -> list[str]:
    """Algunas fichas de Steam repiten la misma categoría o género dos veces.

    Sin esto, `_get_or_create` devuelve el mismo `Genre`/`Tag` para ambas
    repeticiones y la lista queda con el objeto duplicado, lo que rompe la
    unicidad de `game_genres`/`game_tags` al guardar.
    """
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        key = slugify(name)
        if key and key not in seen:
            seen.add(key)
            result.append(name)
    return result


# Meses en español, resueltos a mano. `datetime.strptime` con %b depende del
# locale del proceso, y las fichas se piden con `l=spanish` (ver
# STEAM_STORE_BASE), así que Steam contesta "21 AGO 2012": con los patrones en
# inglés no parseaba ninguna fecha y TODO el catálogo quedaba sin año.
_MESES_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}

_FECHA_ES = re.compile(
    r"^(?:(\d{1,2})\s+)?([A-Za-zÁÉÍÓÚÑáéíóúñ]{3,})\.?,?\s+(\d{4})$"
)


def _parse_release_date(raw: str) -> date | None:
    """Steam no tiene un formato único de fecha; se prueban los habituales.

    El día es opcional a propósito: para los juegos viejos Steam a veces sólo
    publica mes y año ("AGO 2012"), y quedarse con el 1 de ese mes es mucho
    más útil que descartar la fecha entera.
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    match = _FECHA_ES.match(raw)
    if match:
        dia, mes_txt, anio = match.groups()
        mes = _MESES_ES.get(mes_txt[:3].lower())
        if mes:
            try:
                return date(int(anio), mes, int(dia or 1))
            except ValueError:
                pass

    for pattern in ("%d %b, %Y", "%b %d, %Y", "%d %B, %Y", "%B %d, %Y", "%Y"):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Llamadas a Steam
# ---------------------------------------------------------------------------


class SteamUnavailable(RuntimeError):
    """No se pudo hablar con Steam (timeout, error de red, 5xx, 429...).

    Existe para separarlo del caso "Steam contestó que ese AppID no es un
    juego", que devuelve ``None``. Confundir los dos es destructivo: quien
    limpia fichas pendientes que resultaron no ser juegos borraría el
    catálogo entero durante una caída de Steam.
    """

    def __init__(self, message: str, retry_after_seconds: int = 60):
        super().__init__(message)
        self.retry_after_seconds = max(60, min(86400, retry_after_seconds))


def get_app_details(steam_app_id: int) -> dict | None:
    """Ficha de un juego en la tienda de Steam.

    Devuelve ``None`` sólo cuando Steam responde que ese AppID no existe o
    no tiene ficha pública. Si no se pudo contactar a Steam levanta
    ``SteamUnavailable``: no saber no es lo mismo que saber que no está.
    """
    try:
        response = _http_client().get(
            f"{settings.STEAM_STORE_BASE}/appdetails",
            params={"appids": steam_app_id, "l": "spanish"},
        )
    except httpx.HTTPError as error:
        # Nunca persistir la URL ni los parámetros de una excepción HTTP.
        raise SteamUnavailable("No se pudo contactar a Steam") from error

    if response.status_code != 200:
        retry = response.headers.get("Retry-After", "60")
        delay = int(retry) if retry.isdecimal() else 60
        raise SteamUnavailable(f"Steam respondió {response.status_code}", delay)

    try:
        response_data = response.json()
    except ValueError as error:  # respuesta que no es JSON (portal cautivo, proxy)
        raise SteamUnavailable("Steam respondió algo que no es JSON") from error

    if not isinstance(response_data, dict):
        raise SteamUnavailable("Steam devolvió una ficha con formato inválido")
    payload = response_data.get(str(steam_app_id))
    if not isinstance(payload, dict) or not isinstance(payload.get("success"), bool):
        raise SteamUnavailable("Steam devolvió una ficha con formato inválido")
    if payload["success"] is False:
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("type"), str) or not data["type"].strip():
        raise SteamUnavailable("Steam devolvió una ficha con formato inválido")
    if data["type"] == "game":
        if not isinstance(data.get("name"), str) or not data["name"].strip():
            raise SteamUnavailable("Steam devolvió un juego sin nombre válido")
        for key in ("platforms", "release_date", "metacritic"):
            if data.get(key) is not None and not isinstance(data[key], dict):
                raise SteamUnavailable("Steam devolvió una ficha con formato inválido")
        for key in ("genres", "categories"):
            value = data.get(key)
            if value is not None and (not isinstance(value, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("description", ""), str)
                for item in value
            )):
                raise SteamUnavailable("Steam devolvió una ficha con formato inválido")
        for key in ("developers", "publishers"):
            value = data.get(key)
            if value is not None and (not isinstance(value, list) or any(not isinstance(item, str) for item in value)):
                raise SteamUnavailable("Steam devolvió una ficha con formato inválido")
        for key in ("short_description", "header_image"):
            if data.get(key) is not None and not isinstance(data[key], str):
                raise SteamUnavailable("Steam devolvió una ficha con formato inválido")
        if not isinstance((data.get("release_date") or {}).get("date", ""), str):
            raise SteamUnavailable("Steam devolvió una fecha con formato inválido")
        score = (data.get("metacritic") or {}).get("score")
        if score is not None and (type(score) is not int or not 0 <= score <= 100):
            raise SteamUnavailable("Steam devolvió una puntuación con formato inválido")
    return data


_SEARCH_APPID_RE = re.compile(r'data-ds-appid="(\d+)"')


def _featured_appids() -> list[int]:
    """Los títulos más reconocibles: destacados y en oferta de la tienda.

    Se excluye a propósito "new_releases": es donde entra la mayor parte del
    shovelware (cualquiera puede publicar ahí), y ensucia más de lo que suma.
    """
    try:
        response = _http_client().get(
            f"{settings.STEAM_STORE_BASE}/featuredcategories",
            params={"l": "spanish"},
        )
    except httpx.HTTPError:
        return []
    if response.status_code != 200:
        return []
    payload = response.json()

    appids: list[int] = []
    seen: set[int] = set()
    for key in ("top_sellers", "specials"):
        for item in (payload.get(key) or {}).get("items", []):
            appid = item.get("id")
            if appid and appid not in seen:
                seen.add(appid)
                appids.append(appid)
    return appids


def _search_appids_page(start: int, count: int) -> list[int]:
    """Una página del buscador de la tienda, ordenada por cantidad de reseñas.

    No hay un endpoint oficial documentado para "listar juegos por calidad",
    así que se usa el mismo buscador que la tienda expone al público
    (``category1=998`` filtra a juegos, sin DLC ni software). Ordenar por
    reseñas filtra shovelware de forma natural: un asset-flip casi nunca
    acumula reseñas.
    """
    try:
        response = _http_client().get(
            "https://store.steampowered.com/search/results/",
            params={
                "query": "",
                "start": start,
                "count": count,
                "sort_by": "Reviews_DESC",
                "category1": 998,
                "supportedlang": "spanish",
                "ndl": 1,
            },
            headers={"User-Agent": "Mozilla/5.0"},
        )
    except httpx.HTTPError:
        return []
    if response.status_code != 200:
        return []
    return [int(match) for match in _SEARCH_APPID_RE.findall(response.text)]


def get_top_seller_appids(limit: int = 100, delay: float = 1.0) -> list[int]:
    """AppIDs de juegos reales, del más al menos relevante.

    Steam no tiene un endpoint público de "todos los juegos" con metadata
    útil (``GetAppList`` da cientos de miles de entradas, incluye software y
    bandas sonoras). Arranca con los destacados de la tienda —los títulos más
    reconocibles— y completa con el buscador ordenado por reseñas cuando se
    pide más volumen del que traen esas categorías (unos 40-50 juegos).
    """
    appids = _featured_appids()
    seen = set(appids)

    start = 0
    page_size = 100
    while len(appids) < limit:
        batch = _search_appids_page(start, page_size)
        if not batch:
            break
        for appid in batch:
            if appid not in seen:
                seen.add(appid)
                appids.append(appid)
        start += page_size
        if len(appids) < limit:
            time.sleep(delay)
    return appids[:limit]


STEAMSPY_BASE = "https://steamspy.com/api.php"
STEAMSPY_PAGE_SIZE = 1000


def _fetch_steamspy_page(page: int, retries: int = 3, retry_delay: float = 5.0) -> dict | None:
    """Una página de ``request=all``. ``None`` si se agotaron los reintentos.

    SteamSpy es un servicio comunitario gratuito, no de Valve: bajo carga
    responde "Too many connections" (texto plano, no JSON) en vez de fallar
    limpio, así que vale la pena un par de reintentos antes de darse por
    vencido.
    """
    for attempt in range(retries):
        try:
            response = _http_client().get(
                STEAMSPY_BASE, params={"request": "all", "page": page}
            )
        except httpx.HTTPError:
            response = None

        if response is not None and response.status_code == 200:
            try:
                return response.json()
            except ValueError:
                pass

        if attempt < retries - 1:
            time.sleep(retry_delay)
    return None


def get_app_list(delay: float = 1.5, max_pages: int | None = None) -> list[dict]:
    """AppID y nombre de la gran mayoría de lo publicado en Steam.

    Valve dio de baja ``GetAppList``, el endpoint propio que hacía esto en
    un único pedido (confirmado contra ``ISteamWebAPIUtil.
    GetSupportedAPIList``, que ya no lo lista; devuelve 404 "Method not
    found" si se lo llama). En su lugar se usa SteamSpy (steamspy.com), un
    índice comunitario no oficial que pagina de a 1000 juegos por pedido
    (``request=all``) y además trae reseñas positivas/negativas y dueños
    estimados por juego — más rico que lo que daba ``GetAppList``, a costa
    de depender de un tercero en lugar de Steam mismo.

    Se detiene al llegar a una página con menos de 1000 entradas (la
    última) o cuando SteamSpy deja de responder; en ese caso devuelve lo
    que haya juntado hasta ahí en lugar de fallar todo el pedido.
    """
    apps: list[dict] = []
    page = 0
    while max_pages is None or page < max_pages:
        entries = _fetch_steamspy_page(page)
        if entries is None:
            break
        apps.extend(entries.values())
        if len(entries) < STEAMSPY_PAGE_SIZE:
            break
        page += 1
        time.sleep(delay)
    return apps


def get_owned_games(steam_id: str) -> list[dict]:
    """Biblioteca de un usuario. Lista vacía si no hay clave o falla."""
    if not settings.STEAM_API_KEY:
        return []
    try:
        response = _http_client().get(
            f"{settings.STEAM_API_BASE}/IPlayerService/GetOwnedGames/v1/",
            params={
                "key": settings.STEAM_API_KEY,
                "steamid": steam_id,
                "include_appinfo": True,
                "include_played_free_games": True,
            },
        )
    except httpx.HTTPError:
        return []

    if response.status_code != 200:
        return []
    return response.json().get("response", {}).get("games", [])


def get_app_reviews(
    steam_app_id: int, num: int | None = None, language: str = "spanish"
) -> list[dict]:
    """Reseñas públicas reales de un juego. No requiere clave.

    Se filtra por idioma porque el módulo NLP está construido sobre un
    léxico en español (ver ``app/ml/lexicon.py``).
    """
    limit = num or settings.STEAM_REVIEWS_IMPORT_LIMIT
    try:
        response = _http_client().get(
            f"{settings.STEAM_REVIEWS_BASE}/{steam_app_id}",
            params={
                "json": 1,
                "filter": "recent",
                "language": language,
                "num_per_page": min(limit, 100),
                "purchase_type": "all",
            },
        )
    except httpx.HTTPError:
        return []

    if response.status_code != 200:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    if not isinstance(payload, dict) or payload.get("success") != 1:
        return []
    reviews = payload.get("reviews", [])
    return [entry for entry in reviews if isinstance(entry, dict)] if isinstance(reviews, list) else []


def get_review_totals(steam_app_id: int) -> tuple[int, int] | None:
    """Cantidad REAL de reseñas de un juego en Steam: ``(total, positivas)``.

    Es lo que mide popularidad de verdad. No alcanza con contar las reseñas
    importadas: el import está capado (``STEAM_REVIEWS_IMPORT_LIMIT``), así
    que todos los juegos terminan con una muestra parecida y el conteo deja
    de distinguir a Counter-Strike (9,7 millones) de un indie con 500.

    Pide una sola reseña porque lo único que interesa es ``query_summary``,
    que Steam devuelve igual. ``None`` si no se pudo averiguar.
    """
    try:
        response = _http_client().get(
            f"{settings.STEAM_REVIEWS_BASE}/{steam_app_id}",
            params={
                "json": 1,
                "num_per_page": 1,
                # Los totales son del juego, no de un idioma: filtrar acá
                # subestimaría la popularidad de todo lo que no sea español.
                "language": "all",
                "purchase_type": "all",
            },
        )
    except httpx.HTTPError:
        return None

    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None

    if not isinstance(payload, dict) or not isinstance(payload.get("query_summary"), dict):
        return None
    summary = payload["query_summary"]
    total = summary.get("total_reviews")
    positive = summary.get("total_positive")
    if type(total) is not int or type(positive) is not int or not 0 <= positive <= total:
        return None
    return int(total), int(positive)


def get_player_summaries_batch(steam_ids: list[str]) -> dict[str, dict]:
    """Perfiles públicos de varias cuentas en un único pedido (máx. 100)."""
    if not settings.STEAM_API_KEY or not steam_ids:
        return {}
    try:
        response = _http_client().get(
            f"{settings.STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/",
            params={"key": settings.STEAM_API_KEY, "steamids": ",".join(steam_ids[:100])},
        )
    except httpx.HTTPError:
        return {}

    if response.status_code != 200:
        return {}
    players = response.json().get("response", {}).get("players", [])
    return {player["steamid"]: player for player in players if player.get("steamid")}


def get_player_summary(steam_id: str) -> dict | None:
    """Perfil público de un usuario, para completar nombre y avatar."""
    if not settings.STEAM_API_KEY:
        return None
    try:
        response = _http_client().get(
            f"{settings.STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/",
            params={"key": settings.STEAM_API_KEY, "steamids": steam_id},
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
    from urllib.parse import urlsplit
    screenshots = []
    for shot in data.get("screenshots") or []:
        url = shot.get("path_full") if isinstance(shot, dict) else None
        if not isinstance(url, str):
            continue
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        host = (parts.hostname or "").lower()
        if parts.scheme == "https" and any(host == root or host.endswith("." + root)
                for root in ("steamstatic.com", "steamusercontent.com", "akamaihd.net")) and not parts.username and not parts.password:
            if url not in screenshots:
                screenshots.append(url)
    return {
        "name": name[:200],
        "slug": _fit_slug(slugify(name), 120),
        # La descripción corta viene sin HTML; la larga lo trae y ensuciaría
        # tanto la vista como el corpus TF-IDF del recomendador.
        "description": (data.get("short_description") or "").strip() or None,
        "released": _parse_release_date((data.get("release_date") or {}).get("date", "")),
        "developer": ", ".join(data.get("developers") or [])[:120] or None,
        "publisher": ", ".join(data.get("publishers") or [])[:120] or None,
        "platforms": platforms,
        "background_image": data.get("header_image") or None,
        "screenshots": screenshots[:16],
        "metacritic": (data.get("metacritic") or {}).get("score"),
        "genres": _dedupe_names(genres),
        "tags": _dedupe_names(tags)[:8],
    }


def _get_or_create(db: Session, model, name: str):
    """Busca un género o etiqueta por slug y lo crea si no existe."""
    slug = slugify(name)
    existing = db.scalar(select(model).where(model.slug == slug))
    if existing:
        return existing
    # Preserve existing IDs/slugs, including legacy SQLite data. New rows must
    # respect VARCHAR limits: PostgreSQL enforces them, SQLite does not.
    bounded_slug = _fit_slug(slug, 60)
    if bounded_slug != slug:
        existing = db.scalar(select(model).where(model.slug == bounded_slug))
        if existing:
            return existing
    created = model(slug=bounded_slug, name=name[:60])
    db.add(created)
    db.flush()
    return created


def get_game_by_steam_app_id(db: Session, steam_app_id: int) -> Game | None:
    return db.scalar(select(Game).where(Game.steam_app_id == steam_app_id))


def _catalog_entry(db: Session, game: Game):
    from app.models.steam_catalog import SteamCatalogEntry

    entry = db.get(SteamCatalogEntry, game.steam_app_id)
    if entry is None:
        entry = SteamCatalogEntry(
            appid=game.steam_app_id,
            status="ready" if game.is_enriched else "pending",
            attempts=0, priority=0, last_seen_at=datetime.now(timezone.utc),
        )
        db.add(entry)
    return entry


def _record_unavailable(db: Session, entry, message: str, retry_after_seconds: int = 0) -> None:
    now = datetime.now(timezone.utc)
    entry.status = "unavailable"
    entry.last_attempt_at = now
    entry.attempts = (entry.attempts or 0) + 1
    entry.last_error = message
    entry.next_attempt_at = now + timedelta(minutes=min(24 * 60, 5 * 2 ** min(entry.attempts - 1, 9)))
    entry.next_attempt_at = max(entry.next_attempt_at, now + timedelta(seconds=retry_after_seconds))
    db.commit()


def queue_game_refresh(db: Session, game: Game, *, priority: int = 100):
    """Encola sin red y sin commit; respeta el backoff de intentos fallidos."""
    if game.steam_app_id is None:
        return None
    entry = _catalog_entry(db, game)
    if entry.status == "non_game":
        return entry
    entry.priority = max(entry.priority or 0, priority)
    if entry.status == "ready":
        entry.status = "pending"
        entry.next_attempt_at = None
    return entry


def import_reviews(db: Session, game: Game, steam_app_id: int, *, commit: bool = True) -> int:
    """Trae reseñas reales de Steam nuevas para un juego y las analiza con el
    mismo módulo NLP que las reseñas escritas en GameTrack.

    No pertenecen a ningún usuario de la plataforma (``user_id`` nulo); se
    identifican por el nombre de perfil de Steam del autor, resuelto en un
    único pedido en lote para no hacer una llamada por reseña.

    Es seguro llamarla más de una vez sobre el mismo juego (ver
    ``refresh_game``): cada reseña de Steam se identifica por su
    ``recommendationid`` (``steam_review_id``), así que las que ya estén en
    la base se descartan antes de crear nada.
    """
    # Presupuesto persistente por juego, no por pedido: los refrescos no
    # pueden hacer crecer indefinidamente el corpus. No borrar muestras
    # antiguas ni contar las reseñas escritas por usuarios de GameTrack.
    imported = db.scalar(select(func.count(Review.id)).where(
        Review.game_id == game.id, Review.source == "steam"
    )) or 0
    remaining = max(0, settings.STEAM_REVIEWS_IMPORT_LIMIT - imported)
    if remaining == 0:
        return 0
    raw_reviews = get_app_reviews(steam_app_id, num=remaining)

    existing_ids = {
        row[0]
        for row in db.execute(
            select(Review.steam_review_id).where(
                Review.game_id == game.id, Review.steam_review_id.is_not(None)
            )
        ).all()
    }
    new_entries = []
    for entry in raw_reviews:
        review_id = entry.get("recommendationid")
        content = entry.get("review")
        if (
            review_id is None or not 1 <= len(str(review_id)) <= 32
            or str(review_id) in existing_ids
            or not isinstance(content, str) or len(content.strip()) < 10
        ):
            continue
        existing_ids.add(str(review_id))
        new_entries.append(entry)
        if len(new_entries) >= remaining:
            break
    if not new_entries:
        return 0

    steam_ids = [
        author_id
        for entry in new_entries
        if isinstance(entry.get("author"), dict)
        and (author_id := entry["author"].get("steamid"))
    ]
    profiles = get_player_summaries_batch(steam_ids)

    created = 0
    for entry in new_entries:
        text = (entry.get("review") or "").strip()
        if len(text) < 10:
            continue

        author = entry.get("author") if isinstance(entry.get("author"), dict) else {}
        profile = profiles.get(author.get("steamid"), {})
        playtime_minutes = author.get("playtime_at_review") or 0
        if not isinstance(playtime_minutes, (int, float)) or playtime_minutes < 0:
            playtime_minutes = 0

        review = Review(
            user_id=None,
            game_id=game.id,
            content=text[:8000],
            language="es",
            is_recommended=entry.get("voted_up"),
            hours_at_review=round(playtime_minutes / 60, 1),
            helpful_count=entry.get("votes_up") or 0,
            source="steam",
            author_name=(profile.get("personaname") or "Jugador de Steam")[:120],
            steam_review_id=str(entry["recommendationid"]),
        )
        db.add(review)
        apply_analysis(db, review, analyze_review(review))
        created += 1

    if created:
        db.flush()
        recompute_game_aggregates(db, game.id)
        # Las horas de los reseñadores recién importados mueven la mediana:
        # es la fuente de duración del filtro "¿cuánto tiempo tenés?".
        recompute_median_review_hours(db, game.id)
        if commit:
            from app.services.steam_catalog_service import bump_catalog_revision
            bump_catalog_revision(db)
            db.commit()
    return created


def import_game(db: Session, steam_app_id: int) -> Game | None:
    """Importa un juego de Steam al catálogo, con sus reseñas reales.

    Si ya estaba importado lo devuelve sin volver a pedirlo. Devuelve ``None``
    cuando Steam no reconoce el AppID o el AppID no corresponde a un juego
    (DLC, banda sonora, demo, hardware): la tienda los mezcla con juegos en
    listados como "más vendidos".
    """
    existing = get_game_by_steam_app_id(db, steam_app_id)
    if existing:
        return existing

    try:
        data = get_app_details(steam_app_id)
    except SteamUnavailable:
        # Importar es opcional y reintentable: sin Steam simplemente no se
        # importa nada ahora, sin distinguirlo de un AppID inexistente.
        return None
    if not data or data.get("type") != "game":
        return None

    parsed = parse_steam_game(data)
    genres = parsed.pop("genres", [])
    tags = parsed.pop("tags", [])

    # El slug es único: si el juego ya está en el catálogo por otra fuente
    # (el dataset local, RAWG) se le adosa el AppID en lugar de fallar.
    base_slug = parsed["slug"]
    collision = 0
    while db.scalar(select(Game.id).where(Game.slug == parsed["slug"])) is not None:
        collision += 1
        suffix = f"-{steam_app_id}" + (f"-{collision}" if collision > 1 else "")
        parsed["slug"] = base_slug[:120 - len(suffix)].rstrip("-") + suffix

    game = Game(**parsed, steam_app_id=steam_app_id, steam_synced_at=datetime.now(timezone.utc))
    # El juego entra a la sesión antes de asociarle géneros y etiquetas:
    # `_get_or_create` hace flush, y si el Game todavía estuviera fuera de la
    # sesión, SQLAlchemy descartaría silenciosamente la asociación.
    db.add(game)
    game.genres = [_get_or_create(db, Genre, name) for name in genres]
    game.tags = [_get_or_create(db, Tag, name) for name in tags]

    entry = _catalog_entry(db, game)
    entry.status = "ready"
    entry.last_attempt_at = datetime.now(timezone.utc)
    entry.next_attempt_at = entry.last_attempt_at + timedelta(minutes=settings.STEAM_SYNC_TTL_MINUTES)
    entry.attempts = 0
    entry.last_error = None
    db.flush()
    import_reviews(db, game, steam_app_id, commit=False)
    from app.services.steam_catalog_service import bump_catalog_revision
    bump_catalog_revision(db)
    db.commit()
    db.refresh(game)
    invalidate_engine()
    return game


def refresh_game(db: Session, game: Game) -> bool:
    """Refresco de fondo que conserva la ficha y todas sus interacciones.

    False significa exclusivamente que una respuesta válida identifica un
    producto de otro tipo. Una ficha inaccesible queda unavailable y se
    reintenta; no se inventa una fecha de sincronización exitosa.
    """
    if game.steam_app_id is None:
        return True
    entry = _catalog_entry(db, game)
    try:
        data = get_app_details(game.steam_app_id)
    except SteamUnavailable as error:
        _record_unavailable(db, entry, "No se pudo obtener una ficha válida de Steam", error.retry_after_seconds)
        raise
    if data is None:
        _record_unavailable(db, entry, "La ficha no está disponible públicamente en Steam")
        return True
    if not isinstance(data, dict) or not isinstance(data.get("type"), str) or not data["type"].strip():
        _record_unavailable(db, entry, "Steam devolvió una ficha con formato inválido")
        raise SteamUnavailable("Steam devolvió una ficha con formato inválido")
    if data["type"] != "game":
        entry.status = "non_game"
        entry.last_attempt_at = datetime.now(timezone.utc)
        entry.next_attempt_at = None
        entry.last_error = "La tienda identifica este producto como otro tipo de aplicación"
        from app.services.steam_catalog_service import bump_catalog_revision
        bump_catalog_revision(db)
        db.commit()
        invalidate_engine()
        return False

    parsed = parse_steam_game(data)
    # Publicar las capturas oficiales antes del procesamiento de reseñas.
    # No marcar la ficha completa ni cambiar su fecha de sincronización:
    # un fallo posterior conserva las imágenes y deja el resto en reintento.
    if game.screenshots != parsed["screenshots"]:
        game.screenshots = parsed["screenshots"]
        db.commit()
    genres = parsed.pop("genres", [])
    tags = parsed.pop("tags", [])
    # Preservar URLs y votos comunitarios al refrescar categorías de Steam.
    parsed.pop("name", None)
    parsed.pop("slug", None)
    for field, value in parsed.items():
        setattr(game, field, value)
    game.genres = [_get_or_create(db, Genre, name) for name in genres]
    community = [tag for tag in game.tags if tag.kind == "community"]
    platform_tags = [_get_or_create(db, Tag, name) for name in tags]
    game.tags = list(dict.fromkeys(community + platform_tags))

    totals = get_review_totals(game.steam_app_id)
    if totals is not None:
        game.steam_total_reviews, game.steam_positive_reviews = totals

    import_reviews(db, game, game.steam_app_id, commit=False)
    recompute_game_aggregates(db, game.id)
    now = datetime.now(timezone.utc)
    game.steam_synced_at = now
    entry.status = "ready"
    entry.last_attempt_at = now
    entry.next_attempt_at = now + timedelta(minutes=settings.STEAM_SYNC_TTL_MINUTES)
    entry.attempts = 0
    entry.last_error = None
    entry.priority = 0
    from app.services.steam_catalog_service import bump_catalog_revision
    bump_catalog_revision(db)
    db.commit()
    db.refresh(game)
    invalidate_engine()
    return True


def maybe_refresh(db: Session, game: Game) -> bool:
    """Devuelve datos locales al instante y prioriza el trabajo de fondo.

    No realiza pedidos HTTP desde una ficha, el buscador o el asistente.
    False indica una clasificación non_game previamente confirmada.
    """
    if game.steam_app_id is None:
        return True
    from app.models.steam_catalog import SteamCatalogEntry
    entry = db.get(SteamCatalogEntry, game.steam_app_id)
    if entry is not None and entry.status == "non_game":
        return False
    ttl = timedelta(minutes=settings.STEAM_SYNC_TTL_MINUTES)
    if game.steam_synced_at is not None:
        last_sync = game.steam_synced_at
        if last_sync.tzinfo is None:
            last_sync = last_sync.replace(tzinfo=timezone.utc)
        if game.screenshots is not None and datetime.now(timezone.utc) - last_sync < ttl and (entry is None or entry.status == "ready"):
            return True
    # Una galería nueva solicitada por el usuario precede a las tareas
    # normales del catálogo, respetando el backoff si Steam no responde.
    priority = 200 if game.is_enriched and game.screenshots is None else 100
    queue_game_refresh(db, game, priority=priority)
    db.commit()
    return True


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
            user.steam_username = (profile.get("personaname") or "")[:100] or None
            user.steam_avatar_url = profile.get("avatarfull")

    db.commit()
    db.refresh(user)
    return user
