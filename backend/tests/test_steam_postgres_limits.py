"""Steam writes must fit the declared VARCHAR limits enforced by PostgreSQL."""

import pytest
from sqlalchemy import String, create_engine, event, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Game, Genre, Review, Tag, User
from app.services import steam_service as service


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    def no_network():
        raise AssertionError("These ingestion tests must never call Steam")

    monkeypatch.setattr(service, "_http_client", no_network)
    monkeypatch.setattr(service, "get_review_totals", lambda _appid: None)
    with Session(engine) as session:
        @event.listens_for(session, "before_flush")
        def enforce_varchar_lengths(db, *_args):
            # SQLite ignores VARCHAR(n). This deliberately validates against
            # model metadata so the test detects values PostgreSQL rejects.
            for row in db.new | db.dirty:
                for column in row.__table__.columns:
                    value = getattr(row, column.key, None)
                    if isinstance(column.type, String) and column.type.length and value is not None:
                        assert len(value) <= column.type.length, f"{row.__table__.name}.{column.name} too long"

        yield session
    engine.dispose()


def long_payload():
    return {
        "type": "game", "name": "Áventura " * 35 + "Final",
        "developers": ["Desarrollador " * 15, "Otro estudio"],
        "publishers": ["Publisher " * 20, "Otro editor"],
        "short_description": "Descripción completa " * 30,
        "header_image": "https://example.invalid/" + "x" * 400,
        "genres": [{"description": "Un género extenso " * 8 + "A"}],
        "categories": [{"description": "Categoría compartida " * 8 + "B"}],
    }


def test_parser_bounds_only_varchar_fields_without_cutting_free_text():
    payload = long_payload()
    parsed = service.parse_steam_game(payload)
    assert parsed["name"] == payload["name"][:200]
    assert parsed["developer"] == ", ".join(payload["developers"])[:120]
    assert parsed["publisher"] == ", ".join(payload["publishers"])[:120]
    assert len(parsed["slug"]) <= Game.__table__.c.slug.type.length
    assert parsed["slug"] == service.parse_steam_game(payload)["slug"]
    assert parsed["description"] == payload["short_description"].strip()
    assert parsed["background_image"] == payload["header_image"]


def test_long_game_names_with_identical_prefix_have_distinct_stable_slugs():
    first = service.parse_steam_game({"name": "Same title " * 30 + "Alpha"})
    second = service.parse_steam_game({"name": "Same title " * 30 + "Beta"})
    assert first["name"] == second["name"]
    assert first["slug"] != second["slug"]
    assert len(first["slug"]) <= 120 and len(second["slug"]) <= 120


@pytest.mark.parametrize("model", [Genre, Tag])
def test_long_categories_remain_distinct_and_repeated_import_keeps_ids(db, model):
    prefix = "Categoría muy larga compartida " * 4
    first = service._get_or_create(db, model, prefix + "primera")
    second = service._get_or_create(db, model, prefix + "segunda")
    db.commit()
    first_id, first_slug = first.id, first.slug
    assert first.id != second.id and first.slug != second.slug
    assert first.name == second.name == (prefix + "primera")[:60]
    assert len(first.slug) <= 60 and len(second.slug) <= 60
    repeated = service._get_or_create(db, model, prefix + "primera")
    assert repeated.id == first_id and repeated.slug == first_slug
    assert len(db.scalars(select(model)).all()) == 2


@pytest.mark.parametrize("model", [Genre, Tag])
def test_valid_existing_category_urls_and_ids_are_preserved(db, model):
    existing = model(name="Acción", slug="accion")
    boundary = model(name="x" * 60, slug="x" * 60)
    db.add_all([existing, boundary])
    db.commit()
    assert service._get_or_create(db, model, "Acción") is existing
    assert service._get_or_create(db, model, "x" * 60) is boundary
    assert boundary.slug == "x" * 60


