"""Factorización matricial entrenada desde cero en CPU, sin servicios externos.

Sólo optimiza valoraciones observadas. El artefacto guarda factores de juegos;
el perfil del usuario se resuelve con sus notas actuales (ridge/fold-in), también
para usuarios nuevos. No se guardan emails, contraseñas ni nombres de usuarios.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from zipfile import BadZipFile

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Game, Rating, User, UserRole

VERSION = 1


def database_key(db: Session) -> str:
    # Impide mezclar IDs de dos bases distintas. Nunca persiste la URL real.
    url = db.get_bind().url
    identity = str(url)
    if url.drivername.startswith("sqlite") and url.database not in (None, "", ":memory:"):
        identity = str(Path(url.database).resolve())
    return hashlib.sha256(identity.encode()).hexdigest()


def fingerprint(rows: list[tuple[int, int, float]]) -> str:
    serial = json.dumps(sorted((int(u), int(i), float(r)) for u, i, r in rows), separators=(",", ":"))
    return hashlib.sha256(serial.encode()).hexdigest()


def read_training_data(db: Session) -> tuple[list[tuple[int, int, float]], dict[int, str]]:
    rows = [(int(u), int(i), float(r)) for u, i, r in db.execute(
        select(Rating.user_id, Rating.game_id, Rating.score)
        .join(User, Rating.user_id == User.id)
        .where(User.is_active.is_(True), User.role == UserRole.PLAYER)
        .order_by(Rating.created_at, Rating.id)
    )]
    return rows, dict(db.execute(select(Game.id, Game.slug)).all())


@dataclass
class LocalModel:
    game_ids: np.ndarray
    factors: np.ndarray
    biases: np.ndarray
    counts: np.ndarray
    mean: float
    metadata: dict = field(default_factory=dict)

    def predict(self, history: list[tuple[int, float]]) -> np.ndarray | None:
        """Escala 1–5; sólo notas presentes, sin inventar negativos ausentes."""
        index = {int(gid): i for i, gid in enumerate(self.game_ids)}
        known = [(index[gid], score) for gid, score in history if gid in index]
        if len(known) < 3:
            return None
        columns = np.array([i for i, _ in known])
        values = np.array([score for _, score in known], dtype=float)
        design = np.column_stack((self.factors[columns], np.ones(len(columns))))
        penalty = np.eye(design.shape[1]) * 0.35
        penalty[-1, -1] = 1.0
        target = values - self.mean - self.biases[columns]
        profile = np.linalg.solve(design.T @ design + penalty, design.T @ target)
        return np.clip(self.mean + self.biases + self.factors @ profile[:-1] + profile[-1], 1, 5)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, game_ids=self.game_ids, factors=self.factors,
                                biases=self.biases, counts=self.counts, mean=self.mean,
                                metadata=json.dumps(self.metadata, ensure_ascii=False))
        os.replace(temporary, path)

    @classmethod
    def load(cls, path: Path) -> LocalModel:
        with np.load(path, allow_pickle=False) as data:
            model = cls(data["game_ids"], data["factors"], data["biases"], data["counts"],
                        float(data["mean"]), json.loads(str(data["metadata"])))
        # Los IDs se convierten a int al contrastar el catálogo. Rechazar antes
        # floats (incluidos NaN/inf), strings y matrices evita excepciones o
        # truncamientos silenciosos que podrían asociar factores a otro juego.
        if (model.game_ids.ndim != 1 or model.game_ids.dtype.kind not in "iu"
                or model.game_ids.size == 0 or np.any(model.game_ids <= 0)):
            raise ValueError("IDs de juegos inválidos en el artefacto local")
        n = len(model.game_ids)
        if (model.metadata.get("version") != VERSION or model.factors.ndim != 2
                or model.factors.shape[0] != n or not 1 <= model.factors.shape[1] <= 128
                or model.biases.shape != (n,) or model.counts.shape != (n,)
                or len(set(model.game_ids.tolist())) != n
                or not all(np.isfinite(a).all() for a in (model.factors, model.biases, model.counts))
                or not np.isfinite(model.mean)):
            raise ValueError("Artefacto local incompatible")
        return model


def fit(rows: list[tuple[int, int, float]], *, factors: int = 12,
        epochs: int = 100, seed: int = 42) -> LocalModel:
    """SGD con sesgos y regularización L2 sobre error cuadrático observado."""
    if not rows or not 1 <= factors <= 128 or not 1 <= epochs <= 1000:
        raise ValueError("Se requieren valoraciones y parámetros de entrenamiento válidos")
    if any(not 1 <= score <= 5 for _, _, score in rows):
        raise ValueError("Las valoraciones deben estar entre 1 y 5")
    users = sorted({u for u, _, _ in rows})
    games = sorted({g for _, g, _ in rows})
    ui, gi = {u: i for i, u in enumerate(users)}, {g: i for i, g in enumerate(games)}
    uidx = np.array([ui[u] for u, _, _ in rows])
    gidx = np.array([gi[g] for _, g, _ in rows])
    ratings = np.array([score for _, _, score in rows], dtype=float)
    mean = float(ratings.mean())
    rng = np.random.default_rng(seed)
    p = rng.normal(0, 0.15, (len(users), factors))
    q = rng.normal(0, 0.15, (len(games), factors))
    bu, bi = np.zeros(len(users)), np.zeros(len(games))
    for epoch in range(epochs):
        learning_rate = 0.025 / (1.0 + epoch / 80.0)
        for pos in rng.permutation(len(rows)):
            u, i = uidx[pos], gidx[pos]
            error = ratings[pos] - (mean + bu[u] + bi[i] + p[u] @ q[i])
            previous = p[u].copy()
            bu[u] += learning_rate * (error - 0.05 * bu[u])
            bi[i] += learning_rate * (error - 0.05 * bi[i])
            p[u] += learning_rate * (error * q[i] - 0.05 * p[u])
            q[i] += learning_rate * (error * previous - 0.05 * q[i])
    return LocalModel(np.array(games, dtype=np.int64), q, bi,
                      np.bincount(gidx, minlength=len(games)), mean,
                      {"version": VERSION, "algorithm": "biased_matrix_factorization",
                       "factors": factors, "epochs": epochs, "seed": seed,
                       "ratings": len(rows), "users": len(users), "games": len(games)})


def temporal_split(rows: list[tuple[int, int, float]]) -> tuple[list, list]:
    """Reserva cronológicamente la última nota de usuarios con >=5 notas.

    El orden de entrada es created_at/id. Cada usuario de prueba conserva al
    menos cuatro notas de entrenamiento. Se usa como validación de activación;
    no es una prueba final independiente ni un corte temporal global.
    """
    positions: dict[int, list[int]] = {}
    for pos, (uid, _, _) in enumerate(rows):
        positions.setdefault(uid, []).append(pos)
    held = {indices[-1] for indices in positions.values() if len(indices) >= 5}
    return ([r for pos, r in enumerate(rows) if pos not in held],
            [r for pos, r in enumerate(rows) if pos in held])


def evaluate(train: list, test: list, *, factors: int = 12, epochs: int = 100) -> dict:
    if not test:
        return {"status": "insufficient_data", "test_ratings": 0}
    model = fit(train, factors=factors, epochs=epochs)
    index = {int(gid): i for i, gid in enumerate(model.game_ids)}
    histories: dict[int, list] = {}
    for uid, gid, score in train:
        histories.setdefault(uid, []).append((gid, score))
    errors, baseline_errors = [], []
    covered = 0
    for uid, gid, actual in test:
        prediction = model.predict(histories.get(uid, []))
        baseline = model.mean + (model.biases[index[gid]] if gid in index else 0.0)
        # El baseline usa sólo entrenamiento. Los ítems desconocidos cuentan
        # en la evaluación y cobertura; no se eliminan los casos difíciles.
        value = float(prediction[index[gid]]) if prediction is not None and gid in index else baseline
        covered += int(prediction is not None and gid in index)
        errors.append(value - actual)
        baseline_errors.append(float(np.clip(baseline, 1, 5)) - actual)
    def metrics(values):
        a = np.array(values)
        return {"rmse": round(float(np.sqrt(np.mean(a * a))), 4),
                "mae": round(float(np.mean(np.abs(a))), 4)}
    return {"status": "evaluated", "split": "last_created_rating_per_user_min5",
            "test_ratings": len(test), "covered_ratings": covered,
            "model": metrics(errors), "item_bias_baseline": metrics(baseline_errors)}


def train_and_save(db: Session, path: Path, *, data_label: str,
                   factors: int = 12, epochs: int = 100) -> dict:
    if data_label not in {"demo", "observed"}:
        raise ValueError("Identificá los datos como demo u observed")
    rows, slugs = read_training_data(db)
    if len(rows) < 20 or len({u for u, _, _ in rows}) < 3 or len({g for _, g, _ in rows}) < 3:
        raise ValueError("Se necesitan al menos 20 notas, 3 jugadores y 3 juegos; no se fabrican valoraciones")
    train, test = temporal_split(rows)
    report = evaluate(train, test, factors=factors, epochs=epochs)
    # El holdout se usa para decidir activación, así que es validación, no
    # una prueba final independiente. Esa evaluación requiere nuevos datos.
    report["purpose"] = "validation_for_activation_not_final_test"
    automatic_eligible = (report.get("test_ratings", 0) >= 20
                          and report.get("covered_ratings", 0) == report.get("test_ratings")
                          and report["model"]["rmse"] < report["item_bias_baseline"]["rmse"]
                          and report["model"]["mae"] <= report["item_bias_baseline"]["mae"])
    # Tras medir en holdout, el artefacto servido se reentrena con todos los datos.
    model = fit(rows, factors=factors, epochs=epochs)
    model.metadata.update(database_key=database_key(db), fingerprint=fingerprint(rows),
                          slugs={str(int(gid)): slugs[int(gid)] for gid in model.game_ids},
                          data_label=data_label, trained_at=datetime.now(timezone.utc).isoformat(),
                          evaluation=report, automatic_eligible=automatic_eligible)
    model.save(path)
    summary = {k: v for k, v in model.metadata.items() if k not in {"slugs", "database_key", "fingerprint"}}
    path.with_suffix(".json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


@lru_cache(maxsize=2)
def _read_artifact(path: str, modified: int, size: int) -> LocalModel:
    return LocalModel.load(Path(path))


def load_for_database(db: Session) -> tuple[LocalModel | None, dict]:
    path = Path(settings.LOCAL_MODEL_PATH)
    status = {"status": "missing", "data_label": None}
    if not path.is_file():
        return None, status
    try:
        stat = path.stat()
        model = _read_artifact(str(path), stat.st_mtime_ns, stat.st_size)
        if model.metadata.get("database_key") != database_key(db):
            return None, {"status": "different_database", "data_label": None}
        rows, slugs = read_training_data(db)
        if any(slugs.get(int(gid)) != model.metadata.get("slugs", {}).get(str(int(gid))) for gid in model.game_ids):
            return None, {"status": "catalog_changed", "data_label": None}
        status = {key: model.metadata.get(key) for key in
                  ("data_label", "trained_at", "ratings", "users", "games", "algorithm", "automatic_eligible")}
        status["status"] = "ready" if fingerprint(rows) == model.metadata.get("fingerprint") else "stale"
        return model, status
    except (ValueError, KeyError, OSError, TypeError, EOFError, BadZipFile, AttributeError, IndexError):
        return None, {"status": "invalid", "data_label": None}
