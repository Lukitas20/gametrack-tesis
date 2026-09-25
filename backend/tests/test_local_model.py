"""Aprendizaje, evaluación sin fuga e integración del artefacto local."""
import numpy as np
import pytest

from app.core.config import settings
from app.ml.local_model import LocalModel, evaluate, fit, load_for_database, temporal_split, train_and_save
from app.ml.recommender import RecommenderEngine
from app.models import Rating, RecommendationSource
from tests.test_recommender import db, catalog, populated  # noqa: F401


def observations():
    return [(u, g, 5.0 if (u < 5) == (g < 4) else 1.0)
            for u in range(10) for g in range(1, 7)]


def test_aprende_gustos_opuestos_sin_modelo_preentrenado():
    model = fit(observations())
    rpg = model.predict([(1, 5), (4, 1), (5, 1)])
    strategy = model.predict([(1, 1), (4, 5), (5, 5)])
    assert rpg[1] > rpg[5] + 1.0
    assert strategy[5] > strategy[1] + 1.0
    assert np.all((rpg >= 1) & (rpg <= 5))


def test_el_entrenamiento_es_reproducible_y_no_imputa_ausentes():
    rows = [(u, g, s) for u, g, s in observations() if (u, g) != (0, 6)]
    first, second = fit(rows), fit(rows)
    assert first.metadata["ratings"] == 59
    assert first.counts[-1] == 9
    np.testing.assert_array_equal(first.factors, second.factors)
    assert first.predict([(1, 4), (2, 3)]) is None


def test_separacion_temporal_no_comparte_pares_usuario_juego():
    rows = observations()
    train, test = temporal_split(rows)
    assert len(train) == 50 and len(test) == 10
    assert not {(u, g) for u, g, _ in train} & {(u, g) for u, g, _ in test}
    assert all(g == 6 for _, g, _ in test)
    report = evaluate(train, test)
    assert report["test_ratings"] == 10
    # Todos los 6 quedaron en prueba: reporta falta de cobertura, no descarta casos.
    assert report["covered_ratings"] == 0
    assert report["model"]["rmse"] >= 0


def test_guardado_sin_pickle_y_validacion_de_version(tmp_path):
    model = fit(observations(), epochs=5)
    path = tmp_path / "model.npz"
    model.save(path)
    restored = LocalModel.load(path)
    np.testing.assert_array_equal(restored.factors, model.factors)
    model.metadata["version"] = 999
    model.save(path)
    with pytest.raises(ValueError):
        LocalModel.load(path)


def test_datos_insuficientes_no_inventan_entrenamiento(db, catalog, tmp_path):
    with pytest.raises(ValueError, match="20 notas"):
        train_and_save(db, tmp_path / "empty.npz", data_label="observed")
    assert not (tmp_path / "empty.npz").exists()


def test_artefacto_integrado_aportes_y_notas_actuales(db, populated, tmp_path, monkeypatch):
    path = tmp_path / "local.npz"
    monkeypatch.setattr(settings, "LOCAL_MODEL_PATH", str(path))
    report = train_and_save(db, path, data_label="demo", epochs=30)
    assert report["data_label"] == "demo" and report["ratings"] == 60
    model, state = load_for_database(db)
    assert model is not None and state["status"] == "ready"
    user = populated["users"]["rpg0"]
    game = populated["games"]["rpg-uno"]
    engine = RecommenderEngine(db)
    before, basis = engine.personal_scores(user.id, [])
    assert basis == "contenido"  # diez notas de validación: no habilitar automático
    assert engine._local_scores(user.id) is not None
    assert not engine.local_model_status["automatic_eligible"]
    assert before[engine._game_index[game.id]] == 1.0
    rating = db.query(Rating).filter_by(user_id=user.id, game_id=game.id).one()
    rating.score = 1.0
    db.commit()
    updated = RecommenderEngine(db)
    after, _ = updated.personal_scores(user.id, [])
    assert after[updated._game_index[game.id]] == 0.0
    assert updated.local_model_status["status"] == "stale"
    # Quitamos una nota para tener al menos un candidato no valorado.
    db.delete(rating)
    db.commit()
    recs = RecommenderEngine(db).recommend(user.id, strategy="ia_local")
    assert recs and recs[0].source == RecommendationSource.LOCAL
    assert recs[0].score == pytest.approx(sum(recs[0].components.values()))
    assert "ia_local" in recs[0].components
    assert any("demostración" in text for text in recs[0].signals)