def test_import_resolves_multiple_slug_collisions_within_column_limit(db, monkeypatch):
    payload = long_payload()
    slug = service.parse_steam_game(payload)["slug"]
    collided_suffix = "-220"
    second_slug = slug[:120 - len(collided_suffix)].rstrip("-") + collided_suffix
    originals = [Game(slug=slug, name="Original"), Game(slug=second_slug, name="Another source")]
    db.add_all(originals)
    db.commit()
    monkeypatch.setattr(service, "get_app_details", lambda _appid: payload)
    monkeypatch.setattr(service, "import_reviews", lambda *_args, **_kwargs: 0)
    game = service.import_game(db, 220)
    assert game is not None
    assert game.slug.endswith("-220-2") and len(game.slug) <= 120
    assert game.id not in {row.id for row in originals}
    assert len(game.developer) == 120 and len(game.publisher) == 120
    assert len(game.genres[0].name) <= 60 and len(game.tags[0].slug) <= 60
    assert service.import_game(db, 220).id == game.id


def test_short_collision_keeps_existing_url_convention(db, monkeypatch):
    db.add(Game(slug="half-life-2", name="Half-Life 2"))
    db.commit()
    monkeypatch.setattr(service, "get_app_details", lambda _appid: {"name": "Half-Life 2", "type": "game"})
    monkeypatch.setattr(service, "import_reviews", lambda *_args, **_kwargs: 0)
    assert service.import_game(db, 220).slug == "half-life-2-220"


def test_refresh_limits_new_metadata_and_preserves_existing_game_identity(db, monkeypatch):
    game = Game(name="Nombre elegido", slug="url-que-no-debe-cambiar", steam_app_id=220)
    db.add(game)
    db.commit()
    game_id = game.id
    payload = long_payload()
    monkeypatch.setattr(service, "get_app_details", lambda _appid: payload)
    monkeypatch.setattr(service, "import_reviews", lambda *_args, **_kwargs: 0)
    assert service.refresh_game(db, game)
    assert game.id == game_id and game.name == "Nombre elegido"
    assert game.slug == "url-que-no-debe-cambiar"
    assert game.developer == ", ".join(payload["developers"])[:120]
    first_genre_id = game.genres[0].id
    assert service.refresh_game(db, game)
    assert game.genres[0].id == first_genre_id


def test_review_author_is_bounded_and_oversize_external_id_is_not_truncated(db, monkeypatch):
    game = Game(name="Juego", slug="juego", steam_app_id=220)
    db.add(game)
    db.commit()
    content = "La historia está muy bien y el juego es divertido."
    valid_id = "9" * 32
    monkeypatch.setattr(service, "get_app_reviews", lambda *_args, **_kwargs: [
        {"recommendationid": valid_id, "review": content, "author": {"steamid": "123"}},
        {"recommendationid": valid_id + "0", "review": content, "author": {"steamid": "123"}},
        {"recommendationid": "", "review": content, "author": {"steamid": "123"}},
    ])
    monkeypatch.setattr(service, "get_player_summaries_batch", lambda _ids: {
        "123": {"personaname": "Jugador " * 40},
    })
    assert service.import_reviews(db, game, 220) == 1
    review = db.scalar(select(Review).where(Review.game_id == game.id))
    assert review.steam_review_id == valid_id
    assert review.author_name == ("Jugador " * 40)[:120]
    assert service.import_reviews(db, game, 220) == 0


def test_linked_profile_name_fits_user_column(db, monkeypatch):
    user = User(username="player", hashed_password="unused")
    db.add(user)
    db.commit()
    monkeypatch.setattr(service, "get_player_summary", lambda _steam_id: {
        "personaname": "Nombre Steam " * 20, "avatarfull": "https://example.invalid/avatar",
    })
    linked = service.link_steam_account(db, user, "76561198000000000")
    assert linked.id == user.id
    assert linked.steam_username == ("Nombre Steam " * 20)[:100]
    assert linked.steam_id == "76561198000000000"
