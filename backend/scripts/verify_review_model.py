"""Verifica el par de artefactos reales aprobado antes de construir la imagen.

No entrena, no abre bases, no usa la red ni permite cargar objetos pickle.
Un nuevo entrenamiento necesita una auditoría y actualizar estos hashes antes
de reemplazar la versión publicada; cambiar sólo una etiqueta no lo habilita.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


EXPECTED_SHA256 = {
    "review_model.npz": "8af574d2fa306364f1689bafe30c257c53dfe8d6d35ad3b7143a851b991d9789",
    "review_model.json": "be03c3c241a8d214829578d4e43b4174371e5ae5e9fe259420c2f204b55276bd",
}
EXPECTED_DATASET = "f8a1ee239c552cc78b93e9359244546702aa5bc0dfc0b737cbd1f1130e91246e"
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "models" / "review_model.npz"


def verify_artifact(path: Path = DEFAULT_PATH) -> dict:
    path = Path(path)
    for artifact, expected in ((path, EXPECTED_SHA256["review_model.npz"]),
                               (path.with_suffix(".json"), EXPECTED_SHA256["review_model.json"])):
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Artefacto sin auditar o modificado: {artifact.name}")
    metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    with np.load(path, allow_pickle=False) as data:
        if set(data.files) != {"vocabulary", "idf", "coefficients", "intercepts", "metadata"}:
            raise ValueError("Contenido inesperado en el artefacto")
        if json.loads(str(data["metadata"])) != metadata:
            raise ValueError("Metadatos externos e internos diferentes")
        vocabulary = json.loads(str(data["vocabulary"]))
        if (len(vocabulary) != 18000 or sorted(vocabulary.values()) != list(range(18000))
                or data["idf"].shape != (18000,)
                or data["coefficients"].shape != (7, 18000)
                or data["intercepts"].shape != (7,)
                or not all(np.isfinite(data[key]).all() for key in ("idf", "coefficients", "intercepts"))
                or np.any(data["idf"] <= 0)):
            raise ValueError("Pesos o vocabulario incompatibles")
    if (metadata.get("source") != "real_steam_reviews"
            or metadata.get("dataset_fingerprint") != EXPECTED_DATASET
            or metadata.get("examples") != 24000 or metadata.get("games") != 2687
            or metadata.get("reception", {}).get("supervision") != "real_steam_recommendation_flag"
            or metadata.get("reception", {}).get("target") != "steam_author_recommendation_not_personal_taste_or_text_sentiment"):
        raise ValueError("Procedencia no aprobada")
    return {
        "status": "verified",
        "source": metadata["source"],
        "examples": metadata["examples"],
        "games": metadata["games"],
        "dataset_fingerprint": metadata["dataset_fingerprint"],
        "sha256": EXPECTED_SHA256,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args(argv)
    try:
        result = verify_artifact(args.model)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"No se puede publicar el modelo de reseñas: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
