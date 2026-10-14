"""Train personal affinity on real Steam observations, without touching the DB.

python scripts/train_steam_affinity.py --observations /private/observations.jsonl \
  --manifest /private/manifest.json --catalog /private/catalog.json \
  --output data/generated/steam_affinity.json

The corpus and private HMAC key must never be committed or packaged. Only
aggregate fitted parameters and provenance/held-out metrics leave this process.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from app.db.real_data import DATA_POLICY
from app.ml.steam_training_data import SOURCE, observed_epoch, positive_appid
from app.ml.steam_affinity_model import (
    AffinityHead, FEATURE_SCHEMA, HEADS, VERSION, SteamAffinityModel,
    catalog_idf, feature_matrix, file_sha256, game_features, history_for_head,
)

USER_KEY = re.compile(r"^[0-9a-f]{64}$")
MIN_VALIDATION_EXAMPLES = 100
MIN_VALIDATION_CLASS = 20


def split_user(user: str) -> str:
    # Fixed before training: an author's all targets stay in one partition.
    bucket = int(hashlib.sha256(("steam-affinity-v1:" + user).encode()).hexdigest()[:8], 16) % 10
    return "validation" if bucket == 0 else "test" if bucket == 1 else "train"


def load_observations(path: Path, manifest_path: Path) -> tuple[list[dict], dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if (manifest.get("source") != SOURCE or manifest.get("data_policy") != DATA_POLICY
            or manifest.get("schema_version") != 1 or manifest.get("observations_sha256") != file_sha256(path)
            or not manifest.get("started_at") or not manifest.get("finished_at")
            or not manifest.get("endpoint", "").startswith("https://api.steampowered.com/IUserReviewsService/")):
        raise ValueError("Falta procedencia real verificable o el hash del corpus no coincide")
    rows, pairs, review_ids = [], set(), set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            user, appid = row.get("user_key"), positive_appid(row.get("steam_appid"))
            rid = row.get("review_id")
            if (row.get("source") != SOURCE or type(row.get("voted_up")) is not bool
                    or not isinstance(user, str) or not USER_KEY.fullmatch(user)
                    or appid is None or not isinstance(rid, str) or not rid.isdecimal()):
                raise ValueError("Una observación carece de autor seudónimo, voto o procedencia Steam real")
            pair = (user, appid)
            if pair in pairs or rid in review_ids:
                raise ValueError("El corpus contiene autores/juegos o reseñas duplicadas")
            for key in ("created_at", "updated_at"):
                if row.get(key) is not None and observed_epoch(row[key]) is None:
                    raise ValueError("Fecha de reseña inválida; no se reemplaza por la fecha de recolección")
            pairs.add(pair); review_ids.add(rid); rows.append(row)
    if manifest.get("summary", {}).get("observations") != len(rows):
        raise ValueError("El conteo del manifiesto no coincide con el corpus")
    return rows, manifest


def load_catalog(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    games = payload.get("games", []) if isinstance(payload, dict) else payload
    if not isinstance(games, list):
        raise ValueError("El catálogo debe contener una lista de juegos reales")
    result, seen = [], set()
    for game in games:
        if not isinstance(game, dict):
            raise ValueError("Ficha de catálogo inválida")
        appid = positive_appid(game.get("steam_appid", game.get("steam_app_id", game.get("appid"))))
        if appid is not None and appid not in seen and game_features(game):
            result.append({**game, "steam_appid": appid}); seen.add(appid)
    if len(result) < 3:
        raise ValueError("Se necesitan al menos tres juegos con características reales")
    return result


def chronological_examples(rows: list[dict], catalog: list[dict], idf: dict) -> tuple[dict, dict]:
    games = {game["steam_appid"]: game for game in catalog}
    authors = defaultdict(list)
    for row in rows:
        authors[row["user_key"]].append(row)
    samples = {head: {part: [] for part in ("train", "validation", "test")} for head in HEADS}
    skipped = Counter()
    for user, records in authors.items():
        partition = split_user(user)
        for target in sorted(records, key=lambda row: row.get("created_at") or 0):
            target_time = target.get("created_at")
            if target_time is None:
                skipped["unknown_target_date"] += 1; continue
            if target["steam_appid"] not in games:
                skipped["target_without_catalog_features"] += 1; continue
            history = []
            for previous in records:
                if previous["steam_appid"] == target["steam_appid"] or previous["steam_appid"] not in games:
                    continue
                timestamps = [previous.get(key) for key in ("created_at", "updated_at")]
                # Missing creation cannot establish temporal precedence. A
                # later revision excludes the row even if it was created early.
                if timestamps[0] is None or max(time for time in timestamps if time is not None) >= target_time:
                    continue
                history.append({**games[previous["steam_appid"]], "recommended": previous["voted_up"],
                                "minutes": previous.get("minutes_at_review")})
            if not history:
                skipped["without_strictly_earlier_history"] += 1; continue
            for head in HEADS:
                selected = history_for_head(history, head)
                if not selected:
                    skipped[head + "_without_history"] += 1; continue
                x, support = feature_matrix([games[target["steam_appid"]]], selected, idf)
                samples[head][partition].append({"x": x[0], "y": int(target["voted_up"]), "user": user,
                                                "appid": target["steam_appid"],
                                                "history_games": support["history_games"]})
    return samples, dict(skipped)


def metrics(labels, probabilities) -> dict[str, Any]:
    labels = np.asarray(labels, dtype=int); probabilities = np.asarray(probabilities, dtype=float)
    if not len(labels):
        return {"examples": 0, "positive": 0, "negative": 0, "log_loss": None, "brier": None, "roc_auc": None}
    return {"examples": len(labels), "positive": int(labels.sum()), "negative": int((1 - labels).sum()),
            "log_loss": round(float(log_loss(labels, probabilities, labels=[0, 1])), 6),
            "brier": round(float(brier_score_loss(labels, probabilities)), 6),
            "roc_auc": round(float(roc_auc_score(labels, probabilities)), 6) if len(set(labels)) == 2 else None}


def _arrays(examples):
    return np.array([row["x"] for row in examples]).reshape(-1, len(FEATURE_SCHEMA)), np.array([row["y"] for row in examples])


def _fit(x, y, c, weights=None) -> AffinityHead:
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1.
    model = LogisticRegression(C=c, solver="lbfgs", max_iter=1000, random_state=42)
    model.fit((x - mean) / scale, y, sample_weight=weights)
    return AffinityHead(model.coef_[0], float(model.intercept_[0]), mean, scale)


def train_head(partitions: dict) -> tuple[AffinityHead, dict]:
    x, labels = _arrays(partitions["train"])
    if len(labels) < 30 or len(set(labels)) < 2:
        raise ValueError("Se necesitan al menos 30 objetivos reales de entrenamiento, con votos de ambas clases")
    user_counts = Counter(row["user"] for row in partitions["train"])
    weights = np.array([1 / user_counts[row["user"]] for row in partitions["train"]])
    weights *= len(weights) / weights.sum()
    prior = float(np.average(labels, weights=weights))
    arrays = {name: _arrays(rows) for name, rows in partitions.items()}
    # Baselines see exactly the same train rows, preprocessing, weighting and
    # true held-out votes. Fixed content uses positive-minus-negative cosine.
    prior_scores = {name: np.full(len(y), prior) for name, (_, y) in arrays.items()}
    content_head = _fit(x[:, [6]], labels, 1., weights)
    content_scores = {name: content_head.predict_features(values[:, [6]]) for name, (values, _) in arrays.items()}
    # A title's general reception must not masquerade as personal affinity.
    # This comparison uses ONLY train targets; IDs never enter fitted model
    # features or the packaged artifact. Unseen titles fall back to train prior.
    item_weight, item_positive = Counter(), Counter()
    for row, weight in zip(partitions["train"], weights):
        item_weight[row["appid"]] += float(weight)
        item_positive[row["appid"]] += float(weight * row["y"])
    item_scores = {name: np.array([(item_positive[row["appid"]] + 20 * prior) /
                                   (item_weight[row["appid"]] + 20) for row in rows])
                   for name, rows in partitions.items()}
    fitted = [_fit(x, labels, c, weights) for c in (.03, .1, .3, 1., 3.)]
    vx, vy = arrays["validation"]
    # No test labels participate in model choice, threshold or activation.
    chosen = min(range(len(fitted)), key=lambda index: log_loss(vy, fitted[index].predict_features(vx), labels=[0, 1])) if len(vy) else 0
    head = fitted[chosen]
    chosen_c = (.03, .1, .3, 1., 3.)[chosen]
    report = {}
    for name, (values, y) in arrays.items():
        report[name] = {"model": metrics(y, head.predict_features(values)),
                        "prior_baseline": metrics(y, prior_scores[name]),
                        "content_baseline": metrics(y, content_scores[name]),
                        "item_reception_baseline": metrics(y, item_scores[name]),
                        "users": len({row["user"] for row in partitions[name]}),
                        "games": len({row["appid"] for row in partitions[name]})}
    val = report["validation"]
    current = val["model"]
    reasons = []
    if current["examples"] < MIN_VALIDATION_EXAMPLES:
        reasons.append("validation_requires_100_observed_targets")
    if min(current["positive"], current["negative"]) < MIN_VALIDATION_CLASS:
        reasons.append("validation_requires_20_votes_of_each_class")
    if val["users"] < 30:
        reasons.append("validation_requires_30_independent_authors")
    if current["log_loss"] is None or any(current["log_loss"] > val[baseline]["log_loss"] - .005
                                            for baseline in ("prior_baseline", "content_baseline", "item_reception_baseline")):
        reasons.append("validation_log_loss_must_improve_all_baselines_by_0_005")
    if current["brier"] is None or any(current["brier"] > val[baseline]["brier"]
                                         for baseline in ("prior_baseline", "content_baseline", "item_reception_baseline")):
        reasons.append("validation_brier_must_not_worsen_any_baseline")
    if current["roc_auc"] is None or current["roc_auc"] < .55:
        reasons.append("validation_auc_requires_0_55")
    candidate_columns = [0, 1, 2, 3, 4, 5, 6, 12, 13, 14, 15]
    candidate_sensitivity = float(np.abs(head.coefficients[candidate_columns]).sum())
    if candidate_sensitivity < .1:
        reasons.append("learned_candidate_similarity_sensitivity_requires_0_1")
    return head, {"automatic_eligible": not reasons, "activation_reasons": reasons,
                  "training_positive_rate": prior, "regularization_c": chosen_c,
                  "training_examples": len(labels),
                  "eligible_targets_total": sum(len(rows) for rows in partitions.values()),
                  "item_reception_baseline_prior_strength": 20,
                  "candidate_similarity_coefficient_l1": candidate_sensitivity,
                  "loss_weighting": "equal_total_weight_per_training_author",
                  "evaluation": report,
                  "activation_uses": "validation_only_test_is_reported_once_and_never_used_for_selection"}


def train(observations: Path, manifest: Path, catalog: Path, output: Path) -> dict:
    rows, source_manifest = load_observations(observations, manifest)
    games = load_catalog(catalog); idf = catalog_idf(games)
    samples, skipped = chronological_examples(rows, games, idf)
    heads, reports = {}, {}
    for name in HEADS:
        heads[name], reports[name] = train_head(samples[name])
    metadata = {
        "version": VERSION, "algorithm": "history_content_logistic_regression_cpu",
        "feature_schema": FEATURE_SCHEMA, "source": SOURCE,
        "data_policy": DATA_POLICY, "data_label": "observed",
        "trained_at": datetime.now(timezone.utc).isoformat(), "seed": 42,
        "observations": len(rows), "users": len({row["user_key"] for row in rows}),
        "training_observations": len(rows), "collected_observations": len(rows),
        "training_examples": len(samples["explicit"]["train"]),
        "eligible_targets_total": sum(len(part) for part in samples["explicit"].values()),
        "games": len({row["steam_appid"] for row in rows}), "catalog_games": len(games),
        "positive_votes": sum(row["voted_up"] for row in rows), "negative_votes": sum(not row["voted_up"] for row in rows),
        "dataset_sha256": file_sha256(observations), "manifest_sha256": file_sha256(manifest),
        "catalog_sha256": file_sha256(catalog), "collection_finished_at": source_manifest["finished_at"],
        "split": "fixed_author_hash_80_train_10_validation_10_test_with_strict_prior_review_history",
        "chronology": "max(history_created_at,history_updated_at)<target_created_at;history_minutes_at_review_only",
        "training_target": "observed_final_steam_recommendation_vote_not_hours_or_unreviewed_items",
        "heads": reports, "automatic_eligible": any(report["automatic_eligible"] for report in reports.values()),
        "skipped": skipped,
        "limitations": [
            "Selected recent Steam reviewers are not a representative sample of all players or games.",
            "Final observed votes may have been revised; historical rows revised after the target creation are excluded.",
            "Static game features describe the collected catalog, not reconstructed historical store metadata.",
            "Only targets with an earlier real history train personal affinity; singleton reviews add no training pairs.",
            "Hours describe engagement, not enjoyment. The hours head is evaluated separately against real positive and negative votes.",
            "Probability is recommendation among this observed sample, not a universal probability of liking a game.",
            "relative_affinity is sigmoid(log_odds_prediction minus log_odds_training_prior), an index, not a calibrated probability.",
            "confidence is descriptive history/feature support, not measured model accuracy.",
            "No fabricated negatives, stars, profiles, reviews or outside AI services were used.",
        ],
    }
    SteamAffinityModel(heads, idf, metadata).save(output)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/generated/steam_affinity.json"))
    args = parser.parse_args()
    result = train(args.observations, args.manifest, args.catalog, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
