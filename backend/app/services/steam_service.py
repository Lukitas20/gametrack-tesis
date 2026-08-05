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

import json
import re
import time
import unicodedata
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.ml.analytics import analyze_review, apply_analysis
from app.models import (
    Game,
    Genre,
    Review,
    SteamCatalogEntry,
    SteamSyncState,
    Tag,
    User,
)
from app.services.interaction_service import recompute_game_aggregates

TIMEOUT = 15.0
CATALOG_PAGE_SIZE = 50_000


class SteamCatalogError(RuntimeError):
    """Error accionable durante la sincronizacion oficial del catalogo."""

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
        response = _http_client().get(
            f"{settings.STEAM_STORE_BASE}/appdetails",
            params={"appids": steam_app_id, "l": "spanish"},
        )
    except httpx.HTTPError:
        return None

    if response.status_code != 200:
        return None

    payload = response.json().get(str(steam_app_id), {})
    if not payload.get("success"):
        return None
    return payload.get("data")


def get_store_catalog_page(
    last_appid: int = 0,
    if_modified_since: int = 0,
    max_results: int = CATALOG_PAGE_SIZE,
) -> dict:
    """Una pagina del indice oficial de juegos de Steam.

    ``IStoreService/GetAppList`` es distinto del viejo ``ISteamApps``: permite
    filtrar solo juegos, paginar y pedir cambios desde una fecha. Valve exige
    una Web API key aun cuando la informacion devuelta sea publica.
    """
    if not settings.STEAM_API_KEY:
        raise SteamCatalogError(
            "Falta STEAM_API_KEY en backend/.env; el catalogo completo la requiere"
        )

    request = {
        "include_games": True,
        "include_dlc": False,
        "include_software": False,
        "include_videos": False,
        "include_hardware": False,
        "last_appid": max(0, last_appid),
        "max_results": min(max(1, max_results), CATALOG_PAGE_SIZE),
    }
    if if_modified_since > 0:
        request["if_modified_since"] = if_modified_since

    try:
        response = _http_client().get(
            f"{settings.STEAM_API_BASE}/IStoreService/GetAppList/v1/",
            params={"key": settings.STEAM_API_KEY, "input_json": json.dumps(request)},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SteamCatalogError(
            f"Steam rechazo la consulta del catalogo (HTTP {exc.response.status_code})"
        ) from None
    except httpx.HTTPError as exc:
        raise SteamCatalogError(f"No se pudo contactar a Steam: {exc}") from None

    try:
        payload = response.json().get("response", {})
    except ValueError as exc:
        raise SteamCatalogError("Steam devolvio una respuesta que no es JSON") from None

    apps = payload.get("apps") or []
    if not isinstance(apps, list):
        raise SteamCatalogError("Steam devolvio un formato de catalogo inesperado")

    returned_last_appid = payload.get("last_appid")
    if returned_last_appid is None and apps:
        returned_last_appid = apps[-1].get("appid", last_appid)

    return {
        "apps": apps,
        "last_appid": int(returned_last_appid or last_appid),
        "have_more_results": bool(payload.get("have_more_results", False)),
    }


_SEARCH_APPID_RE = re.compile(r'data-ds-appid="(\d+)"')


def _store_get_with_retries(url: str, *, params: dict, headers: dict | None = None):
    """La tienda a veces corta el TLS; reintenta antes de rendirse."""
    for attempt in range(3):
        try:
            return _http_client().get(url, params=params, headers=headers)
        except httpx.HTTPError:
            if attempt < 2:
                time.sleep(attempt + 1)
    return None


def _featured_appids() -> list[int]:
    """Los títulos más reconocibles: destacados y en oferta de la tienda.

    Se excluye a propósito "new_releases": es donde entra la mayor parte del
    shovelware (cualquiera puede publicar ahí), y ensucia más de lo que suma.
    """
    response = _store_get_with_retries(
        f"{settings.STEAM_STORE_BASE}/featuredcategories",
        params={"l": "spanish"},
    )
    if response is None or response.status_code != 200:
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
    response = _store_get_with_retries(
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
    if response is None or response.status_code != 200:
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
            raise SteamCatalogError(
                "Steam Store no respondió al construir el ranking popular; "
                "el plan no fue modificado y se puede reintentar más tarde"
            )
        for appid in batch:
            if appid not in seen:
                seen.add(appid)
                appids.append(appid)
        start += page_size
        if len(appids) < limit:
            time.sleep(delay)
    return appids[:limit]


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
    payload = response.json()
    if payload.get("success") != 1:
        return []
    return payload.get("reviews", [])


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
# Indice masivo y checkpoints
# ---------------------------------------------------------------------------


def _has_useful_metadata(game: Game) -> bool:
    return bool(game.description or game.background_image or game.genres or game.tags)


def _available_slug(base: str, steam_app_id: int, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    candidate = f"{base}-{steam_app_id}"
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{steam_app_id}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def upsert_store_catalog_page(db: Session, apps: list[dict]) -> dict[str, int]:
    """Guarda una pagina basica sin pedir una ficha individual por juego.

    Tambien enlaza por nombre exacto un juego local/RAWG que todavia no tenga
    AppID. Esa conciliacion conservadora evita duplicar los titulos del seed
    actual sin mezclar ediciones con nombres diferentes.
    """
    games = list(db.scalars(select(Game)))
    by_appid = {game.steam_app_id: game for game in games if game.steam_app_id}
    used_slugs = {game.slug for game in games}

    by_name_candidates: dict[str, list[Game]] = {}
    for game in games:
        if game.steam_app_id is None:
            by_name_candidates.setdefault(game.name.casefold().strip(), []).append(game)
    by_name = {
        name: matches[0] for name, matches in by_name_candidates.items() if len(matches) == 1
    }

    entries = {
        entry.steam_app_id: entry for entry in db.scalars(select(SteamCatalogEntry))
    }
    stats = {"created": 0, "linked": 0, "updated": 0, "ignored": 0}

    for item in apps:
        try:
            appid = int(item.get("appid", 0))
        except (TypeError, ValueError):
            appid = 0
        name = str(item.get("name") or "").strip()
        if appid <= 0 or not name:
            stats["ignored"] += 1
            continue

        last_modified = item.get("last_modified")
        try:
            last_modified = int(last_modified) if last_modified is not None else None
        except (TypeError, ValueError):
            last_modified = None

        game = by_appid.get(appid)
        if game is None:
            game = by_name.pop(name.casefold(), None)
            if game is not None:
                game.steam_app_id = appid
                by_appid[appid] = game
                stats["linked"] += 1
            else:
                game = Game(
                    steam_app_id=appid,
                    name=name,
                    slug=_available_slug(slugify(name), appid, used_slugs),
                )
                db.add(game)
                by_appid[appid] = game
                stats["created"] += 1
        else:
            # El nombre de tienda puede corregirse con el tiempo. El slug se
            # mantiene estable para no romper enlaces ya compartidos.
            game.name = name
            stats["updated"] += 1

        entry = entries.get(appid)
        if entry is None:
            entry = SteamCatalogEntry(
                steam_app_id=appid,
                game=game,
                last_modified=last_modified,
                metadata_status="complete" if _has_useful_metadata(game) else "pending",
            )
            db.add(entry)
            entries[appid] = entry
        else:
            changed = (
                last_modified is not None
                and entry.last_modified is not None
                and last_modified > entry.last_modified
            )
            entry.game = game
            if last_modified is not None:
                entry.last_modified = last_modified
            if changed and entry.metadata_status == "complete":
                entry.metadata_status = "pending"

    db.commit()
    return stats


def get_sync_state(db: Session) -> SteamSyncState:
    state = db.get(SteamSyncState, "catalog")
    if state is None:
        state = SteamSyncState(key="catalog")
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def mark_catalog_page_synced(
    db: Session,
    state: SteamSyncState,
    *,
    last_appid: int,
    if_modified_since: int,
    complete: bool,
) -> None:
    """Persiste el cursor despues de cada pagina importada."""
    state.last_appid = 0 if complete else last_appid
    state.if_modified_since = 0 if complete else if_modified_since
    if complete:
        state.completed_at = datetime.now(UTC)
    db.commit()


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
        "genres": _dedupe_names(genres),
        "tags": _dedupe_names(tags)[:8],
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


def import_reviews(db: Session, game: Game, steam_app_id: int) -> int:
    """Trae reseñas reales de Steam para un juego recién importado y las
    analiza con el mismo módulo NLP que las reseñas escritas en GameTrack.

    No pertenecen a ningún usuario de la plataforma (``user_id`` nulo); se
    identifican por el nombre de perfil de Steam del autor, resuelto en un
    único pedido en lote para no hacer una llamada por reseña.
    """
    # La API de reseñas no ofrece un identificador que el modelo local guarde.
    # Hasta incorporar sincronización incremental, no repetimos una importación
    # ya terminada para evitar duplicar las mismas reseñas en cada visita.
    already_imported = db.scalar(
        select(func.count(Review.id)).where(
            Review.game_id == game.id, Review.source == "steam"
        )
    ) or 0
    if already_imported:
        return 0

    raw_reviews = get_app_reviews(steam_app_id)
    if not raw_reviews:
        return 0

    steam_ids = [
        author_id
        for entry in raw_reviews
        if (author_id := (entry.get("author") or {}).get("steamid"))
    ]
    profiles = get_player_summaries_batch(steam_ids)

    created = 0
    for entry in raw_reviews:
        text = (entry.get("review") or "").strip()
        if len(text) < 10:
            continue

        author = entry.get("author") or {}
        profile = profiles.get(author.get("steamid"), {})
        playtime_minutes = author.get("playtime_at_review") or 0

        review = Review(
            user_id=None,
            game_id=game.id,
            content=text[:8000],
            language="es",
            is_recommended=entry.get("voted_up"),
            hours_at_review=round(playtime_minutes / 60, 1),
            helpful_count=entry.get("votes_up") or 0,
            source="steam",
            author_name=profile.get("personaname") or "Jugador de Steam",
        )
        db.add(review)
        apply_analysis(db, review, analyze_review(review))
        created += 1

    if created:
        db.flush()
        recompute_game_aggregates(db, game.id)
        db.commit()
    return created


def import_game(
    db: Session,
    steam_app_id: int,
    *,
    include_reviews: bool = True,
    refresh: bool = False,
) -> Game | None:
    """Importa un juego de Steam al catálogo, con sus reseñas reales.

    Si ya estaba importado lo devuelve sin volver a pedirlo. Devuelve ``None``
    cuando Steam no reconoce el AppID o el AppID no corresponde a un juego
    (DLC, banda sonora, demo, hardware): la tienda los mezcla con juegos en
    listados como "más vendidos".
    """
    existing = get_game_by_steam_app_id(db, steam_app_id)
    if existing and existing.metadata_status == "complete" and not refresh:
        return existing

    data = get_app_details(steam_app_id)
    if not data or data.get("type") != "game":
        if existing and existing.steam_catalog_entry:
            existing.steam_catalog_entry.metadata_status = "failed"
            existing.steam_catalog_entry.last_error = (
                "Steam no devolvio una ficha de tipo juego"
            )
            db.commit()
        return None

    parsed = parse_steam_game(data)
    genres = parsed.pop("genres", [])
    tags = parsed.pop("tags", [])

    # El slug es único: si el juego ya está en el catálogo por otra fuente
    # (el dataset local, RAWG) se le adosa el AppID en lugar de fallar.
    if existing is None:
        if db.scalar(select(Game).where(Game.slug == parsed["slug"])):
            parsed["slug"] = f"{parsed['slug']}-{steam_app_id}"
        game = Game(**parsed, steam_app_id=steam_app_id)
        db.add(game)
    else:
        game = existing
        # El slug del indice es estable; el resto de la ficha si se actualiza.
        parsed.pop("slug", None)
        for field, value in parsed.items():
            setattr(game, field, value)
    # El juego entra a la sesión antes de asociarle géneros y etiquetas:
    # `_get_or_create` hace flush, y si el Game todavía estuviera fuera de la
    # sesión, SQLAlchemy descartaría silenciosamente la asociación.
    game.genres = [_get_or_create(db, Genre, name) for name in genres]
    game.tags = [_get_or_create(db, Tag, name) for name in tags]

    db.flush()
    entry = game.steam_catalog_entry
    if entry is None:
        entry = SteamCatalogEntry(steam_app_id=steam_app_id, game_id=game.id)
        db.add(entry)
        game.steam_catalog_entry = entry
    entry.metadata_status = "complete"
    entry.last_error = None
    entry.enriched_at = datetime.now(UTC)

    db.commit()
    db.refresh(game)

    # Una ficha nueva o actualizada cambia el corpus de contenido aunque la
    # cantidad de juegos permanezca igual.
    from app.ml.recommender import invalidate_engine

    invalidate_engine()

    if include_reviews:
        import_reviews(db, game, steam_app_id)
    return game


def enrich_catalog_game(db: Session, game_id: int) -> int:
    """Completa metadata y reseñas de una ficha sin bloquear su GET público.

    Devuelve la cantidad total de reseñas de Steam que quedaron disponibles.
    La función es idempotente: una segunda ejecución no vuelve a insertar las
    reseñas existentes.
    """
    game = db.get(Game, game_id)
    if game is None or game.steam_app_id is None:
        raise ValueError("El juego no pertenece al catálogo de Steam")

    if game.metadata_status != "complete":
        game = import_game(
            db,
            game.steam_app_id,
            include_reviews=False,
            refresh=True,
        )
        if game is None:
            raise ValueError("Steam no devolvió una ficha válida para este juego")

    import_reviews(db, game, game.steam_app_id)
    return int(
        db.scalar(
            select(func.count(Review.id)).where(
                Review.game_id == game.id, Review.source == "steam"
            )
        )
        or 0
    )


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