def test_falta_de_artefacto_tiene_respaldo_sin_atribuir_ia(db, populated, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_MODEL_PATH", str(tmp_path / "missing.npz"))
    engine = RecommenderEngine(db)
    assert engine.local_model_status["status"] == "missing"
    results = engine.recommend(None, strategy="ia_local")
    assert results and all(r.source == RecommendationSource.POPULARITY for r in results)
    assert all("ia_local" not in r.components for r in results)


def test_catalogo_incompatible_no_reutiliza_ids(db, populated, tmp_path, monkeypatch):
    path = tmp_path / "model.npz"
    monkeypatch.setattr(settings, "LOCAL_MODEL_PATH", str(path))
    train_and_save(db, path, data_label="demo", epochs=5)
    populated["games"]["rpg-uno"].slug = "otro-juego"
    db.commit()
    model, status = load_for_database(db)
    assert model is None and status["status"] == "catalog_changed"


def test_archivo_roto_no_impide_recomendar(db, tmp_path, monkeypatch):
    path = tmp_path / "broken.npz"
    path.write_bytes(b"not a numpy archive")
    monkeypatch.setattr(settings, "LOCAL_MODEL_PATH", str(path))
    assert load_for_database(db) == (None, {"status": "invalid", "data_label": None})


@pytest.mark.parametrize("ids", [
    np.array([1, 2, 3, 4, 5, np.inf]),
    np.array([1, 2, 3, 4, 5, np.nan]),
    np.array([1, 2, 3, 4, 5, 6.5]),
    np.array(["1", "2", "3", "4", "5", "6"]),
    np.array([[1, 2, 3], [4, 5, 6]]),
    np.array([1, 2, 3, 4, 5, 5]),
    np.array([0, 2, 3, 4, 5, 6]),
])
def test_ids_invalidos_en_npz_usan_respaldo(db, catalog, tmp_path, monkeypatch, ids):
    path = tmp_path / "invalid_ids.npz"
    model = fit(observations(), epochs=5)
    model.game_ids = ids
    model.save(path)
    monkeypatch.setattr(settings, "LOCAL_MODEL_PATH", str(path))
    engine = RecommenderEngine(db)
    assert engine.local_model_status == {"status": "invalid", "data_label": None}
    results = engine.recommend(None, strategy="ia_local")
    assert results and all(r.source == RecommendationSource.POPULARITY for r in results)
    assert all("ia_local" not in r.components for r in results)


@pytest.mark.parametrize("eligible", [False, True])
def test_activacion_automatica_respeta_validacion_del_artefacto(
    db, populated, tmp_path, monkeypatch, eligible,
):
    path = tmp_path / "local.npz"
    monkeypatch.setattr(settings, "LOCAL_MODEL_PATH", str(path))
    user = populated["users"]["rpg0"]
    game = populated["games"]["rpg-tres"]
    db.delete(db.query(Rating).filter_by(user_id=user.id, game_id=game.id).one())
    db.commit()
    train_and_save(db, path, data_label="demo", epochs=5)
    # La prueba aísla el contrato del gate; no ajusta el entrenamiento para
    # fabricar un resultado favorable en los datos de demostración.
    model = LocalModel.load(path)
    model.metadata["automatic_eligible"] = eligible
    model.save(path)
    engine = RecommenderEngine(db)
    assert engine._local_scores(user.id) is not None
    results = engine.recommend(user.id, strategy="auto")
    assert results
    assert all(("ia_local" in r.components) == eligible for r in results)
    if eligible:
        assert all(r.components["ia_local"] > 0 for r in results)
        assert all(r.source == RecommendationSource.HYBRID for r in results)
        assert all("Modelo entrenado localmente" in r.reason for r in results)
    _, basis = engine.personal_scores(user.id, [])
    assert basis == ("ia_local" if eligible else "contenido")
