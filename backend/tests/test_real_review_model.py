"""Carga del artefacto real y contratos de inferencia; no entrena fixtures."""
import copy
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from app.ml import trained_reviews
from app.ml.trained_reviews import ReviewModel, TOPICS, load_model, validate_provenance


def test_bundled_real_model_is_ready_and_has_observed_supervision():
    model, status = load_model()
    assert model is not None and status['status'] == 'ready'
    assert status['source'] == 'real_steam_reviews'
    assert status['examples'] == 24000 and status['games'] == 2687
    assert status['reception']['supervision'] == 'real_steam_recommendation_flag'
    assert all(status['topics'][topic]['supervision'] == 'weak_curated_vocabulary' for topic in TOPICS)


def test_inference_abstains_for_empty_or_unknown_vocabulary():
    model, _ = load_model()
    assert model.predict([]) == []
    assert model.predict(['', 'zzzzzzzzzzzzzzzzzzzzzzz']) == [
        {'reception': None, 'topics': []}, {'reception': None, 'topics': []}]


def test_real_model_round_trip_preserves_predictions_without_pickle(tmp_path):
    model, _ = load_model()
    target = tmp_path/'reviews.npz'
    model.save(target)
    loaded = ReviewModel.load(target)
    text = 'El rendimiento tiene problemas y errores. La historia me resultó interesante.'
    assert loaded.predict([text]) == model.predict([text])
    prediction = loaded.predict([text])[0]
    assert 0 <= prediction['reception'] <= 1
    for topic in prediction['topics']:
        quote = loaded.evidence(text, topic['topic']).strip('…')
        assert quote in text


@pytest.mark.parametrize('field,value', [
    ('source', 'demo'), ('source', 'unknown'), ('dataset_fingerprint', ''),
    ('examples', 1), ('split', 'random_rows'), ('language', 'en'),
])
def test_unknown_or_demo_provenance_is_rejected(field, value):
    model, _ = load_model()
    metadata = copy.deepcopy(model.metadata)
    metadata[field] = value
    with pytest.raises(ValueError):
        validate_provenance(metadata)


def test_fabricated_reception_labels_cannot_be_loaded():
    model, _ = load_model()
    metadata = copy.deepcopy(model.metadata)
    metadata['reception']['supervision'] = 'generated'
    with pytest.raises(ValueError, match='Procedencia'):
        validate_provenance(metadata)


def test_invalid_artifact_falls_back_without_inference(tmp_path, monkeypatch):
    path = tmp_path/'invalid.npz'
    path.write_bytes(b'invalid')
    monkeypatch.setattr(trained_reviews, 'artifact_path', lambda: path)
    model, status = load_model()
    assert model is None and status['status'] == 'invalid'


def test_nonfinite_weights_are_rejected(tmp_path):
    model, _ = load_model()
    model = ReviewModel(model.vector, model.coefficients.copy(), model.intercepts.copy(), model.metadata)
    model.coefficients[0, 0] = np.nan
    path = tmp_path/'nonfinite.npz'
    model.save(path)
    with pytest.raises(ValueError, match='inválido'):
        ReviewModel.load(path)


def test_training_split_has_no_duplicate_text_or_game_leakage():
    script = Path(__file__).resolve().parents[1]/'scripts/train_review_model.py'
    spec = importlib.util.spec_from_file_location('review_training', script)
    training = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(training)
    # Entradas aisladas para probar la partición; no se ajusta ningún modelo.
    rows = [(i, i % 60, f'Texto de prueba de partición número {i}, sin entrenamiento operativo.', i % 2)
            for i in range(200)]
    rows += [(500+i, 100+i, '  '+rows[i][2].upper()+'  ', False) for i in range(20)]
    selected, partitions = training.prepare(rows, 300)
    assert len(selected) == 200
    groups = [{row[2] for row in part} for part in partitions.values()]
    hashes = [{row[0] for row in part} for part in partitions.values()]
    assert all(not groups[i] & groups[j] for i in range(3) for j in range(i))
    assert all(not hashes[i] & hashes[j] for i in range(3) for j in range(i))
