"""Comprueba etiquetas públicas de anuncios desde el runtime, sin perfiles ni escrituras."""
import argparse
import json
import sys
from pathlib import Path

import truststore

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
truststore.inject_into_ssl()

from app.services.prelaunch_score_service import announced_game


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--appid', type=int, action='append', required=True)
    args = parser.parse_args()
    results = []
    for appid in args.appid:
        if not 0 < appid <= 4294967295:
            parser.error('AppID fuera de rango')
        game = announced_game(appid)
        if not game['tags_available'] or not game.get('tag_source'):
            raise RuntimeError(f'No hay etiquetas públicas verificables para {appid}')
        results.append({'appid': appid, 'name': game['name'], 'tags': game['tags'],
                        'tag_source': game['tag_source']})
    print(json.dumps({'status': 'verified', 'announcements': results}, ensure_ascii=False))


if __name__ == '__main__':
    main()
