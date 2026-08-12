"""Pruebas de las métricas de evaluación y del arnés.

Las métricas van directo a la tabla comparativa de la tesis: un NDCG mal
calculado invalida el capítulo entero de evaluación, así que acá se
verifican contra valores calculados a mano, no contra otra implementación.

El test de paridad es la garantía metodológica del arnés: la variante
"producción" del arnés y el endpoint real tienen que producir el mismo
ranking sobre el mismo catálogo. Si divergen, el arnés estaría midiendo un
sistema que no es el que usa la gente — y toda la tabla sería sobre otra
cosa.
"""

from __future__ import annotations

import math

import pytest

from app.ml.evaluacion import (
    average_precision,
    cohen_kappa,
    ndcg_at_k,
    precision_at_k,
)

# ---------------------------------------------------------------------------
# Métricas contra valores a mano
# ---------------------------------------------------------------------------


def test_precision_at_k_a_mano() -> None:
    assert precision_at_k([1, 0, 1, 1], 3) == pytest.approx(2 / 3)
    assert precision_at_k([0, 0, 0], 3) == 0.0
    assert precision_at_k([1, 1, 1], 3) == 1.0
    # Ranking más corto que k: el denominador sigue siendo k. Devolver dos
    # relevantes cuando se pidieron tres no es un ranking perfecto.
    assert precision_at_k([1, 1], 3) == pytest.approx(2 / 3)


def test_average_precision_a_mano() -> None:
    # Relevantes en posiciones 1 y 3, con 2 relevantes en el pool:
    # AP = (1/1 + 2/3) / 2 = 5/6
    assert average_precision([1, 0, 1, 0], total_relevant=2) == pytest.approx(5 / 6)
    # El mismo ranking, pero el pool tenía 4 relevantes: recuperar 2 de 4
    # vale la mitad que recuperar 2 de 2.
    assert average_precision([1, 0, 1, 0], total_relevant=4) == pytest.approx(5 / 12)
    assert average_precision([0, 0, 0], total_relevant=3) == 0.0
    assert average_precision([1, 1], total_relevant=0) == 0.0


def test_average_precision_premia_el_orden() -> None:
    """Mismos relevantes, distinto orden: arriba tiene que valer más."""
    arriba = average_precision([1, 1, 0, 0], total_relevant=2)
    abajo = average_precision([0, 0, 1, 1], total_relevant=2)
    assert arriba == pytest.approx(1.0)
    assert arriba > abajo


def test_ndcg_a_mano() -> None:
    # DCG([1,0,1]) = 1/log2(2) + 0 + 1/log2(4) = 1.5
    # IDCG con 2 relevantes = 1/log2(2) + 1/log2(3) ~ 1.6309
    expected = 1.5 / (1 / math.log2(2) + 1 / math.log2(3))
    assert ndcg_at_k([1, 0, 1], total_relevant=2, k=3) == pytest.approx(expected)
    assert ndcg_at_k([1, 1, 1], total_relevant=3, k=3) == pytest.approx(1.0)
    assert ndcg_at_k([0, 0, 0], total_relevant=2, k=3) == 0.0


def test_ndcg_con_un_solo_relevante_posible() -> None:
    """El ideal se normaliza por lo que EXISTE en el pool: si hay un solo
    relevante y está primero, el ranking ya es perfecto."""
    assert ndcg_at_k([1, 0, 0], total_relevant=1, k=3) == pytest.approx(1.0)


def test_kappa_a_mano() -> None:
    # 8 ítems, acuerdo en 6. p_o = 0.75; marginales: A dice sí 4/8, B 4/8
    # -> p_e = 0.5; kappa = (0.75 - 0.5) / 0.5 = 0.5
    a = [1, 1, 1, 1, 0, 0, 0, 0]
    b = [1, 1, 1, 0, 1, 0, 0, 0]
    assert cohen_kappa(a, b) == pytest.approx(0.5)


def test_kappa_acuerdo_perfecto_y_azar() -> None:
    assert cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == pytest.approx(1.0)
    # Acuerdo observado igual al esperado por azar: kappa 0.
    assert cohen_kappa([1, 1, 0, 0], [1, 0, 1, 0]) == pytest.approx(0.0)
    # Todo igual y degenerado (los dos dicen siempre sí): 1 por convención.
    assert cohen_kappa([1, 1], [1, 1]) == pytest.approx(1.0)


def test_kappa_exige_los_mismos_items() -> None:
    with pytest.raises(ValueError):
        cohen_kappa([1, 0], [1])


