"""Portable personal-affinity model trained only on observed Steam votes.

No author IDs, game IDs, texts or historical observations are serialized. The
learned coefficients relate a candidate's real genres/community tags to the
person's earlier history. A target's own hours are never an input.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.sparse import csr_matrix
from scipy.special import expit
from sklearn.preprocessing import normalize

from app.db.real_data import DATA_POLICY
from app.ml.steam_training_data import SOURCE

VERSION = 1
FEATURE_SCHEMA = [
    "positive_mean_cosine", "positive_max_cosine",
    "negative_mean_cosine", "negative_max_cosine",
    "engagement_mean_cosine", "engagement_max_cosine",
    "signed_cosine", "positive_fraction", "history_support",
    "positive_available", "negative_available", "engagement_available",
    "positive_genre_cosine", "negative_genre_cosine",
    "positive_tag_cosine", "negative_tag_cosine",
]
HEADS = ("explicit", "hours")


def _slugs(values: Any, *, community_only: bool = False) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    result = []
    for value in values:
        if isinstance(value, Mapping):
            if community_only and value.get("kind", "community") != "community":
                continue
            value = value.get("slug")
        if isinstance(value, str) and value and len(value) <= 100:
            result.append(value)
    return sorted(set(result))


def game_features(game: Mapping[str, Any]) -> dict[str, float]:
    """Identical feature extraction for training/runtime; platform tags excluded.

    `community_tags` is the preferred explicit whitelist. If only `tags` is
    supplied, tag dictionaries must declare kind=community (plain strings are
    accepted only as the caller's already filtered community tags).
    """
    genres = _slugs(game.get("genres", []))
    tags = _slugs(game.get("community_tags", game.get("tags", [])), community_only=True)
    # Binary memberships avoid pretending that the exported catalog contains
    # vote counts. Normalization makes different tag counts comparable.
    result = {"genre:" + slug: .4 for slug in genres}
    tag_weight = 1 / math.sqrt(len(tags)) if tags else 0.
    result.update({"tag:" + slug: tag_weight for slug in tags})
    return result


def catalog_idf(games: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    counts = Counter(term for game in games for term in game_features(game))
    return {term: float(math.log((1 + len(games)) / (1 + count)) + 1)
            for term, count in sorted(counts.items())}


def _matrix(games: Sequence[Mapping[str, Any]], terms: Mapping[str, int], idf: Mapping[str, float]) -> csr_matrix:
    rows, columns, data = [], [], []
    for row, game in enumerate(games):
        for term, value in game_features(game).items():
            rows.append(row); columns.append(terms[term]); data.append(value * idf.get(term, 1.))
    matrix = csr_matrix((data, (rows, columns)), shape=(len(games), len(terms)), dtype=float)
    return normalize(matrix) if terms else matrix


def _strength(row: Mapping[str, Any]) -> float:
    value = row.get("strength")
    if type(value) in (int, float) and math.isfinite(value):
        return float(np.clip(value, -1, 1))
    recommendation = row.get("recommended")
    if type(recommendation) is bool:
        return 1. if recommendation else -1.
    return 0.


def feature_matrix(
    candidates: Sequence[Mapping[str, Any]], history: Sequence[Mapping[str, Any]],
    term_idf: Mapping[str, float],
) -> tuple[np.ndarray, dict[str, Any]]:
    """Batch O(candidate x history), sparse comparisons; no catalog-square array.

    History `minutes` is the caller's observation *of the history game*. During
    training it is strictly minutes_at_review, never current lifetime/recent.
    Hours weigh interest continuously; they never create a positive/negative
    training label. Runtime may use real hours with an explicit `strength`.
    """
    # Keep only interpretable signals with actual descriptive characteristics.
    useful = [row for row in history if game_features(row) and _strength(row) != 0]
    all_games = [*candidates, *useful]
    terms = {term: index for index, term in enumerate(sorted({
        term for game in all_games for term in game_features(game)
    }))}
    x = np.zeros((len(candidates), len(FEATURE_SCHEMA)))
    if not useful or not candidates or not terms:
        return x, {"history_games": 0, "maximum_overlap": np.zeros(len(candidates)),
                   "feature_coverage": np.zeros(len(candidates)), "explicit_history_games": 0}
    matrix = _matrix(all_games, terms, term_idf)
    similarity = (matrix[:len(candidates)] @ matrix[len(candidates):].T).toarray()
    strengths = np.array([_strength(row) for row in useful])
    minutes = np.array([float(row.get("minutes")) if type(row.get("minutes")) in (int, float)
                        and math.isfinite(row["minutes"]) and row["minutes"] >= 0 else 0.
                        for row in useful])
    interest = 1 + np.clip(np.log1p(minutes / 60) / math.log1p(100), 0, 1)
    positive, negative = strengths > 0, strengths < 0
    engagement = positive & (minutes > 0)
    def weighted_mean(mask, weights):
        return similarity[:, mask] @ weights[mask] / weights[mask].sum() if mask.any() else np.zeros(len(candidates))
    def maximum(mask):
        return similarity[:, mask].max(axis=1) if mask.any() else np.zeros(len(candidates))
    positive_weights = np.maximum(strengths, 0) * interest
    negative_weights = np.maximum(-strengths, 0)
    x[:, 0] = weighted_mean(positive, positive_weights)
    x[:, 1] = maximum(positive)
    x[:, 2] = weighted_mean(negative, negative_weights)
    x[:, 3] = maximum(negative)
    x[:, 4] = weighted_mean(engagement, positive_weights)
    x[:, 5] = maximum(engagement)
    x[:, 6] = x[:, 0] - x[:, 2]
    x[:, 7] = positive.sum() / len(useful)
    x[:, 8] = min(1., math.log1p(len(useful)) / math.log1p(20))
    x[:, 9] = positive.any(); x[:, 10] = negative.any(); x[:, 11] = engagement.any()
    # Learning separate genre/tag effects avoids imposing a single manually
    # chosen similarity weight. No target ID or population popularity enters.
    for prefix, offset in (("genre:", 12), ("tag:", 14)):
        selected_columns = [column for term, column in terms.items() if term.startswith(prefix)]
        if selected_columns:
            separate = normalize(matrix[:, selected_columns])
            similarities = (separate[:len(candidates)] @ separate[len(candidates):].T).toarray()
            for index, mask, weights in ((offset, positive, positive_weights), (offset + 1, negative, negative_weights)):
                if mask.any():
                    x[:, index] = similarities[:, mask] @ weights[mask] / weights[mask].sum()
    coverage = np.array([sum(term in term_idf for term in game_features(game)) / max(1, len(game_features(game)))
                         for game in candidates])
    return x, {"history_games": len(useful), "maximum_overlap": similarity.max(axis=1),
               "feature_coverage": coverage,
               "explicit_history_games": sum(type(row.get("recommended")) is bool for row in useful)}


@dataclass
class AffinityHead:
    coefficients: np.ndarray
    intercept: float
    feature_mean: np.ndarray
    feature_scale: np.ndarray

    def predict_features(self, matrix: np.ndarray) -> np.ndarray:
        return expit((matrix - self.feature_mean) / self.feature_scale @ self.coefficients + self.intercept)


def history_for_head(history: Sequence[Mapping[str, Any]], head: str) -> list[dict[str, Any]]:
    if head == "explicit":
        return [dict(row) for row in history if type(row.get("recommended")) is bool]
    if head != "hours":
        raise ValueError("Canal de afinidad desconocido")
    # An interest profile, never a constructed label: all targets remain
    # actual Steam votes, including negative votes after many played hours.
    return [{**row, "recommended": None, "strength": 1.}
            for row in history if type(row.get("minutes")) in (int, float)
            and math.isfinite(row["minutes"]) and row["minutes"] >= 120]


@dataclass
class SteamAffinityModel:
    heads: dict[str, AffinityHead]
    term_idf: dict[str, float]
    metadata: dict[str, Any]

    def predict_many(self, candidates: Sequence[Mapping[str, Any]], history: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        explicit = history_for_head(history, "explicit")
        eligible = self.metadata.get("heads", {})
        head = "explicit" if explicit and eligible.get("explicit", {}).get("automatic_eligible") else "hours"
        head_history = history_for_head(history, head)
        matrix, support = feature_matrix(candidates, head_history, self.term_idf)
        probabilities = self.heads[head].predict_features(matrix)
        prior = float(self.metadata.get("heads", {}).get(head, {}).get("training_positive_rate", .5))
        prior_logit = math.log(max(1e-9, prior) / max(1e-9, 1 - prior))
        results = []
        for index, probability in enumerate(probabilities):
            overlap = float(support["maximum_overlap"][index])
            available = (support["history_games"] > 0 and overlap > 0 and bool(game_features(candidates[index]))
                         and bool(eligible.get(head, {}).get("automatic_eligible")))
            # Evidence support, not a second probability or measured accuracy.
            confidence = min(1., math.log1p(support["history_games"]) / math.log1p(10)) * overlap
            logit = math.log(max(1e-9, probability) / max(1e-9, 1 - probability))
            results.append({"probability": float(probability) if available else None,
                            "relative_affinity": float(expit(logit - prior_logit)) if available else None,
                            "personal_lift": float(probability - prior) if available else None,
                            "head": head,
                            "available": available, "confidence": float(confidence),
                            "history_games": support["history_games"],
                            "explicit_history_games": support["explicit_history_games"],
                            "maximum_overlap": overlap,
                            "feature_coverage": float(support["feature_coverage"][index])})
        return results

    def predict(self, candidate: Mapping[str, Any], history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return self.predict_many([candidate], history)[0]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"metadata": self.metadata, "term_idf": self.term_idf,
                   "heads": {name: {"coefficients": head.coefficients.tolist(), "intercept": head.intercept,
                                     "feature_mean": head.feature_mean.tolist(), "feature_scale": head.feature_scale.tolist()}
                             for name, head in self.heads.items()}}
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path) -> SteamAffinityModel:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if set(payload) != {"metadata", "term_idf", "heads"} or set(payload["heads"]) != set(HEADS):
            raise ValueError("Esquema del artefacto de afinidad incompatible")
        model = cls({name: AffinityHead(np.asarray(row["coefficients"], dtype=float), float(row["intercept"]),
                                      np.asarray(row["feature_mean"], dtype=float), np.asarray(row["feature_scale"], dtype=float))
                     for name, row in payload["heads"].items()}, payload["term_idf"], payload["metadata"])
        n = len(FEATURE_SCHEMA)
        if (model.metadata.get("version") != VERSION or model.metadata.get("feature_schema") != FEATURE_SCHEMA
                or model.metadata.get("source") != SOURCE or model.metadata.get("data_policy") != DATA_POLICY
                or model.metadata.get("data_label") != "observed"
                or set(model.metadata.get("heads", {})) != set(HEADS)
                or any(type(model.metadata["heads"][name].get("automatic_eligible")) is not bool
                       or type(model.metadata["heads"][name].get("training_positive_rate")) not in (int, float)
                       or not 0 < model.metadata["heads"][name]["training_positive_rate"] < 1 for name in HEADS)
                or any(head.coefficients.shape != (n,) or head.feature_mean.shape != (n,) or head.feature_scale.shape != (n,)
                       or not np.isfinite(head.intercept) or np.any(head.feature_scale <= 0)
                       or not all(np.isfinite(value).all() for value in (head.coefficients, head.feature_mean, head.feature_scale))
                       for head in model.heads.values()) or not isinstance(model.term_idf, dict)
                or not model.term_idf or any(not isinstance(key, str) or not key.startswith(("genre:", "tag:"))
                                           or type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                                           for key, value in model.term_idf.items())):
            raise ValueError("Procedencia o pesos inválidos en el modelo de afinidad")
        return model


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
