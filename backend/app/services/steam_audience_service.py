"""Segmentos descriptivos de autores reales y consulta pública acotada.

El GET sólo agrega caché vigente. Cada POST consulta un juego para recuperar
identidades exactas y hasta cuatro historiales de dos páginas. No hay tareas
en memoria que puedan perderse al suspender una instancia de Cloud Run.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import time
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from app.models import (AudienceCollectionState, Game, Genre, Review,
                        SteamAudienceHistory, Tag, game_genres, game_tags)
from app.services import steam_review_service
from app.services.steam_review_authors import repair_game_authors

MIN_GROUP = 5
MAX_AUTHORS = 60
BATCH_SIZE = 4
MAX_PAGES = 2
TTL = 86400
RETRY = 3600
LEASE = 300
# Géneros ya visibles por separado; evitar duplicados y etiquetas de contenido
# adulto que no describen una experiencia de juego.
OMIT_TAGS = {"action", "adventure", "indie", "gore", "violent", "nudity", "sexual content"}
LABELS = {"Open World":"Mundo abierto", "Story Rich":"Historia elaborada", "Singleplayer":"Un jugador",
          "Multiplayer":"Multijugador", "Third Person":"Tercera persona", "Atmospheric":"Atmosféricos",
          "Shooter":"Shooters", "Co-op":"Cooperativos", "RPG":"RPG", "Souls-like":"Souls-like"}


def scope_games(db, *, game_id=None, studio=None):
    query = select(Game.id, Game.steam_app_id)
    if game_id is not None:
        query = query.where(Game.id == game_id)
    elif studio and studio.strip():
        query = query.where(func.lower(func.trim(Game.developer)) == studio.strip().lower())
    else:
        raise ValueError("Se requiere un juego o estudio")
    return list(db.execute(query.order_by(Game.id)))


def _scope_key(game_id, studio):
    return f"game:{game_id}" if game_id is not None else f"studio:{studio.strip().lower()}"


def _reviews(db, ids):
    return list(db.execute(select(Review.steam_author_id, Review.is_recommended)
        .where(Review.game_id.in_(ids), Review.source == "steam")))


def _valid_id(value):
    return isinstance(value, str) and value.isascii() and value.isdigit() and 1 <= len(value) <= 32


def _sample(rows):
    authors = {sid for sid, _ in rows if _valid_id(sid)}
    return sorted(authors, key=lambda sid: sha256(sid.encode()).hexdigest())[:MAX_AUTHORS]


def _fresh(history, now):
    return history and 0 <= now-history.checked_at < (TTL if history.status == "ok" else RETRY)


def _identity_pending(db, games, state, now):
    checks = state.identity_checks if state else {}
    ids = [gid for gid, appid in games if appid and now-float(checks.get(str(gid), 0)) >= TTL]
    return list(db.scalars(select(Review.game_id).where(Review.game_id.in_(ids), Review.source == "steam",
        Review.steam_review_id.is_not(None), Review.steam_author_id.is_(None))
        .group_by(Review.game_id).order_by(func.count(Review.id).desc(), Review.game_id)))


def history_report(db, *, game_id=None, studio=None, now=None):
    now = time.time() if now is None else now
    games = scope_games(db, game_id=game_id, studio=studio)
    rows = _reviews(db, [gid for gid, _ in games])
    selected = _sample(rows)
    histories = {h.steam_id: h for h in db.scalars(select(SteamAudienceHistory).where(SteamAudienceHistory.steam_id.in_(selected)))
                 if _fresh(h, now)}
    public = {sid: h for sid, h in histories.items() if h.status == "ok"}
    excluded = {appid for _, appid in games if appid}
    # Deduplicar por autor/app. No convertir horas en una opinión.
    positives = {sid: {i["appid"] for i in h.payload.get("items", [])
                       if type(i.get("appid")) is int and i.get("recommended") is True and i["appid"] not in excluded}
                 for sid, h in public.items()}
    apps = set().union(*positives.values()) if positives else set()
    traits = defaultdict(set)
    for appid, name in db.execute(select(Game.steam_app_id, Genre.name).join(game_genres, game_genres.c.game_id == Game.id)
        .join(Genre, Genre.id == game_genres.c.genre_id).where(Game.steam_app_id.in_(apps))):
        traits[appid].add(("genre", name))
    for appid, name in db.execute(select(Game.steam_app_id, Tag.name).join(game_tags, game_tags.c.game_id == Game.id)
        .join(Tag, Tag.id == game_tags.c.tag_id).where(Game.steam_app_id.in_(apps), Tag.kind == "community")):
        if name.lower() not in OMIT_TAGS:
            traits[appid].add(("tag", name))
    game_info = {a: (gid, name) for a, gid, name in db.execute(select(Game.steam_app_id, Game.id, Game.name).where(Game.steam_app_id.in_(apps)))}
    members = defaultdict(set)
    for sid, liked in positives.items():
        counts = Counter(trait for app in liked for trait in traits[app])
        for trait, count in counts.items():
            if count >= 2:
                members[trait].add(sid)
    votes = defaultdict(set)
    for sid, vote in rows:
        if type(vote) is bool:
            votes[sid].add(vote)
    groups = []
    for (kind, name), people in sorted(members.items(), key=lambda row: (-len(row[1]), row[0])):
        if len(people) < MIN_GROUP:
            continue
        reception = Counter("mixed" if len(votes[sid]) > 1 else "recommended" if votes[sid] == {True}
                            else "not_recommended" if votes[sid] == {False} else "unknown" for sid in people)
        examples = Counter(app for sid in people for app in positives[sid] if (kind, name) in traits[app])
        groups.append(dict(key=f"{kind}:{name}", label=LABELS.get(name, name), kind=kind, size=len(people),
            denominator=len(public), criterion="Recomendaron al menos dos juegos distintos con este rasgo, fuera de los juegos de este informe.",
            reception={key: reception[key] for key in ("recommended", "not_recommended", "mixed", "unknown")},
            examples=[dict(appid=app, game_id=game_info[app][0], name=game_info[app][1], authors=count)
                      for app, count in examples.most_common() if count >= MIN_GROUP][:3]))
    state = db.get(AudienceCollectionState, _scope_key(game_id, studio))
    checks_pending = _identity_pending(db, games, state, now)
    timestamps = [h.checked_at for h in histories.values()]
    total_positives = sum(map(len, positives.values()))
    return dict(identified_reviews=sum(_valid_id(sid) for sid, _ in rows),
        identified_authors=len({sid for sid, _ in rows if _valid_id(sid)}), sample_limit=MAX_AUTHORS,
        selected_authors=len(selected), checked_authors=len(histories), public_histories=len(public),
        private_histories=sum(h.status == "private" for h in histories.values()),
        unavailable_histories=sum(h.status == "unavailable" for h in histories.values()),
        partial_histories=sum(not h.payload.get("complete", False) for h in public.values()),
        pending_authors=len(selected)-len(histories), identity_games_pending=len(checks_pending),
        authors_with_two_other_recommendations=sum(len(apps) >= 2 for apps in positives.values()),
        other_recommendations=total_positives,
        recommendations_with_metadata=sum(bool(traits[app]) for liked in positives.values() for app in liked),
        oldest_checked_at=datetime.fromtimestamp(min(timestamps), timezone.utc).isoformat() if timestamps else None,
        newest_checked_at=datetime.fromtimestamp(max(timestamps), timezone.utc).isoformat() if timestamps else None,
        page_limit=MAX_PAGES, cache_hours=TTL//3600, groups=groups,
        selection="Hasta 60 autores únicos, ordenados por hash del identificador antes de consultar sus gustos; no por voto ni por similitud.",
        source="Reseñas públicas de Steam Community; géneros y etiquetas del catálogo guardado, importado de Steam/SteamSpy.")


def _state(db, key):
    value = db.get(AudienceCollectionState, key)
    if value is None:
        try:
            with db.begin_nested():
                db.add(AudienceCollectionState(key=key, lease_until=0, token="", identity_checks={}))
                db.flush()
        except IntegrityError:
            pass  # Otra instancia lo creó; la actualización condicional decide.
        value = db.get(AudienceCollectionState, key)
    return value


def collect_step(db, *, game_id=None, studio=None):
    games = scope_games(db, game_id=game_id, studio=studio)
    if not games:
        raise ValueError("No hay juegos en el alcance seleccionado")
    now, token = time.time(), uuid4().hex
    # Un único lote de red en toda la plataforma, también entre instancias.
    _state(db, "public-steam-collection")
    acquired = db.execute(update(AudienceCollectionState).where(
        AudienceCollectionState.key == "public-steam-collection", AudienceCollectionState.lease_until < now)
        .values(lease_until=now+LEASE, token=token)).rowcount
    db.commit()
    if not acquired:
        return {"status": "busy", "profiles_fetched": 0}
    fetched = 0
    try:
        state = _state(db, _scope_key(game_id, studio))
        pending_games = _identity_pending(db, games, state, now)
        if pending_games:
            gid = pending_games[0]
            # Sólo coincidencias exactas recommendationid; no alias ni texto.
            repair_game_authors(db, db.get(Game, gid), max_pages=2, hydrate=False)
            state.identity_checks = {**state.identity_checks, str(gid): time.time()}
            db.commit()
        selected = _sample(_reviews(db, [gid for gid, _ in games]))
        existing = {h.steam_id: h for h in db.scalars(select(SteamAudienceHistory).where(SteamAudienceHistory.steam_id.in_(selected)))}
        pending = [sid for sid in selected if not _fresh(existing.get(sid), now)]
        for sid in pending[:BATCH_SIZE]:
            items, page, complete, status = {}, 1, False, "unavailable"
            for _ in range(MAX_PAGES):
                result = steam_review_service.fetch_reviews(sid, page)
                status = result["status"]
                if status != "ok":
                    items = {}  # Nunca retener una página privada/inaccesible.
                    break
                for item in result.get("items", []):
                    if type(item.get("appid")) is int and 0 < item["appid"] <= 4294967295 and type(item.get("is_recommended")) is bool:
                        items[item["appid"]] = {"appid": item["appid"], "recommended": item["is_recommended"]}
                page = result.get("next_page")
                if not page:
                    complete = True
                    break
                time.sleep(1)
            history = existing.get(sid)
            if history is None:
                history = SteamAudienceHistory(steam_id=sid)
                db.add(history)
            history.checked_at, history.status = time.time(), status
            history.payload = {"items": list(items.values()), "complete": complete and status == "ok"}
            db.commit()
            fetched += 1
            time.sleep(0.5)
        report = history_report(db, game_id=game_id, studio=studio)
        return {"status": "more" if report["pending_authors"] or report["identity_games_pending"] else "ready", "profiles_fetched": fetched}
    finally:
        db.rollback()
        db.execute(update(AudienceCollectionState).where(AudienceCollectionState.key == "public-steam-collection",
            AudienceCollectionState.token == token).values(lease_until=0, token=""))
        db.commit()