# ---------------------------------------------------------------------------
# Paridad arnés <-> endpoint
# ---------------------------------------------------------------------------

# Reutiliza el catálogo canónico de los golden: mismo fixture, misma app.
from fastapi.testclient import TestClient  # noqa: E402

from app.api.v1.endpoints.quiz import QuizRequest, run_quiz  # noqa: E402
from app.ml.recommender import EngineConfig, RecommenderEngine  # noqa: E402
from tests.test_quiz_golden import client, db, sin_red  # noqa: E402,F401


def test_paridad_arnes_endpoint(client: TestClient, db) -> None:  # noqa: F811
    """La variante 'producción' del arnés == el endpoint real, mismo orden.

    Si este test falla, el arnés está evaluando OTRO sistema que el que usa
    la gente, y ningún número de la tabla comparativa es defendible.
    """
    payload = {
        "mood": "competir", "company": "en-linea",
        "max_playtime": None, "priority_aspect": None,
    }
    via_endpoint = [
        pick["game"]["slug"]
        for pick in client.post("/api/v1/quiz/suggest", json=payload).json()["picks"]
    ]

    engine = RecommenderEngine(db, config=EngineConfig())  # default = producción
    outcome = run_quiz(db, QuizRequest(**payload), engine=engine)
    via_arnes = [outcome.games[c.game_id].slug for c in outcome.ranked[:3]]

    assert via_arnes == via_endpoint


def test_variantes_distintas_pueden_diferir(db) -> None:  # noqa: F811
    """Sanidad: apagar las comunitarias tiene que poder cambiar el ranking
    (si todas las variantes rankearan igual, la tabla no mediría nada)."""
    from app.ml import quiz_vocab

    produccion = RecommenderEngine(db, config=EngineConfig())
    sin_comunitarias = RecommenderEngine(db, config=EngineConfig(use_community_tags=False))

    profile = quiz_vocab.MOOD_PROFILES["competir"]
    top = lambda engine: [r.game_id for r in engine.suggest_by_mood(profile, limit=5)]  # noqa: E731
    # Con el perfil de competir, el corpus sin etiquetas comunitarias no
    # puede ver "competitive"/"pvp": los rankings deben divergir.
    assert top(produccion) != top(sin_comunitarias)


# ---------------------------------------------------------------------------
# El pool no puede pisar la anotación
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

from scripts.eval_arnes import _write_annotation_csv  # noqa: E402


def _pool_row(consulta_id: str, slug: str) -> list[str]:
    return [consulta_id, f"desc {consulta_id}", slug, slug.title(), "Acción", "fps"]


def test_regenerar_el_pool_conserva_lo_ya_anotado(tmp_path: Path) -> None:
    """Re-correr ``--pool`` (para sumar una variante) NO puede llevarse
    puesto el trabajo de anotación.

    Son ~1000 juicios por cabeza, un fin de semana entre los dos, y el CSV
    se reescribe entero en cada corrida: sin esto, agregar una variante
    borraba todo en silencio. El juicio "¿le sirve este juego a alguien que
    pidió esto?" no depende de qué variante lo propuso, así que sobrevive.
    """
    path = tmp_path / "anotacion_a.csv"
    primera = [_pool_row("C01", "counter-strike-2"), _pool_row("C01", "dota-2")]
    assert _write_annotation_csv(path, primera) == (0, 2)

    # El anotador llena una fila y deja una nota.
    import csv as _csv

    with open(path, newline="", encoding="utf-8") as fh:
        filas = list(_csv.DictReader(fh))
    filas[0]["relevante"] = "1"
    filas[0]["notas"] = "clarísimo"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = _csv.DictWriter(fh, fieldnames=list(filas[0]))
        writer.writeheader()
        writer.writerows(filas)

    # Segunda corrida: el pool cambió (entra un juego nuevo, sale otro).
    segunda = [
        _pool_row("C01", "counter-strike-2"),
        _pool_row("C01", "rainbow-six-siege"),
    ]
    kept, pending = _write_annotation_csv(path, segunda)
    assert (kept, pending) == (1, 1)

    with open(path, newline="", encoding="utf-8") as fh:
        resultado = {row["juego_slug"]: row for row in _csv.DictReader(fh)}
    assert resultado["counter-strike-2"]["relevante"] == "1"
    assert resultado["counter-strike-2"]["notas"] == "clarísimo"
    assert resultado["rainbow-six-siege"]["relevante"] == ""
    assert "dota-2" not in resultado
