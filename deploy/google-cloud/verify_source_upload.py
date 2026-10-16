"""Audita la lista de fuentes de Cloud Build sin leer secretos ni usar la red.

gcloud meta list-files-for-upload > /tmp/gametrack-upload.txt
python deploy/google-cloud/verify_source_upload.py --files-list /tmp/gametrack-upload.txt
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath

ALLOWED_MODELS = {
    "backend/models/review_model.npz", "backend/models/review_model.json",
    "backend/models/steam_affinity.json",
}
REQUIRED = ALLOWED_MODELS | {
    "Dockerfile", "backend/scripts/verify_review_model.py",
    "backend/scripts/verify_steam_affinity_model.py",
}
PRIVATE_PARTS = {
    ".git", ".terraform", ".venv", ".venv2", ".venv-local", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".ruff_cache", "gcloud-config", "steam_training",
}


def audit_paths(values) -> dict:
    paths, rejected = set(), []
    for raw in values:
        value = raw.strip().replace("\\", "/")
        if not value:
            continue
        while value.startswith("./"):
            value = value[2:]
        path = PurePosixPath(value)
        name = path.name.lower()
        private = (
            path.is_absolute() or ":" in value or ".." in path.parts
            or bool(set(path.parts) & PRIVATE_PARTS)
            or value.startswith(("backend/data/generated/", "backend/tests/", "frontend/tests/", "deploy/backups/", "deploy/google-cloud/.tools/"))
            or name == ".env" or (name.startswith(".env.") and name != ".env.example")
            or name in {"application_default_credentials.json", "observations.jsonl", "observations.jsonl.tmp"}
            or name.endswith((".key", ".pem", ".p12", ".pfx", ".jsonl", ".tfstate", ".tfplan", ".tfvars", ".tfvars.json", ".db", ".sqlite", ".sqlite3", ".pkl", ".joblib"))
            or ".tfstate." in name or ".db-" in name or ".sqlite3-" in name or ".sqlite-" in name
            or (name.endswith(".npz") and value != "backend/models/review_model.npz")
            or name in {"local_model.json", "local_model.npz"}
            or (value.startswith("backend/models/") and value not in ALLOWED_MODELS)
        )
        if private:
            rejected.append(value)
        paths.add(value)
    if rejected:
        raise ValueError("Fuentes privadas o artefactos no aprobados incluidos: " + ", ".join(sorted(rejected)))
    missing = REQUIRED - paths
    if missing:
        raise ValueError("Faltan verificadores o modelos aprobados: " + ", ".join(sorted(missing)))
    return {"status": "verified", "files": len(paths), "models": sorted(ALLOWED_MODELS), "private_paths": 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files-list", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = audit_paths(args.files_list.read_text(encoding="utf-8-sig").splitlines())
    except (OSError, ValueError) as exc:
        parser.exit(1, f"No se pueden subir estas fuentes: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
