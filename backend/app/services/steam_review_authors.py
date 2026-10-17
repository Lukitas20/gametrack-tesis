"""Atribución de reseñas mediante identidades públicas verificadas de Steam.

Los nombres son metadatos de presentación: nunca se usan como señales de IA.
Sólo se asocia un autor al coincidir exactamente el recommendationid original.
"""
from __future__ import annotations

import json
import time
import unicodedata
from datetime import timezone

import httpx
from sqlalchemy import or_, select

from app.models import Review

UNKNOWN_NAMES = ("Jugador de Steam", "Autor no disponible")


def missing_author(name: str | None) -> bool:
    return not name or not name.strip() or name in UNKNOWN_NAMES


def public_name(profile: dict) -> str | None:
    value = profile.get("personaname")
    if not isinstance(value, str):
        return None
    # Conservar Unicode del alias; quitar controles que no son texto visible.
    value = "".join(c for c in value if not unicodedata.category(c).startswith('C')).strip()
    return value[:120] if value else None


def author_id(entry: dict) -> str | None:
    author = entry.get("author")
    value = author.get("steamid") if isinstance(author, dict) else None
    value = str(value) if value is not None else ""
    return value if value.isascii() and value.isdigit() and 1 <= len(value) <= 32 else None


def hydrate_authors(reviews: list[Review]) -> int:
    from app.services.steam_service import get_player_summaries_batch

    pending = [r for r in reviews if r.source == "steam" and r.steam_author_id
               and missing_author(r.author_name)]
    profiles = get_player_summaries_batch([r.steam_author_id for r in pending]) if pending else {}
    changed = 0
    for review in pending:
        name = public_name(profiles.get(review.steam_author_id, {}))
        if name:
            review.author_name = name
            changed += 1
    return changed


def _review_page(appid: int, cursor: str, *, start: int | None, end: int | None) -> tuple[list[dict], str | None]:
    from app.services.steam_service import _http_client

    query = {"appid": appid, "filter": 1, "languages": ["spanish"],
             "review_type": 0, "purchase_type": 1, "num_per_page": 100,
             "cursor": cursor, "filter_offtopic_activity": False}
    if start is not None and end is not None:
        query.update(date_range_start=start, date_range_end=end)
    try:
        response = _http_client().get(
            "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/",
            params={"input_json": json.dumps(query)},
        )
        if response.status_code != 200:
            return [], None
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return [], None
    body = payload.get("response") if isinstance(payload, dict) else None
    rows = body.get("reviews") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return [], None
    next_cursor = body.get("cursor")
    return [row for row in rows if isinstance(row, dict)], next_cursor if isinstance(next_cursor, str) else None


def repair_game_authors(db, game, *, max_pages: int = 5, delay: float = 1.0, hydrate: bool = True) -> dict:
    """Reparación acotada/reanudable sin importar ni reanalizar reseñas.

    Para muestras antiguas se consulta el intervalo original, si está guardado.
    Ante un fallo no se atribuye la reseña a otro autor ni se borra su nombre.
    El caller controla commit y se ejecuta fuera del camino de lectura de la UI.
    """
    report = {"examined": 0, "identities_recovered": 0, "names_recovered": 0, "pages": 0}
    if not game.steam_app_id:
        return report
    rows = list(db.scalars(select(Review).where(
        Review.game_id == game.id, Review.source == "steam",
        or_(Review.steam_author_id.is_(None), Review.author_name.is_(None), Review.author_name.in_(UNKNOWN_NAMES),
            Review.author_name == ""),
    )))
    report["examined"] = len(rows)
    report["names_recovered"] = hydrate_authors(rows) if hydrate else 0
    pending = {r.steam_review_id: r for r in rows if r.steam_review_id and not r.steam_author_id}
    if not pending:
        return report
    dated = [r.published_at.replace(tzinfo=timezone.utc).timestamp() for r in pending.values()
             if r.published_at is not None]
    if len(dated) == len(pending):
        start, end = int(min(dated)), int(max(dated))
    else:
        # Una reseña ya existía al importarla. Esa fecha sólo acota la
        # búsqueda: NO se presenta ni guarda como fecha de publicación.
        # Evita recorrer semanas de reseñas nuevas para reparar una muestra
        # histórica que originalmente se tomó de las más recientes.
        bounds = [(r.published_at or r.created_at).replace(tzinfo=timezone.utc).timestamp()
                  for r in pending.values() if r.published_at or r.created_at]
        start, end = (946684800, int(max(bounds))) if bounds else (None, None)
    cursor, seen = "*", set()
    for _ in range(max(0, min(max_pages, 100))):
        if cursor in seen:
            break
        seen.add(cursor)
        entries, next_cursor = _review_page(game.steam_app_id, cursor, start=start, end=end)
        report["pages"] += 1
        for entry in entries:
            review = pending.get(str(entry.get("recommendationid")))
            identity = author_id(entry)
            if review is not None and identity:
                review.steam_author_id = identity
                pending.pop(review.steam_review_id)
                report["identities_recovered"] += 1
        if not pending or not entries or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        if delay:
            time.sleep(max(1.0, delay))
    report["names_recovered"] += hydrate_authors(rows) if hydrate else 0
    return report
