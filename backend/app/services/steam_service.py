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

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.ml.analytics import analyze_review, apply_analysis
from app.ml.recommender import invalidate_engine
from app.models import Game, Genre, Review, Tag, User
from app.services.interaction_service import recompute_game_aggregates

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


def get_app_list() -> list[dict]:
    """AppID y nombre de *todo* lo publicado en Steam, sin distinguir tipo.

    A diferencia del resto de esta integración (que descubre juegos de a
    poco por búsqueda o destacados), ``GetAppList`` sí es el catálogo
    completo en un único pedido: no hace falta clave ni paginar. La
    contrapartida es que mezcla juegos con DLC, bandas sonoras, demos y
    software — no hay forma de filtrar el tipo sin pedir la ficha de cada
    uno, que es exactamente el trabajo que se difiere a ``refresh_game``
    (ver ``scripts/import_steam_appindex.py``).
    """
    try:
        response = _http_client().get(f"{settings.STEAM_API_BASE}/ISteamApps/GetAppList/v2/")
    except httpx.HTTPError:
        return []
    if response.status_code != 200:
        return []
    return response.json().get("applist", {}).get("apps", [])


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
    raw_reviews = get_app_reviews(steam_app_id)
    if not raw_reviews:
        return 0

    existing_ids = {
        row[0]
        for row in db.execute(
            select(Review.steam_review_id).where(
                Review.game_id == game.id, Review.steam_review_id.is_not(None)
            )
        ).all()
    }
    new_entries = [
        entry
        for entry in raw_reviews
        if entry.get("recommendationid") is not None
        and str(entry["recommendationid"]) not in existing_ids
    ]
    if not new_entries:
        return 0

    steam_ids = [
        author_id
        for entry in new_entries
        if (author_id := (entry.get("author") or {}).get("steamid"))
    ]
    profiles = get_player_summaries_batch(steam_ids)

    created = 0
    for entry in new_entries:
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
            steam_review_id=str(entry["recommendationid"]),
        )
        db.add(review)
        apply_analysis(db, review, analyze_review(review))
        created += 1

    if created:
        db.flush()
        recompute_game_aggregates(db, game.id)
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

    data = get_app_details(steam_app_id)
    if not data or data.get("type") != "game":
        return None

    parsed = parse_steam_game(data)
    genres = parsed.pop("genres", [])
    tags = parsed.pop("tags", [])

    # El slug es único: si el juego ya está en el catálogo por otra fuente
    # (el dataset local, RAWG) se le adosa el AppID en lugar de fallar.
    if db.scalar(select(Game).where(Game.slug == parsed["slug"])):
        parsed["slug"] = f"{parsed['slug']}-{steam_app_id}"

    game = Game(**parsed, steam_app_id=steam_app_id, steam_synced_at=datetime.now(timezone.utc))
    # El juego entra a la sesión antes de asociarle géneros y etiquetas:
    # `_get_or_create` hace flush, y si el Game todavía estuviera fuera de la
    # sesión, SQLAlchemy descartaría silenciosamente la asociación.
    db.add(game)
    game.genres = [_get_or_create(db, Genre, name) for name in genres]
    game.tags = [_get_or_create(db, Tag, name) for name in tags]

    db.commit()
    db.refresh(game)

    import_reviews(db, game, steam_app_id)
    return game


def refresh_game(db: Session, game: Game) -> bool:
    """Vuelve a pedir la ficha de Steam de un juego ya importado (o de una
    ficha pendiente sin enriquecer todavía) y trae las reseñas nuevas que
    haya desde la última vez.

    A diferencia de ``import_game``, esto sí puede repetirse: actualiza la
    ficha existente en lugar de crear una, y ``import_reviews`` ya se ocupa
    de no duplicar reseñas. Se usa desde ``maybe_refresh`` para mantener el
    catálogo al día sin depender de un proceso aparte sondeando Steam.

    Devuelve ``False`` si el juego dejó de existir: una ficha pendiente
    (``get_app_list`` mezcla DLC, bandas sonoras y software con juegos, ver
    ``get_app_list``) que al pedir su detalle resulta no ser un juego se
    borra en vez de quedar eternamente pendiente. Un juego que ya estaba
    enriquecido nunca se borra por esto — sólo se deja de actualizar — para
    no tirar evidencia real (reseñas, valoraciones) por una falla puntual de
    Steam.
    """
    if game.steam_app_id is None:
        return True

    was_pending = not game.is_enriched
    data = get_app_details(game.steam_app_id)

    if data and data.get("type") == "game":
        parsed = parse_steam_game(data)
        genres = parsed.pop("genres", [])
        tags = parsed.pop("tags", [])
        # El nombre y el slug son la identidad pública del juego (URLs,
        # referencias del recomendador): no se pisan aunque Steam les haga
        # un pequeño retoque de redacción.
        parsed.pop("name", None)
        parsed.pop("slug", None)
        for field, value in parsed.items():
            setattr(game, field, value)
        game.genres = [_get_or_create(db, Genre, name) for name in genres]
        game.tags = [_get_or_create(db, Tag, name) for name in tags]
    elif was_pending:
        db.delete(game)
        db.commit()
        invalidate_engine()
        return False

    import_reviews(db, game, game.steam_app_id)

    game.steam_synced_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(game)
    # Los géneros, etiquetas o la descripción pudieron haber cambiado: el
    # contenido que ve el filtrado basado en contenido ya no es el mismo.
    invalidate_engine()
    return True


def maybe_refresh(db: Session, game: Game) -> bool:
    """Refresca un juego de Steam si hace más de ``STEAM_SYNC_TTL_MINUTES``
    que no se sincroniza, o si es una ficha pendiente que todavía no se
    enriqueció ni una vez (no espera al TTL en ese caso).

    Esto es lo que hace que el catálogo se sienta "en tiempo real" sin pagar
    el costo de golpear la API de Steam en cada vista de cada juego: como
    mucho se refresca una vez por ventana, y sólo los juegos que alguien
    efectivamente está mirando (no hay un barrido de fondo sobre todo el
    catálogo). Si Steam no responde, el juego sigue sirviéndose con los
    datos que ya tenía en vez de romper el pedido.

    Devuelve ``False`` si el juego se borró durante el refresco (ver
    ``refresh_game``) — quien llama debe tratarlo como si ya no existiera.
    """
    if game.steam_app_id is None:
        return True

    ttl = timedelta(minutes=settings.STEAM_SYNC_TTL_MINUTES)
    if game.steam_synced_at is not None:
        last_sync = game.steam_synced_at
        if last_sync.tzinfo is None:
            last_sync = last_sync.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last_sync < ttl:
            return True

    try:
        return refresh_game(db, game)
    except Exception:
        # El refresco es una mejora sobre la respuesta, no la respuesta en sí:
        # si Steam devuelve algo inesperado (o no responde), la página se
        # sirve igual con los datos que el juego ya tenía. `get_db` cierra la
        # sesión al terminar el request, lo que descarta cualquier cambio a
        # medio aplicar que no haya llegado a `commit()`.
        db.rollback()
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
            user.steam_username = profile.get("personaname")
            user.steam_avatar_url = profile.get("avatarfull")

    db.commit()
    db.refresh(user)
    return user
