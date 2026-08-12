"""Métricas de evaluación offline del recomendador.

Relevancia binaria (0/1) juzgada por anotadores humanos sobre pools de
resultados (metodología TREC: se anota la unión de los top-k de todas las
variantes, así todo lo que cualquier variante rankea alto está juzgado).

Son las tres métricas estándar de ranking con las que la tesis compara
variantes de diseño, más el acuerdo inter-anotador que valida que "relevante"
signifique lo mismo para los dos integrantes:

- **Precision@k** — de los k sugeridos, qué fracción es relevante. Es la
  experiencia directa del usuario del asistente (ve 3 sugerencias).
- **Average Precision** — precisión promediada en cada posición relevante;
  premia poner lo relevante ARRIBA, no sólo incluirlo. Su media entre
  consultas es MAP.
- **NDCG@k** — ganancia descontada por posición (log2), normalizada por el
  ranking ideal. Con relevancia binaria y k=3 cuenta una historia parecida a
  AP pero es el estándar de la literatura, así que se reporta igual.
- **Kappa de Cohen** — acuerdo entre anotadores descontando el azar. Sin un
  kappa razonable (> 0.6 es el umbral usual de "acuerdo sustancial"), las
  métricas de arriba miden ruido.

Convención sobre lo no juzgado: un ítem del ranking sin juicio cuenta como
NO relevante (supuesto estándar de pooling). Con el pool a la misma
profundidad que las métricas, no debería haber ninguno; el arnés los reporta
aparte para detectar anotaciones incompletas.
"""

from __future__ import annotations

import math


def precision_at_k(relevances: list[int], k: int) -> float:
    """Fracción de relevantes entre los primeros ``k`` del ranking.

    Si el ranking trae menos de ``k`` ítems, el denominador sigue siendo
    ``k``: devolver 2 sugerencias relevantes cuando se pidieron 3 no es lo
    mismo que devolver 3 relevantes.
    """
    if k <= 0:
        return 0.0
    return sum(relevances[:k]) / k


def average_precision(relevances: list[int], total_relevant: int) -> float:
    """Precisión promediada en cada posición relevante del ranking.

    ``total_relevant`` es cuántos relevantes existen EN EL POOL de la
    consulta (no en el ranking): un ranking que recupera 2 de 6 relevantes
    posibles no puede valer lo mismo que uno que recupera 2 de 2.
    """
    if total_relevant <= 0:
        return 0.0
    hits = 0
    accumulated = 0.0
    for position, relevant in enumerate(relevances, start=1):
        if relevant:
            hits += 1
            accumulated += hits / position
    return accumulated / total_relevant


def ndcg_at_k(relevances: list[int], total_relevant: int, k: int) -> float:
    """NDCG con ganancias binarias y descuento logarítmico estándar.

    El ideal (IDCG) tiene ``min(total_relevant, k)`` unos al principio: si en
    el pool hay un solo relevante, ponerlo primero ya es el ranking perfecto.
    """
    if k <= 0 or total_relevant <= 0:
        return 0.0
    dcg = sum(
        relevant / math.log2(position + 1)
        for position, relevant in enumerate(relevances[:k], start=1)
    )
    ideal_hits = min(total_relevant, k)
    idcg = sum(1.0 / math.log2(position + 1) for position in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def cohen_kappa(labels_a: list[int], labels_b: list[int]) -> float:
    """Acuerdo entre dos anotadores binarios, descontando el azar.

    kappa = (p_o - p_e) / (1 - p_e), con p_o el acuerdo observado y p_e el
    esperado por azar según las proporciones marginales de cada anotador.
    Si ambos anotan todo igual (marginales degeneradas y acuerdo perfecto),
    kappa es 1 por convención; si el acuerdo esperado ya es 1 y el observado
    no, es 0.
    """
    if len(labels_a) != len(labels_b):
        raise ValueError("los dos anotadores deben juzgar los mismos ítems")
    n = len(labels_a)
    if n == 0:
        return 0.0

    observed = sum(1 for a, b in zip(labels_a, labels_b) if a == b) / n
    p_yes_a = sum(labels_a) / n
    p_yes_b = sum(labels_b) / n
    expected = p_yes_a * p_yes_b + (1 - p_yes_a) * (1 - p_yes_b)

    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else 0.0
    return (observed - expected) / (1 - expected)
