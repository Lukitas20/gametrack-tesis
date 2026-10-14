"""Adapta el modelo de Steam a señales propias sin escribir ni entrenar."""
from functools import lru_cache
from pathlib import Path
import math

import numpy as np

from app.core.config import settings
from app.ml.steam_affinity_model import SteamAffinityModel


@lru_cache(maxsize=2)
def _load(path, modified, size):
    return SteamAffinityModel.load(Path(path))


def load_affinity_model():
    path = Path(settings.STEAM_AFFINITY_MODEL_PATH)
    if not path.is_file():
        return None, dict(status='missing', source='steam_public_review_api',
                          training_observations=0, message='El modelo de afinidad todavía no está disponible.')
    try:
        stat = path.stat()
        if stat.st_size > 2_000_000:
            raise ValueError('Artefacto demasiado grande')
        model = _load(str(path), stat.st_mtime_ns, stat.st_size)
        metadata = model.metadata
        heads = {name: bool(value.get('automatic_eligible')) for name, value in metadata.get('heads', {}).items()}
        status = dict(status='ready' if any(heads.values()) else 'not_validated',
                      source=metadata['source'], model_version=str(metadata.get('model_version', metadata['version'])),
                      trained_at=metadata.get('trained_at'), training_observations=metadata.get('training_observations', 0),
                      training_examples=metadata.get('training_examples', 0), heads=heads,
                      training_examples_by_head={name: value.get('training_examples', 0)
                                                 for name, value in metadata.get('heads', {}).items()},
                      dataset_fingerprint=metadata.get('dataset_sha256'),
                      message='Modelo propio entrenado con recomendaciones reales de Steam.')
        return model, status
    except (ValueError, OSError, KeyError, TypeError, AttributeError, OverflowError):
        return None, dict(status='invalid', source='steam_public_review_api', training_observations=0,
                          message='No se pudo verificar el modelo; se conserva el motor de historial.')


def affinity_scores(engine, signals):
    """Una sola inferencia por perfil, alineada con los IDs del catálogo actual."""
    model, status = load_affinity_model()
    history = []
    features = getattr(engine, 'affinity_features', {})
    # Priorizar opiniones expresas y conservar también el interés por horas.
    ordered = sorted(signals.items(), key=lambda item: (
        item[1].get('source') == 'steam_playtime', -abs(item[1].get('weight', 0)), item[0]))
    for gid, signal in ordered[:100]:
        weight = signal.get('weight')
        if gid not in features or type(weight) not in (int, float) or not math.isfinite(weight) or not weight:
            continue
        explicit = signal.get('source') != 'steam_playtime'
        row = dict(features[gid], strength=float(weight),
                   recommended=(weight > 0) if explicit else None)
        minutes = signal.get('minutes')
        if type(minutes) is int and minutes >= 0:
            row['minutes'] = minutes
        history.append(row)
    status = dict(status, history_examples=len(history), used=False)
    n = len(engine.game_ids)
    empty = (np.zeros(n), np.zeros(n), {}, status)
    if model is None or status['status'] != 'ready' or not history:
        return empty
    try:
        candidates = [features.get(gid, {}) for gid in engine.game_ids]
        predictions = []
        # Memoria acotada aunque crezca el catálogo: nunca matriz juegos².
        for start in range(0, len(candidates), 2048):
            predictions.extend(model.predict_many(candidates[start:start + 2048], history))
        if len(predictions) != n:
            raise ValueError('Desalineación del catálogo')
        values, support, details = np.zeros(n), np.zeros(n), {}
        for index, (gid, item) in enumerate(zip(engine.game_ids, predictions)):
            value, confidence = item.get('relative_affinity'), item.get('confidence')
            if (not item.get('available') or type(value) not in (int, float) or not math.isfinite(value)
                    or type(confidence) not in (int, float) or not math.isfinite(confidence)
                    or item.get('feature_coverage', 0) < .5 or item.get('maximum_overlap', 0) < .15):
                continue
            values[index] = np.clip(value, 0, 1)
            support[index] = np.clip(confidence, 0, 1)
            details[gid] = {key: item[key] for key in ('head', 'history_games', 'maximum_overlap', 'feature_coverage')}
        return values, support, details, status
    except (ValueError, TypeError, KeyError, ArithmeticError):
        status.update(status='invalid', message='El modelo no pudo calcular este perfil; se conserva el motor de historial.')
        return np.zeros(n), np.zeros(n), {}, status


def learning_status(context, game_id, *, own=False, weight=0.):
    status = context.get('affinity_status', {})
    selected_head = context.get('affinity_details', {}).get(game_id, {}).get('head')
    result = dict(status=status.get('status', 'missing'), used=False,
                  model_version=status.get('model_version'), source='steam_public_review_api',
                  training_observations=status.get('training_observations', 0),
                  training_examples=status.get('training_examples_by_head', {}).get(selected_head,
                                                                                  status.get('training_examples', 0)),
                  history_examples=status.get('history_examples', 0), weight=0.,
                  message=status.get('message', 'El cálculo conserva el motor de gustos e historial.'))
    if own:
        result.update(status='own_experience', message='Se conserva tu experiencia en este juego; el modelo no la sustituye.')
    elif weight > 0:
        result.update(used=True, weight=weight,
                      message='El modelo compara los rasgos del juego con tu historial. Su aporte excluye la tendencia positiva general de Steam.')
    elif status.get('status') == 'ready':
        result.update(status='insufficient_evidence',
                      message='El modelo está disponible, pero este perfil o juego no tiene respaldo suficiente para que aporte al índice.')
    return result
