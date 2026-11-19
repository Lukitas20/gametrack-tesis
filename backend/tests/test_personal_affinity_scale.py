"""Escala de opiniones y horas con comparaciones sintéticas, sin cuentas ni red."""

from copy import deepcopy

import numpy as np
import pytest

from app.services.personal_history_service import (
    combine_history_affinities,
    own_history_affinity,
    playtime_strength,
)


def opinion(rating):
    return {"source": "rating", "weight": (rating - 3.) / 2., "rating": rating}


def played(hours):
    minutes = hours * 60
    return {"source": "steam_playtime", "weight": playtime_strength(minutes), "minutes": minutes}


def values(signals, similarities):
    vectors = {gid: np.asarray(vector, dtype=float) for gid, vector in similarities.items()}
    size = len(next(iter(vectors.values())))
    return combine_history_affinities(vectors, signals, size)[0]


@pytest.mark.parametrize("rating,expected", [(3.5, .625), (4., .75), (5., 1.)])
def test_perfect_similarity_preserves_explicit_opinion_scale(rating, expected):
    signal = opinion(rating)
    result = values({1: signal}, {1: [1., .5, 0.]})
    assert result[0] == pytest.approx(expected)
    assert result[0] == pytest.approx(own_history_affinity(signal))
    assert result[1] == pytest.approx(.15 + .5 * (expected - .15))
    assert result[2] == pytest.approx(.15)


def test_partial_similarity_and_rating_intensity_both_matter():
    rows = [values({1: opinion(rating)}, {1: [1., .7, .45, 0.]}) for rating in (3.5, 4., 5.)]
    assert np.all(rows[0][:-1] < rows[1][:-1])
    assert np.all(rows[1][:-1] < rows[2][:-1])
    assert all(row[0] > row[1] > row[2] > row[3] for row in rows)
    # Un género amplio con semejanza .45 no equivale a afinidad fuerte,
    # ni siquiera partiendo de la mejor nota explícita posible.
    assert max(row[2] for row in rows) < .55


def test_several_four_star_opinions_keep_the_same_anchor_without_accumulating():
    signals = {gid: opinion(4.) for gid in range(1, 21)}
    similarities = {gid: [1., .5, 0.] for gid in signals}
    result = values(signals, similarities)
    assert result == pytest.approx([.75, .45, .15])


def test_diverse_positive_opinions_preserve_more_than_one_interest():
    signals = {1: opinion(4.), 2: opinion(5.)}
    result = values(signals, {1: [1., 0., 0.], 2: [0., 1., 0.]})
    assert .65 < result[0] <= .75
    assert .90 < result[1] <= 1.
    assert result[2] == pytest.approx(.15)
    assert result[1] > result[0]
    # Ningún candidato debe ganar afinidad sumando gustos de géneros ajenos.
    alone = values({1: signals[1]}, {1: [1., 0., 0.]})
    assert result[0] <= alone[0]


@pytest.mark.parametrize("source,weight", [("steam_review", 1.), ("gametrack_review", 1.), ("play_feedback", .75)])
def test_all_explicit_positive_sources_share_their_own_affinity_anchor(source, weight):
    signal = {"source": source, "weight": weight}
    if source in {"steam_review", "gametrack_review"}:
        signal["recommended"] = True
    assert values({1: signal}, {1: [1.]})[0] == pytest.approx(own_history_affinity(signal))


def test_hours_remain_gradual_saturated_and_weaker_than_an_explicit_opinion():
    hours = (2, 100, 500, 1500, 15000)
    results = [values({1: played(amount)}, {1: [1., .45, 0.]}) for amount in hours]
    own = [result[0] for result in results]
    assert own[0] < .35
    assert own[0] < own[1] < own[2] < own[3] == own[4] < 1.
    assert own[3] - own[2] < own[2] - own[1]
    assert all(result[0] == pytest.approx(own_history_affinity(played(amount)))
               for amount, result in zip(hours, results))
    assert values({1: opinion(5.)}, {1: [1.]})[0] > own[3]


def test_unrelated_explicit_opinion_does_not_erase_sustained_playtime():
    hours = played(1500)
    alone = values({1: hours}, {1: [1., 0.]})
    combined = values({1: hours, 2: opinion(4.)}, {1: [1., 0.], 2: [0., 1.]})
    assert combined[0] == pytest.approx(alone[0])
    assert combined[1] == pytest.approx(.75)


def test_explicit_rejection_dominates_many_positive_hour_signals():
    signals = {gid: played(1500) for gid in range(1, 41)}
    similarities = {gid: [1., 0.] for gid in signals}
    signals[100] = {"source": "steam_review", "weight": -1., "recommended": False}
    similarities[100] = [1., 0.]
    result = values(signals, similarities)
    assert result[0] < .2
    assert result[1] == pytest.approx(.15)


def test_negative_penalty_keeps_its_original_scale():
    assert values({1: opinion(1.)}, {1: [1., .5, 0.]}) == pytest.approx([0., .125, .5])
    signals = {1: opinion(5.), 2: opinion(1.)}
    assert values(signals, {1: [1.], 2: [1.]})[0] == pytest.approx(.25)
    signals[1] = opinion(4.)
    assert values(signals, {1: [1.], 2: [1.]})[0] == pytest.approx(0.)


def test_calculation_does_not_rewrite_personal_signals():
    signals = {1: opinion(4.), 2: played(1500), 3: opinion(1.)}
    original = deepcopy(signals)
    similarities = {1: [1., 0.], 2: [0., 1.], 3: [0., 0.]}
    values(signals, similarities)
    assert signals == original
