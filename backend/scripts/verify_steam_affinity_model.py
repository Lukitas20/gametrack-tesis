"""Verifica el modelo de afinidad real aprobado antes de construir la imagen.

No entrena, no abre una base ni consulta Steam. Sólo permite pesos numéricos y
metadatos auditados; las observaciones y las claves privadas quedan fuera.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


# Se completan después de auditar el entrenamiento real, nunca desde el build.
EXPECTED_SHA256 = "9d11c8ac94af5822ab2bbbea49f3822ac71db710753efea88fef6d335d014365"
EXPECTED_DATASET = "877e4f82549e6ad8cbfbb8b0d3c338616069609725cf14f6f31f06eea4efb6cc"
EXPECTED_CATALOG = "23ca31e38aa85bdfb0c5f68bc74203606dc1060b5cbe134265024c8bac878dfe"
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "models" / "steam_affinity.json"
FEATURE_SCHEMA = [
    "positive_mean_cosine", "positive_max_cosine", "negative_mean_cosine", "negative_max_cosine",
    "engagement_mean_cosine", "engagement_max_cosine", "signed_cosine", "positive_fraction",
    "history_support", "positive_available", "negative_available", "engagement_available",
    "positive_genre_cosine", "negative_genre_cosine", "positive_tag_cosine", "negative_tag_cosine",
    "positive_max_squared", "negative_max_squared",
    "positive_genre_tag_agreement", "negative_genre_tag_agreement",
    "signed_by_positive_fraction", "signed_by_history_support",
    "positive_peak_above_mean", "negative_peak_above_mean",
]
HEADS = {"explicit", "hours"}
WEIGHT_KEYS = {"coefficients", "intercept", "feature_mean", "feature_scale"}


def verify_artifact(path: Path = DEFAULT_PATH) -> dict:
    path = Path(path)
    if not EXPECTED_SHA256 or not EXPECTED_DATASET or not EXPECTED_CATALOG:
        raise ValueError("Todavía no se auditó un artefacto real para publicación")
    if hashlib.sha256(path.read_bytes()).hexdigest() != EXPECTED_SHA256:
        raise ValueError(f"Artefacto sin auditar o modificado: {path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"metadata", "heads", "term_idf"}:
        raise ValueError("El artefacto contiene campos inesperados")
    metadata = payload["metadata"]
    if (metadata.get("version") != 1
            or metadata.get("algorithm") != "history_content_logistic_regression_cpu"
            or metadata.get("source") != "steam_public_review_api"
            or metadata.get("data_policy") != "observed-only-v1"
            or metadata.get("data_label") != "observed"
            or metadata.get("dataset_sha256") != EXPECTED_DATASET
            or metadata.get("catalog_sha256") != EXPECTED_CATALOG
            or metadata.get("feature_schema") != FEATURE_SCHEMA):
        raise ValueError("Procedencia o esquema no aprobado")
    if (set(payload["heads"]) != HEADS or set(metadata.get("heads", {})) != HEADS
            or not any(metadata["heads"][name].get("automatic_eligible") is True for name in HEADS)):
        raise ValueError("No hay un canal evaluado y aprobado para publicar")
    dimension = len(FEATURE_SCHEMA)
    eligible = []
    for name in sorted(HEADS):
        head = payload["heads"][name]
        if not isinstance(head, dict) or set(head) != WEIGHT_KEYS:
            raise ValueError(f"Campos inesperados en canal {name}")
        for field in ("coefficients", "feature_mean", "feature_scale"):
            values = np.asarray(head[field])
            if values.shape != (dimension,) or values.dtype.kind not in "fi" or not np.isfinite(values).all():
                raise ValueError(f"Pesos incompatibles: {name}/{field}")
        if (type(head["intercept"]) not in (int, float) or not np.isfinite(head["intercept"])
                or np.any(np.asarray(head["feature_scale"]) <= 0)):
            raise ValueError(f"Escala o intercepto incompatible: {name}")
        approved = metadata["heads"][name].get("automatic_eligible")
        if type(approved) is not bool:
            raise ValueError(f"Elegibilidad sin evaluar: {name}")
        if approved:
            if not np.any(np.abs(head["coefficients"]) > 1e-12):
                raise ValueError(f"Canal aprobado sin pesos aprendidos: {name}")
            eligible.append(name)
    term_idf = payload["term_idf"]
    if (not isinstance(term_idf, dict) or not term_idf
            or not all(isinstance(term, str) and term.startswith(("genre:", "tag:"))
                       and 0 < len(term) <= 200 and type(value) in (int, float)
                       and np.isfinite(value) and value > 0 for term, value in term_idf.items())):
        raise ValueError("Vocabulario de atributos incompatible")
    return {
        "status": "verified", "source": metadata["source"],
        "data_policy": metadata["data_policy"], "algorithm": metadata["algorithm"],
        "eligible_heads": eligible, "features": dimension,
        "dataset_sha256": EXPECTED_DATASET, "catalog_sha256": EXPECTED_CATALOG,
        "sha256": EXPECTED_SHA256,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args(argv)
    try:
        result = verify_artifact(args.model)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"No se puede publicar el modelo de afinidad: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
