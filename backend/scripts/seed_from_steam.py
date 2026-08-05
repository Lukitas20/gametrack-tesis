#!/usr/bin/env python
"""Puebla GameTrack con un catálogo 100% real importado de Steam.

A diferencia de ``seed_data.py``, acá no hay nada sintético: ni jugadores con
perfiles de gusto fabricados, ni ratings artificiales, ni reseñas generadas.
Los juegos, sus fichas y sus reseñas salen de la API pública de Steam
(``app/services/steam_service.py``), analizadas por el mismo módulo NLP que
procesa las reseñas escritas en la plataforma.

El recomendador arranca en frío de verdad: sin ratings no hay señal
colaborativa hasta que cuentas reales empiecen a puntuar juegos. Lo único
que hay desde el arranque es el fallback por popularidad, calculado sobre
reseñas reales.

Dos modos, por ``--source``:

    snapshot (default) — lee ``data/steam_catalog.json``, ya versionado en el
        repo, y siembra la base con eso. No pega contra Steam: arranca en
        segundos y da el mismo catálogo a todo el equipo, sin necesitar
        ``STEAM_API_KEY`` ni esperar a Steam.

    live — pega contra Steam de verdad, trae juegos y reseñas nuevas, y
        además ACTUALIZA ``data/steam_catalog.json`` con lo que trajo. Lo
        corre quien quiera ampliar o refrescar el catálogo compartido; el
        JSON resultante se commitea para que el resto del equipo lo tenga
        sin volver a pegarle a Steam.

Uso:
    python scripts/seed_from_steam.py                        # snapshot compartido, sin red
    python scripts/seed_from_steam.py --reset                # borra todo antes de sembrar
    python scripts/seed_from_steam.py --source live           # ~150 juegos, actualiza el snapshot
    python scripts/seed_from_steam.py --source live --count 300
    python scripts/seed_from_steam.py --source live --delay 2 # más pausa entre pedidos

Steam limita la cantidad de pedidos por IP en poco tiempo: si un ``--source
live`` se corta a mitad de camino, subí ``--delay`` y volvé a correrlo — tanto
la corrida de Steam como la siembra en la base son incrementales, no
duplican lo que ya está.

Literalmente "todos" los juegos de Steam (cientos de miles, la mayoría sin
relevancia) no es un objetivo realista ni deseable: Steam ni siquiera expone
un endpoint así (ver ``steam_service.get_top_seller_appids``). El criterio es
"todos los que importan": los más vendidos y más reseñados, con ``--count``
para ajustar cuántos.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import truststore

# Usa el almacén de certificados del sistema operativo (ver app/main.py).
truststore.inject_into_ssl()

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import DATA_DIR  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.base import SessionLocal, drop_db, init_db  # noqa: E402
from app.ml.analytics import analyze_review, apply_analysis  # noqa: E402
from app.models import Game, Genre, Review, Tag, User, UserRole  # noqa: E402
from app.services import steam_service  # noqa: E402
from app.services.interaction_service import recompute_game_aggregates  # noqa: E402

DEMO_PASSWORD = "demo1234"
DEFAULT_COUNT = 150
DEFAULT_DELAY = 1.5
SNAPSHOT_PATH = DATA_DIR / "steam_catalog.json"


# ---------------------------------------------------------------------------
# Steam en vivo -> estructura serializable
# ---------------------------------------------------------------------------


def fetch_catalog_from_steam(count: int, delay: float) -> list[dict[str, Any]]:
    """Trae juegos reales de Steam con sus reseñas ya resueltas (nombre de
    autor incluido), listos para exportar a JSON o insertar en la base.
    """
    print("Buscando juegos populares en Steam (puede tardar si hacen falta varias páginas)...")
    appids = steam_service.get_top_seller_appids(count, delay=delay)
    if not appids:
        print("No se pudo contactar a Steam (o está limitando pedidos). Reintentá en unos minutos.")
        return []
    print(f"{len(appids)} AppIDs encontrados. Trayendo fichas y reseñas ({delay}s entre pedidos)...")

    games: list[dict[str, Any]] = []
    for index, appid in enumerate(appids, start=1):
        data = steam_service.get_app_details(appid)
        if not data or data.get("type") != "game":
            print(f"  [{index}/{len(appids)}] AppID {appid}: no disponible, se salta")
            if index < len(appids):
                time.sleep(delay)
            continue

        parsed = steam_service.parse_steam_game(data)
        raw_reviews = steam_service.get_app_reviews(appid)

        steam_ids = [
            author_id
            for entry in raw_reviews
            if (author_id := (entry.get("author") or {}).get("steamid"))
        ]
        profiles = steam_service.get_player_summaries_batch(steam_ids)

        reviews: list[dict[str, Any]] = []
        for entry in raw_reviews:
            text = (entry.get("review") or "").strip()
            if len(text) < 10 or entry.get("recommendationid") is None:
                continue
            author = entry.get("author") or {}
            profile = profiles.get(author.get("steamid"), {})
            playtime_minutes = author.get("playtime_at_review") or 0
            reviews.append(
                {
                    "steam_review_id": str(entry["recommendationid"]),
                    "author_name": profile.get("personaname") or "Jugador de Steam",
                    "content": text[:8000],
                    "is_recommended": entry.get("voted_up"),
                    "hours_at_review": round(playtime_minutes / 60, 1),
                    "helpful_count": entry.get("votes_up") or 0,
                }
            )

        released = parsed.get("released")
        games.append(
            {
                **parsed,
                "steam_app_id": appid,
                "released": released.isoformat() if released else None,
                "reviews": reviews,
            }
        )
        print(f"  [{index}/{len(appids)}] {parsed['name']}: {len(reviews)} reseñas")

        if index < len(appids):
            time.sleep(delay)

    return games


def export_snapshot(games: list[dict[str, Any]], path: Path) -> None:
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "games": games}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(f"Snapshot guardado en {path} ({len(games)} juegos). Commiteálo para compartirlo con el equipo.")


def load_snapshot(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(
            f"No hay snapshot en {path}. Alguien del equipo tiene que correr este script "
            "con --source live una vez y commitear el JSON resultante."
        )
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload["games"]


# ---------------------------------------------------------------------------
# Inserción en la base (compartida por --source live y --source snapshot)
# ---------------------------------------------------------------------------


def seed_catalog(db: Session, games_data: list[dict[str, Any]]) -> tuple[int, int]:
    """Inserta juegos y reseñas reales en la base.

    Incremental en los dos niveles: un juego que ya existe (mismo
    ``steam_app_id``) no se recrea, y una reseña que ya existe (mismo
    ``steam_review_id``) no se duplica. Correr esto de nuevo sobre una base
    ya sembrada sólo agrega lo que falte.
    """
    games_imported = 0
    reviews_imported = 0
    now = datetime.now(timezone.utc)

    for data in games_data:
        appid = data["steam_app_id"]
        game = steam_service.get_game_by_steam_app_id(db, appid)
        if game is None:
            slug = data["slug"]
            if db.scalar(select(Game).where(Game.slug == slug)):
                slug = f"{slug}-{appid}"
            game = Game(
                steam_app_id=appid,
                slug=slug,
                name=data["name"],
                description=data.get("description"),
                released=date.fromisoformat(data["released"]) if data.get("released") else None,
                developer=data.get("developer"),
                publisher=data.get("publisher"),
                platforms=data.get("platforms", []),
                background_image=data.get("background_image"),
                metacritic=data.get("metacritic"),
                steam_synced_at=now,
            )
            db.add(game)
            game.genres = [
                steam_service._get_or_create(db, Genre, name) for name in data.get("genres", [])
            ]
            game.tags = [
                steam_service._get_or_create(db, Tag, name) for name in data.get("tags", [])
            ]
            db.flush()
            games_imported += 1

        existing_ids = {
            row[0]
            for row in db.execute(
                select(Review.steam_review_id).where(
                    Review.game_id == game.id, Review.steam_review_id.is_not(None)
                )
            ).all()
        }
        for review_data in data.get("reviews", []):
            if review_data["steam_review_id"] in existing_ids:
                continue
            review = Review(
                user_id=None,
                game_id=game.id,
                content=review_data["content"],
                language="es",
                is_recommended=review_data.get("is_recommended"),
                hours_at_review=review_data.get("hours_at_review"),
                helpful_count=review_data.get("helpful_count", 0),
                source="steam",
                author_name=review_data.get("author_name") or "Jugador de Steam",
                steam_review_id=review_data["steam_review_id"],
            )
            db.add(review)
            apply_analysis(db, review, analyze_review(review))
            reviews_imported += 1

        db.flush()
        recompute_game_aggregates(db, game.id)

    db.commit()
    return games_imported, reviews_imported


def seed_demo_accounts(db: Session, studio: str | None) -> None:
    """Dos cuentas para poder entrar a la demo, sin historial fabricado.

    ``jugador.demo`` arranca igual que cualquier cuenta recién creada: es el
    arranque en frío real, no una simulación de uno. El estudio de
    ``dev.demo`` se elige entre los juegos efectivamente importados para que
    el panel de analítica no quede vacío.
    """
    password_hash = hash_password(DEMO_PASSWORD)
    db.add_all(
        [
            User(
                username="jugador.demo",
                email="jugador.demo@gametrack.app",
                hashed_password=password_hash,
                full_name="Cuenta Demo Jugador",
                role=UserRole.PLAYER,
            ),
            User(
                username="dev.demo",
                email="dev.demo@gametrack.app",
                hashed_password=password_hash,
                full_name="Cuenta Demo Desarrollador",
                role=UserRole.DEVELOPER,
                studio=studio,
            ),
        ]
    )
    db.commit()


def most_reviewed_developer(db: Session) -> str | None:
    """El estudio con más reseñas reales acumuladas, no con más juegos.

    Contar juegos favorece a las "fábricas" que publican decenas de títulos
    casi idénticos con pocas reseñas cada uno (ej. packs de "100 X Cats").
    Sumar reseñas reales aproxima mejor qué estudio es reconocible de
    verdad para elegirlo como cuenta de demostración.
    """
    totals: dict[str, int] = {}
    rows = db.execute(
        select(Game.developer, Game.reviews_count).where(Game.developer.is_not(None))
    )
    for developer, reviews_count in rows:
        totals[developer] = totals.get(developer, 0) + (reviews_count or 0)
    return max(totals, key=totals.get) if totals else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Importa un catálogo real de Steam a GameTrack.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        choices=["snapshot", "live"],
        default="snapshot",
        help="snapshot (default): lee data/steam_catalog.json, sin red. "
        "live: pega contra Steam y actualiza ese snapshot.",
    )
    parser.add_argument(
        "--count", type=int, default=DEFAULT_COUNT, help="juegos a traer (sólo --source live)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="segundos entre pedidos a Steam (sólo --source live)",
    )
    parser.add_argument("--reset", action="store_true", help="borra la base antes de sembrar")
    parser.add_argument(
        "--snapshot-path", type=Path, default=SNAPSHOT_PATH, help="ubicación del snapshot JSON"
    )
    args = parser.parse_args()

    if args.source == "live":
        games_data = fetch_catalog_from_steam(args.count, args.delay)
        if not games_data:
            return 1
        export_snapshot(games_data, args.snapshot_path)
    else:
        print(f"Leyendo el snapshot compartido de {args.snapshot_path} (sin pegarle a Steam)...")
        try:
            games_data = load_snapshot(args.snapshot_path)
        except FileNotFoundError as error:
            print(error)
            return 1

    db = SessionLocal()
    try:
        if args.reset:
            print("Eliminando el esquema existente...")
            drop_db()
        init_db()

        games_imported, reviews_imported = seed_catalog(db, games_data)

        games_count = db.scalar(select(func.count(Game.id))) or 0
        reviews_count = db.scalar(select(func.count(Review.id))) or 0
        studio = most_reviewed_developer(db)

        if (db.scalar(select(func.count(User.id))) or 0) == 0:
            print("Creando cuentas de demostración (sin historial: arranque en frío real)...")
            seed_demo_accounts(db, studio)

        print()
        print(f"Catálogo poblado con datos reales de Steam (fuente: {args.source}):")
        print(f"  juegos nuevos en esta corrida: {games_imported}")
        print(f"  reseñas nuevas en esta corrida: {reviews_imported}")
        print(f"  juegos totales en el catálogo: {games_count}")
        print(f"  reseñas reales totales: {reviews_count}")
        print(f"  estudio de dev.demo: {studio or '(ninguno)'}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
