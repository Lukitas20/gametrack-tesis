"""GTS sólo recibe aprendizaje local validado, con opiniones actuales propias."""

import numpy as np
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.ml.local_model import LocalModel, fit
from app.ml.recommender import RecommenderEngine
from app.models import Rating
from tests.test_recommender import db, catalog, populated  # noqa: F401


@pytest.fixture
def channel(db, catalog, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_MODEL_PATH", str(tmp_path / "no-model.npz"))
    engine = RecommenderEngine(db)
    rows = [(uid, gid, 5.0 if (uid < 5) == (gid < 4) else 1.0)
            for uid in range(10) for gid in engine.game_ids]
    engine.local_model = fit(rows)
    # Se aísla el contrato de activación. La evaluación real del artefacto
    # tiene sus pruebas; este fixture no declara datos productivos observados.
    engine.local_model_status = {"status": "ready", "data_label": "observed", "automatic_eligible": True}
    return engine


def opinions():
    return {
        1: {"source": "rating", "rating": 4.5, "weight": .75},
        4: {"source": "steam_review", "recommended": False, "weight": -1.},
        5: {"source": "play_feedback", "weight": -.75},
    }


@pytest.mark.parametrize("status", [
    {"status": "stale", "data_label": "observed", "automatic_eligible": True},
    {"status": "ready", "data_label": "demo", "automatic_eligible": True},
    {"status": "ready", "data_label": "observed", "automatic_eligible": False},
    {"status": "ready", "data_label": "observed"},
    {"status": "missing", "data_label": "observed", "automatic_eligible": True},
    {"status": "invalid", "data_label": "observed", "automatic_eligible": True},
    {"status": "ready", "data_label": None, "automatic_eligible": True},
])
def test_no_activa_demo_obsoleto_no_validado_o_inaccesible(channel, monkeypatch, status):
    channel.local_model_status = status
    monkeypatch.setattr(channel.local_model, "predict", lambda *a: pytest.fail("Este artefacto no debe intervenir en GTS"))
    assert channel.personal_local_scores(opinions()) is None


def test_falta_artefacto_no_produce_afinidad_aprendida(channel):
    channel.local_model = None
    assert channel.personal_local_scores(opinions()) is None


def test_usa_opiniones_actuales_sin_exigir_estrellas_ni_usuario_en_snapshot(channel, monkeypatch):
    calls = []
    predict = channel.local_model.predict

    def capture(history):
        calls.append(history)
        return predict(history)

    monkeypatch.setattr(channel.local_model, "predict", capture)
    assert not channel._user_index
    values, support = channel.personal_local_scores(opinions())
    assert calls == [[(1, 4.5), (4, 1.0), (5, 1.5)]]
    assert values.shape == support.shape == (len(channel.game_ids),)
    assert values[channel._game_index[2]] > values[channel._game_index[6]] + .2
    np.testing.assert_allclose(support, 10 / 15)
    assert np.all((0 <= values) & (values <= 1))


def test_recomendaciones_propias_binarias_y_feedback_tienen_conversion_explicita(channel, monkeypatch):
    calls = []
    predict = channel.local_model.predict

    def capture(history):
        calls.append(history)
        return predict(history)

    monkeypatch.setattr(channel.local_model, "predict", capture)
    signals = {
        1: {"source": "gametrack_review", "recommended": True, "weight": 1.},
        4: {"source": "steam_review", "recommended": False, "weight": -1.},
        5: {"source": "play_feedback", "weight": .75},
    }
    assert channel.personal_local_scores(signals) is not None
    assert calls == [[(1, 5.0), (4, 1.0), (5, 4.5)]]


def test_horas_no_fabrican_notas_ni_completan_el_minimo_de_opiniones(channel, monkeypatch):
    signals = opinions()
    signals.pop(5)
    signals[2] = {"source": "steam_playtime", "minutes": 90000, "weight": .9}
    monkeypatch.setattr(channel.local_model, "predict", lambda *a: pytest.fail("Sólo hay dos opiniones explícitas"))
    assert channel.personal_local_scores(signals) is None


def test_no_duplica_notas_viejas_del_snapshot_ni_reintroduce_horas(db, populated, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_MODEL_PATH", str(tmp_path / "no-model.npz"))
    engine = RecommenderEngine(db)
    user = populated["users"]["rpg0"]
    observed, snapshot = engine._history(engine._user_index[user.id])
    assert dict(zip((engine.game_ids[index] for index in observed), snapshot))[1] == 5.
    engine.local_model = fit([(u, g, float(r)) for u, g, r in db.execute(
        select(Rating.user_id, Rating.game_id, Rating.score)
    )])
    engine.local_model_status = {"status": "ready", "data_label": "observed", "automatic_eligible": True}
    calls = []
    predict = engine.local_model.predict

    def capture(history):
        calls.append(history)
        return predict(history)

    monkeypatch.setattr(engine.local_model, "predict", capture)
    signals = {
        1: {"source": "rating", "rating": 1., "weight": -1.},
        4: {"source": "steam_review", "recommended": True, "weight": 1.},
        5: {"source": "play_feedback", "weight": .75},
        2: {"source": "steam_playtime", "minutes": 90000, "weight": .9},
    }
    values, _ = engine.personal_local_scores(signals)
    assert calls == [[(1, 1.0), (4, 5.0), (5, 4.5)]]
    assert values[engine._game_index[6]] > values[engine._game_index[2]] + .2


@pytest.mark.parametrize("extra", [
    {"source": "rating", "rating": 3., "weight": 0.},
    {"source": "rating", "rating": 9., "weight": 1.},
    {"source": "rating", "rating": float("nan"), "weight": 1.},
    {"source": "steam_review", "recommended": "true", "weight": 1.},
    {"source": "play_feedback", "weight": float("inf")},
    {"source": "play_feedback", "weight": 2.},
    {"source": "another_users_review", "recommended": True, "weight": 1.},
])
def test_no_cuenta_senales_neutrales_malformadas_o_fuentes_ajenas(channel, monkeypatch, extra):
    signals = opinions()
    signals[5] = extra
    monkeypatch.setattr(channel.local_model, "predict", lambda *a: pytest.fail("La tercera señal no es comparable"))
    assert channel.personal_local_scores(signals) is None


def test_alinea_por_ids_y_reduce_soporte_con_menos_dos_notas(channel):
    channel.local_model = LocalModel(
        game_ids=np.array([5, 1, 3, 999]), factors=np.zeros((4, 2)),
        biases=np.zeros(4), counts=np.array([10, 2, 1, 100]), mean=3.,
    )
    channel.local_model.predict = lambda _: np.array([4., 1., 5., 5.])
    signals = {
        gid: {"source": "rating", "rating": 5., "weight": 1.}
        for gid in (1, 3, 5, 999)
    }
    values, support = channel.personal_local_scores(signals)
    np.testing.assert_allclose(values, [0., 0., 0., 0., .75, 0.])
    np.testing.assert_allclose(support, [2 / 7, 0., 0., 0., 10 / 15, 0.])
