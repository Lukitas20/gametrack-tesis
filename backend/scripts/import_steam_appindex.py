#!/usr/bin/env python
"""Sincroniza el índice oficial IStoreService/GetAppList de forma reanudable.

Requiere STEAM_API_KEY en backend/.env. Crea fichas livianas y una cola para
completarlas. Conserva el cursor después de cada página, y retoma sin borrar
las fichas ni las interacciones existentes. No usa un índice alternativo.

    python scripts/import_steam_appindex.py
    python scripts/import_steam_appindex.py --limit-pages 5
    python scripts/import_steam_appindex.py --page-size 1000 --delay 2

``import_stub_catalog`` se conserva para importar listas locales antiguas;
el comando principal usa exclusivamente la sincronización oficial.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import truststore

# Usa el almacén de certificados del sistema operativo (ver app/main.py).
truststore.inject_into_ssl()

# El catálogo real de Steam tiene nombres en japonés, chino y cirílico que la
# consola de Windows (cp1252) no sabe imprimir: sin esto, mostrar el progreso
# corta el proceso entero con UnicodeEncodeError.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.base import SessionLocal, init_db  # noqa: E402
from app.models import Game  # noqa: E402
from app.services import steam_service  # noqa: E402
from app.services.steam_catalog_service import sync_catalog  # noqa: E402

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
        "--limit-pages", "--max-pages", dest="max_pages",
        type=int,
        default=None,
        help="tope de páginas; otro llamado continúa desde el checkpoint guardado",
    )
    parser.add_argument(
        "--delay", type=float, default=2.0, help="segundos entre páginas del índice oficial"
    )
    parser.add_argument("--page-size", type=int, default=None, help="entradas por página (máximo 50000)")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        init_db()
        result = sync_catalog(db, max_pages=args.max_pages, page_size=args.page_size, delay=args.delay)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if result.get("busy"):
            print("Ya hay otro proceso actualizando este catálogo. Esperá a que termine.")
            return 2
        if result["partial"] and result["status"] == "running":
            print("Importación parcial guardada. Ejecutá de nuevo para continuar.")
        return 1 if result["status"] in ("error", "disabled") else 0
    except ValueError as exc:
        parser.error(str(exc))
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
