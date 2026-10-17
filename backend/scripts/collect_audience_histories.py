"""Consulta acotada de historiales públicos reales para un juego/estudio.

No crea usuarios, reseñas, votos ni etiquetas. Sólo recupera identidades por
recommendationid exacto y guarda appid/voto de páginas públicas. La salida es
agregada; nunca imprime identificadores de autores ni textos de reseñas.
Requiere la migración b71f4a920c83. No entrena modelos ni toca sus artefactos.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import truststore
from app.db.database import SessionLocal
from app.services.steam_audience_service import collect_step, history_report
from app.schemas.analytics import SteamHistoryReport


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    scope=parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--studio")
    scope.add_argument("--game-id",type=int)
    parser.add_argument("--steps",type=int,default=16)
    args=parser.parse_args()
    if not 1 <= args.steps <= 30 or (args.game_id is not None and args.game_id < 1):
        parser.error("steps debe ser 1..30 y game-id positivo")
    truststore.inject_into_ssl()
    target={"game_id":args.game_id} if args.game_id else {"studio":args.studio}
    try:
        with SessionLocal() as db:
            for step in range(args.steps):
                result=collect_step(db,**target)
                report=history_report(db,**target)
                SteamHistoryReport.model_validate(report)
                print(json.dumps({"operation":"public_audience_histories","step":step+1,
                    "status":result["status"],"profiles_fetched":result["profiles_fetched"],
                    "checked_authors":report["checked_authors"],"groups":len(report["groups"]) }),flush=True)
                if result["status"] != "more":
                    break
            print(json.dumps({"operation":"public_audience_histories","result":"observed",
                "scope":target,"audience":report},ensure_ascii=False),flush=True)
        return 0
    except Exception:
        # HTTP/SQL puede incluir parámetros privados. No volcar la excepción.
        print(json.dumps({"operation":"public_audience_histories","result":"failed_no_private_details"}),flush=True)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
