#!/usr/bin/env python
"""Importa el índice casi completo de Steam como fichas pendientes.

A diferencia de ``seed_from_steam.py`` (que trae la ficha completa y las
reseñas de un puñado de juegos populares), esto usa el índice de SteamSpy
(steamspy.com/api.php?request=all) para crear una fila liviana por cada
AppID: sólo nombre y AppID, sin géneros, descripción ni reseñas todavía.

Steam mismo tenía un endpoint para esto (``GetAppList``), pero Valve lo dio
de baja — confirmado contra ``ISteamWebAPIUtil.GetSupportedAPIList``, que ya
no lo lista, y contra la propia URL, que devuelve 404. SteamSpy es la
alternativa: un índice comunitario no oficial, no de Valve, que pagina de a
1000 juegos por pedido (unos ~85-90 mil en total). Al ser un tercero puede
estar más lento o caído en cualquier momento; el script se corta con lo que
haya juntado hasta ahí en vez de fallar todo.

Eso es lo que hace que el catálogo y el buscador cubran casi todo Steam
desde el primer momento: cada ficha pendiente se completa sola
(``steam_service.maybe_refresh``) la primera vez que alguien la abre en la
aplicación, y si resulta no ser un juego de verdad (el índice mezcla DLC,
bandas sonoras y software) se descarta en ese momento en vez de quedar
pendiente para siempre.

Es puramente aditivo: no toca los juegos que ya tienen ficha completa (del
dataset curado, de RAWG o ya importados de Steam), y correrlo de nuevo sólo
agrega los AppIDs nuevos que hayan aparecido desde la última vez.

Uso:
    python scripts/import_steam_appindex.py                # todo el índice (~10-15 min)
    python scripts/import_steam_appindex.py --limit 5000    # para probar rápido
    python scripts/import_steam_appindex.py --max-pages 5   # ídem, por páginas
    python scripts/import_steam_appindex.py --delay 3        # más pausa entre páginas
"""

from __future__ import annotations

import argparse
import sys
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

from app.db.base import SessionLocal, init_db  # noqa: E402
from app.models import Game  # noqa: E402
from app.services import steam_service  # noqa: E402

CHUNK_SIZE = 2000


def import_stub_catalog(
    db: Session, apps: list[dict[str, Any]], limit: int | None = None
) -> tuple[int, int]:
    """Crea una fila mínima por AppID nuevo. Devuelve (creadas, salteadas)."""
    existing_appids = {
        row[0]
        for row in db.execute(
            select(Game.steam_app_id).where(Game.steam_app_id.is_not(None))
        ).all()
    }
    existing_slugs = {row[0] for row in db.execute(select(Game.slug)).all()}

    created = 0
    skipped = 0
    pending: list[Game] = []

    for app in apps:
        if limit is not None and created >= limit:
            break

        appid = app.get("appid")
        name = (app.get("name") or "").strip()
        if not appid or not name or appid in existing_appids:
            skipped += 1
            continue

        slug = steam_service.slugify(name)
        if slug in existing_slugs:
            slug = f"{slug}-{appid}"
        existing_appids.add(appid)
        existing_slugs.add(slug)

        pending.append(Game(steam_app_id=appid, slug=slug, name=name))
        created += 1

        if len(pending) >= CHUNK_SIZE:
            db.add_all(pending)
            db.commit()
            pending = []
            print(f"  {created} fichas pendientes creadas...")

    if pending:
        db.add_all(pending)
        db.commit()

    return created, skipped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Importa el índice completo de AppIDs de Steam como fichas pendientes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="tope de fichas nuevas a crear (para pruebas)"
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="tope de páginas de SteamSpy a pedir, 1000 juegos c/u (para pruebas)",
    )
    parser.add_argument(
        "--delay", type=float, default=1.5, help="segundos entre páginas de SteamSpy"
    )
    args = parser.parse_args()

    print("Pidiendo el índice de Steam a SteamSpy (steamspy.com, no es un servicio de Valve)...")
    apps = steam_service.get_app_list(delay=args.delay, max_pages=args.max_pages)
    if not apps:
        print("No se pudo contactar a SteamSpy. Reintentá en unos minutos.")
        return 1
    print(f"{len(apps)} entradas recibidas (juegos, DLC, software y bandas sonoras mezclados).")

    db = SessionLocal()
    try:
        init_db()
        created, skipped = import_stub_catalog(db, apps, limit=args.limit)
        total = db.scalar(select(func.count(Game.id))) or 0

        print()
        print("Índice de Steam importado:")
        print(f"  fichas pendientes nuevas: {created}")
        print(f"  salteadas (ya existían, o sin nombre/AppID): {skipped}")
        print(f"  juegos totales en el catálogo: {total}")
        print()
        print(
            "Cada ficha se completa sola (géneros, descripción, reseñas) la "
            "primera vez que alguien la abre en la aplicación."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
