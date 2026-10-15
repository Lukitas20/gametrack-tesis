"""Atribución pública sobre fixtures efímeros; nunca son datos de entrenamiento."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from tests.test_steam import db  # noqa: F401, base aislada en memoria
from app.models import Game, Review, ReviewAspect, User
from app.models.enums import Aspect, Sentiment
from app.schemas.interaction import ReviewOut
from app.services import steam_review_authors as authors
from app.services import steam_service
from app.services.steam_service import get_player_summaries_batch as player_summaries


@pytest.fixture(autouse=True)
def isolated_sources(monkeypatch):
    monkeypatch.setattr(steam_service.settings, "STEAM_API_KEY", "test-key")
    monkeypatch.setattr(steam_service.settings, "STEAM_REVIEWS_IMPORT_LIMIT", 1)
    monkeypatch.setattr(steam_service, "_http_client", lambda: pytest.fail("La prueba no permite red"))
    monkeypatch.setattr(steam_service, "get_app_reviews", lambda *args, **kwargs: [])
    monkeypatch.setattr(steam_service, "get_player_summaries_batch", lambda ids: {})
    monkeypatch.setattr(authors, "_review_page", lambda *args, **kwargs: ([], None))
    monkeypatch.setattr(steam_service, "analyze_review", lambda *args: pytest.fail("No se debe reanalizar una reseña existente"))


def game(db, suffix="one", appid=220):
    item = Game(name=f"Fixture {suffix}", slug=f"author-fixture-{suffix}", steam_app_id=appid)
    db.add(item)
    db.flush()
    return item


def review(db, item, rid="review-1", **values):
    data = dict(game_id=item.id, source="steam", steam_review_id=rid,
                author_name="Jugador de Steam", content="La experiencia original que no debe modificarse.",
                published_at=datetime(2023, 11, 14, tzinfo=timezone.utc),
                is_recommended=False, hours_at_review=42.5, helpful_count=7,
                is_analyzed=True, sentiment=Sentiment.NEGATIVE, sentiment_score=-0.75,
                sentiment_confidence=0.92,
                analyzed_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    data.update(values)
    item = Review(**data)
    db.add(item)
    db.commit()
    return item


def evidence(row):
    """Nombre e identidad pueden repararse; texto y evidencia no pueden cambiar."""
    return (row.content, row.source, row.title, row.user_id, row.steam_review_id,
            row.is_recommended, row.hours_at_review, row.helpful_count,
            row.is_analyzed, row.sentiment, row.sentiment_score,
            row.sentiment_confidence, row.analyzed_at, row.published_at,
            tuple((a.aspect, a.sentiment, a.evidence) for a in row.aspects))


def test_import_at_cap_hydrates_stored_identity_without_fetching_or_reanalysis(db, monkeypatch):
    item = game(db)
    row = review(db, item, steam_author_id="101")
    before = evidence(row)
    calls = []
    def profiles(ids):
        calls.append(ids)
        return {"101": {"personaname": "Alias público observado"}}
    monkeypatch.setattr(steam_service, "get_player_summaries_batch", profiles)
    monkeypatch.setattr(steam_service, "get_app_reviews", lambda *args, **kwargs: pytest.fail("La identidad ya está guardada"))

    assert steam_service.import_reviews(db, item, item.steam_app_id) == 0
    db.expire_all()
    assert row.author_name == "Alias público observado" and row.steam_author_id == "101"
    assert calls == [["101"]] and evidence(row) == before
    assert db.scalar(select(func.count(Review.id))) == 1


def test_import_legacy_at_cap_recovers_only_matching_review_id(db, monkeypatch):
    item = game(db)
    row = review(db, item)
    before = evidence(row)
    raw = [
        {"recommendationid": "other-review", "review": row.content, "author": {"steamid": "999"}},
        {"recommendationid": row.steam_review_id, "review": "Un texto más nuevo ajeno a la muestra.", "author": {"steamid": "102"}},
    ]
    requested = []
    monkeypatch.setattr(steam_service, "get_app_reviews", lambda *args, **kwargs: raw)
    def profiles(ids):
        requested.extend(ids)
        return {"102": {"personaname": "Nombre del autor correcto"}, "999": {"personaname": "Otro autor"}}
    monkeypatch.setattr(steam_service, "get_player_summaries_batch", profiles)

    assert steam_service.import_reviews(db, item, item.steam_app_id) == 0
    db.expire_all()
    assert row.author_name == "Nombre del autor correcto" and row.steam_author_id == "102"
    assert requested == ["102"] and evidence(row) == before
    assert db.scalar(select(func.count(Review.id))) == 1


def test_profile_unavailable_preserves_known_alias_and_existing_analysis(db, monkeypatch):
    item = game(db)
    known = review(db, item, author_name="Alias ya observado", steam_author_id="103")
    pending = review(db, item, rid="review-2", steam_author_id="104")
    before = evidence(known), evidence(pending)
    requested = []
    monkeypatch.setattr(steam_service, "get_player_summaries_batch", lambda ids: requested.extend(ids) or {})

    assert steam_service.import_reviews(db, item, item.steam_app_id) == 0
    assert known.author_name == "Alias ya observado" and pending.author_name == "Jugador de Steam"
    assert requested == ["104"] and (evidence(known), evidence(pending)) == before


def test_repair_uses_exact_ids_and_never_text_other_games_or_user_sources(db, monkeypatch):
    item = game(db)
    other = game(db, "other", appid=221)
    target = review(db, item)
    unmatched = review(db, item, rid="review-unmatched")
    user = User(username="author-fixture-account")
    db.add(user)
    db.flush()
    native = review(db, item, rid="review-native", user_id=user.id, source="user", author_name="Jugador de Steam")
    foreign = review(db, other, rid="review-other-game")
    db.add(ReviewAspect(review_id=target.id, game_id=item.id, aspect=Aspect.STORY,
                       sentiment=Sentiment.NEGATIVE, score=-0.7,
                       confidence=0.85, evidence="Evidencia original del análisis"))
    db.commit()
    before = [evidence(row) for row in (target, unmatched, native, foreign)]
    calls = []
    def page(appid, cursor, **kwargs):
        calls.append((appid, cursor, kwargs))
        return ([
            {"recommendationid": "not-the-same-id", "review": target.content, "author": {"steamid": "999"}},
            {"recommendationid": target.steam_review_id, "review": "Texto cambiado en Steam", "author": {"steamid": "105"}},
            {"recommendationid": native.steam_review_id, "author": {"steamid": "106"}},
            {"recommendationid": foreign.steam_review_id, "author": {"steamid": "107"}},
        ], None)
    monkeypatch.setattr(authors, "_review_page", page)
    monkeypatch.setattr(steam_service, "get_player_summaries_batch", lambda ids: {sid: {"personaname": f"Alias {sid}"} for sid in ids})

    result = authors.repair_game_authors(db, item, max_pages=2, delay=0)
    db.commit()
    db.expire_all()
    assert result == {"examined": 2, "identities_recovered": 1, "names_recovered": 1, "pages": 1}
    assert target.steam_author_id == "105" and target.author_name == "Alias 105"
    assert all(row.steam_author_id is None for row in (unmatched, native, foreign))
    assert [evidence(row) for row in (target, unmatched, native, foreign)] == before
    assert len(calls) == 1 and calls[0][2]["start"] == calls[0][2]["end"]
    assert db.scalar(select(func.count(Review.id))) == 4


def test_repair_retries_saved_identity_without_review_lookup(db, monkeypatch):
    item = game(db)
    row = review(db, item)
    monkeypatch.setattr(authors, "_review_page", lambda *args, **kwargs: ([{"recommendationid": row.steam_review_id, "author": {"steamid": "108"}}], None))
    first = authors.repair_game_authors(db, item, delay=0)
    db.commit()
    assert first["identities_recovered"] == 1 and first["names_recovered"] == 0
    assert row.steam_author_id == "108"
    monkeypatch.setattr(authors, "_review_page", lambda *args, **kwargs: pytest.fail("No necesita buscar de nuevo la reseña"))
    monkeypatch.setattr(steam_service, "get_player_summaries_batch", lambda ids: {"108": {"personaname": "Alias recuperado después"}})
    second = authors.repair_game_authors(db, item, delay=0)
    assert second == {"examined": 1, "identities_recovered": 0, "names_recovered": 1, "pages": 0}
    assert row.author_name == "Alias recuperado después"


def test_repair_is_bounded_and_leaves_unmatched_review_unattributed(db, monkeypatch):
    item = game(db)
    row = review(db, item)
    seen = []
    def page(appid, cursor, **kwargs):
        seen.append(cursor)
        return ([{"recommendationid": "different-id", "review": row.content, "author": {"steamid": "109"}}], "next")
    monkeypatch.setattr(authors, "_review_page", page)
    result = authors.repair_game_authors(db, item, max_pages=1, delay=0)
    assert result["pages"] == 1 and seen == ["*"]
    assert row.steam_author_id is None and row.author_name == "Jugador de Steam"


def test_internal_author_identity_is_not_exposed_in_review_response(db):
    item = game(db)
    row = review(db, item, steam_author_id="110", author_name="Alias visible")
    response = ReviewOut.model_validate(row).model_dump()
    assert response["author_name"] == "Alias visible"
    assert "steam_author_id" not in response and "steam_review_id" not in response


def test_legacy_lookup_bounds_by_import_time_without_inventing_publication_date(db, monkeypatch):
    item = game(db)
    row = review(db, item, published_at=None)
    calls = []
    def page(appid, cursor, **kwargs):
        calls.append(kwargs)
        return ([], None)
    monkeypatch.setattr(authors, "_review_page", page)
    authors.repair_game_authors(db, item, delay=0)
    assert calls == [{"start": 946684800, "end": int(row.created_at.replace(tzinfo=timezone.utc).timestamp())}]
    assert row.published_at is None


@pytest.mark.parametrize("value", [None, {}, {"personaname": None}, {"personaname": 123}, {"personaname": " \u0000\n "}])
def test_public_names_do_not_invent_identity(value):
    assert authors.public_name(value or {}) is None


def test_public_name_preserves_unicode_but_removes_controls_and_bounds_length():
    assert authors.public_name({"personaname": "  Ñandú\u0000\n 🎮  "}) == "Ñandú 🎮"
    assert authors.public_name({"personaname": "a" * 200}) == "a" * 120


def test_profile_batches_are_deduplicated_limited_and_reject_unrequested_ids(monkeypatch):
    requests = []
    def get(url, *, params):
        batch = params["steamids"].split(",")
        requests.append(batch)
        players = [{"steamid": sid, "personaname": "Alias de prueba"} for sid in batch]
        players.extend([{"steamid": "99999", "personaname": "No solicitado"}, "malformed", None])
        return SimpleNamespace(status_code=200, json=lambda: {"response": {"players": players}})
    monkeypatch.setattr(steam_service, "_http_client", lambda: SimpleNamespace(get=get))
    expected = {str(value) for value in range(1, 206)}
    result = player_summaries(list(expected) + ["1", "bad", "", "1" * 33, "١"])
    assert set(result) == expected
    assert [len(batch) for batch in requests] == [100, 100, 5]
    assert len({sid for batch in requests for sid in batch}) == 205


@pytest.mark.parametrize("payload", [None, [], {"response": None}, {"response": []},
                                    {"response": {"players": None}}, {"response": {"players": "not a list"}}])
def test_malformed_profile_response_is_unavailable_without_inventing_names(monkeypatch, payload):
    client = SimpleNamespace(get=lambda *args, **kwargs: SimpleNamespace(status_code=200, json=lambda: payload))
    monkeypatch.setattr(steam_service, "_http_client", lambda: client)
    assert player_summaries(["111"]) == {}
