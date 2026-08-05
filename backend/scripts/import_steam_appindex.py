#!/usr/bin/env python
"""Importa el índice completo de Steam como fichas pendientes.

A diferencia de ``seed_from_steam.py`` (que trae la ficha completa y las
reseñas de un puñado de juegos populares), esto usa ``GetAppList`` — el único
endpoint de Steam que sí devuelve *todo* en un único pedido, sin paginar ni
necesitar clave — para crear una fila liviana por cada AppID: sólo nombre y
AppID, sin géneros, descripción ni reseñas todavía.

Eso es lo que hace que el catálogo y el buscador cubran todo Steam desde el
primer momento: cada ficha pendiente se completa sola (``steam_service.
maybe_refresh``) la primera vez que alguien la abre en la aplicación, y si
resulta no ser un juego de verdad (GetAppList mezcla DLC, bandas sonoras y
software) se descarta en ese momento en vez de quedar pendiente para siempre.

Es puramente aditivo: no toca los juegos que ya tienen ficha completa (del
dataset curado, de RAWG o ya importados de Steam), y correrlo de nuevo sólo
agrega los AppIDs nuevos que Steam haya sumado desde la última vez.

Uso:
    python scripts/import_steam_appindex.py             # todo el índice
    python scripts/import_steam_appindex.py --limit 5000 # para probar rápido
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
    args = parser.parse_args()

    print("Pidiendo el listado completo de aplicaciones de Steam (GetAppList)...")
    apps = steam_service.get_app_list()
    if not apps:
        print("No se pudo contactar a Steam. Reintentá en unos minutos.")
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
