"""Actualiza etiquetas reales de fichas existentes; no importa usuarios ni opiniones."""
import argparse
import json
import sys
import time
from pathlib import Path

import truststore

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
truststore.inject_into_ssl()

from sqlalchemy import select
from app.db.database import SessionLocal
from app.models import Game
from app.models.steamspy import SteamSpySync
from app.services.steamspy_service import refresh_known_traits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--appid', type=int, action='append', required=True)
    args = parser.parse_args()
    appids = list(dict.fromkeys(args.appid))
    if len(appids) > 10 or any(not 0 < appid <= 4294967295 for appid in appids):
        parser.error('Indicá hasta diez AppID válidos.')
    with SessionLocal() as db:
        games = [db.scalar(select(Game).where(Game.steam_app_id == appid)) for appid in appids]
        if any(game is None for game in games):
            parser.error('Las fichas deben existir previamente en el catálogo.')
        results = []
        for position, game in enumerate(games):
            if position:
                time.sleep(1)
            if not refresh_known_traits(db, game, force=True):
                raise RuntimeError(f'No se actualizaron los rasgos de {game.steam_app_id}; se conservan los anteriores.')
            row = db.get(SteamSpySync, game.steam_app_id)
            results.append({'appid': game.steam_app_id, 'tags': sorted(row.raw['tags'])})
    print(json.dumps({'status': 'updated', 'games': results}, ensure_ascii=True))


if __name__ == '__main__':
    main()
