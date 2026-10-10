"""Entrena en CPU y publica una versión nueva sin reemplazar modelos activos."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile

import httpx
import truststore

from app.db.base import SessionLocal
from app.ml.local_model import train_and_save


def validate_version(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", value) or value == "untrained":
        raise argparse.ArgumentTypeError("Usá una versión nueva, sin barras ni espacios.")
    return value


def upload_artifact(client: httpx.Client, *, bucket: str, version: str,
                    path: Path, token: str) -> None:
    # ifGenerationMatch=0 impide modificar una versión publicada, incluso con
    # dos ejecuciones concurrentes. La identidad sólo tiene objectCreator.
    response = client.post(
        f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o",
        params={"uploadType": "media", "name": f"versions/{version}/{path.name}",
                "ifGenerationMatch": "0"},
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/octet-stream"},
        content=path.read_bytes(),
    )
    response.raise_for_status()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-label", required=True, choices=("demo", "observed"))
    parser.add_argument("--version", required=True, type=validate_version)
    args = parser.parse_args(argv)
    bucket = os.environ.get("MODEL_BUCKET", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", bucket):
        parser.error("Falta MODEL_BUCKET válido.")
    truststore.inject_into_ssl()
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local_model.npz"
            with SessionLocal() as db:
                report = train_and_save(db, path, data_label=args.data_label)
            # Sólo funciona con la identidad del job; no requiere archivos de claves.
            with httpx.Client(timeout=10, trust_env=False) as metadata:
                response = metadata.get(
                    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                    headers={"Metadata-Flavor": "Google"},
                )
                response.raise_for_status()
                token = response.json()["access_token"]
            with httpx.Client(timeout=120) as storage:
                # Subir el informe primero y activar la versión sólo si el job
                # finaliza bien; el archivo npz se publica completo al terminar.
                for artifact in (path.with_suffix(".json"), path):
                    upload_artifact(storage, bucket=bucket, version=args.version,
                                    path=artifact, token=token)
            print(json.dumps({"version": args.version, "report": report}, ensure_ascii=False))
        return 0
    except Exception as error:
        # No volcar URLs de base, encabezados ni respuestas con credenciales.
        print(f"No se pudo publicar el modelo ({type(error).__name__}). Revisá los logs y usá una versión nueva.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
