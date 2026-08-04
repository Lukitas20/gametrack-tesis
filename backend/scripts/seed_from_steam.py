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

Uso:
    python scripts/seed_from_steam.py                # ~40 juegos populares
    python scripts/seed_from_steam.py --count 80      # más juegos
    python scripts/seed_from_steam.py --reset         # borra todo antes
    python scripts/seed_from_steam.py --delay 2       # más pausa entre pedidos

Steam limita la cantidad de pedidos por IP en poco tiempo: si el import se
corta a mitad de camino, subí ``--delay`` y volvé a correrlo (los juegos ya
importados se saltean, ``steam_service.import_game`` no los duplica).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import truststore

# Usa el almacén de certificados del sistema operativo (ver app/main.py).
truststore.inject_into_ssl()

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.base import SessionLocal, drop_db, init_db  # noqa: E402
from app.models import Game, Review, User, UserRole  # noqa: E402
from app.services import steam_service  # noqa: E402

DEMO_PASSWORD = "demo1234"
DEFAULT_COUNT = 40
DEFAULT_DELAY = 1.5


def seed_demo_accounts(db, studio: str | None) -> None:
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


def most_reviewed_developer(db) -> str | None:
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
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="juegos a importar")
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY, help="segundos entre pedidos a Steam"
    )
    parser.add_argument("--reset", action="store_true", help="borra la base antes de importar")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.reset:
            print("Eliminando el esquema existente...")
            drop_db()
        init_db()

        print("Buscando juegos populares en Steam (puede tardar si hacen falta varias páginas)...")
        appids = steam_service.get_top_seller_appids(args.count, delay=args.delay)
        if not appids:
            print("No se pudo contactar a Steam (o está limitando pedidos). Reintentá en unos minutos.")
            return 1
        print(f"{len(appids)} AppIDs encontrados. Importando con {args.delay}s entre pedidos...")

        imported = 0
        for index, appid in enumerate(appids, start=1):
            game = steam_service.import_game(db, appid)
            if game:
                imported += 1
                print(f"  [{index}/{len(appids)}] {game.name}")
            else:
                print(f"  [{index}/{len(appids)}] AppID {appid}: no disponible, se salta")
            if index < len(appids):
                time.sleep(args.delay)

        games_count = db.scalar(select(func.count(Game.id))) or 0
        reviews_count = db.scalar(select(func.count(Review.id))) or 0
        studio = most_reviewed_developer(db)

        print("Creando cuentas de demostración (sin historial: arranque en frío real)...")
        seed_demo_accounts(db, studio)

        print()
        print("Catálogo poblado con datos reales de Steam:")
        print(f"  juegos importados en esta corrida: {imported}/{len(appids)}")
        print(f"  juegos totales en el catálogo: {games_count}")
        print(f"  reseñas reales analizadas: {reviews_count}")
        print(f"  estudio de dev.demo: {studio or '(ninguno)'}")
        print()
        print("Cuentas de demostración (contraseña: demo1234):")
        print("  jugador.demo · sin valoraciones, arranque en frío real")
        print("  dev.demo     · panel de analítica del estudio con más juegos importados")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
