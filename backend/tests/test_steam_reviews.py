"""Reseñas por autor, paginación, identidad y conservación de notas propias."""
from copy import deepcopy
from unittest.mock import Mock

import httpx
import pytest
from sqlalchemy import select

from tests.test_steam_profile import client, db, player, source, SID  # noqa: F401
from app.models import Rating, SteamProfileCache
from app.services import steam_profile_service as profiles
from app.services import steam_review_service as service


def review_box(appid=220, recommended=True, content="Muy bueno", review_id="123"):
    thumb = "Up" if recommended else "Down"
    return f'''<div class="review_box">
      <a href="https://steamcommunity.com/app/{appid}"><img class="game_capsule"></a>
      <div class="rightcol"><div class="thumb"><img src="https://community.fastly.steamstatic.com/public/shared/images/userreviews/icon_thumbs{thumb}.png"></div>
      <div class="content">{content}</div><div class="hours">12.5 hrs on record (8.0 hrs at review time)</div>
      <div class="posted">Posted 27 November, 2024.</div>
      <a id="RecommendationVoteUpBtn{review_id}"></a></div></div>'''


def html_page(boxes="", total=0, next_page=None):
    link = f'<a class="pagebtn" href="?p={next_page}&amp;l=english">&gt;</a>' if next_page else ""
    return f'<div class="review_list"><div class="giantNumber ellipsis">{total}</div><div class="giantNumber">999</div>{boxes}{link}</div>'


def test_parser_preserves_binary_review_text_and_pagination():
    result = service.parse_reviews(html_page(review_box(content="Hola &amp; adiós<br><b>Excelente</b>") + review_box(400, False, "No me gustó", "456"), 13, 2), SID, 1)
    assert result["status"] == "ok" and result["total"] == 13 and result["next_page"] == 2
    positive, negative = result["items"]
    assert positive["content"] == "Hola & adiós\nExcelente"
    assert positive["minutes"] == 750 and positive["review_id"] == "123"
    assert positive["url"] == f"https://steamcommunity.com/profiles/{SID}/recommended/220/"
    assert positive["is_recommended"] is True and negative["is_recommended"] is False
    assert "score" not in positive
    assert service.parse_reviews(html_page(review_box(400, False), 13), SID, 2)["next_page"] is None


def test_parser_distinguishes_empty_private_and_broken_pages():
    assert service.parse_reviews(html_page(), SID, 1)["status"] == "ok"
    assert service.parse_reviews('<div class="profile_private_info">Private</div>', SID, 1) == {"status": "private"}
    assert service.parse_reviews("<html>Login or error</html>", SID, 1) == {"status": "unavailable"}
    assert service.parse_reviews(html_page(total=10), SID, 1) == {"status": "unavailable"}
    assert service.parse_reviews(html_page(review_box().replace("icon_thumbsUp.png", "unknown.png"), 1), SID, 1) == {"status": "unavailable"}


def test_parser_does_not_trust_links_or_execute_markup():
    box = review_box(content='<script>alert("x")</script>&lt;img src=x onerror=alert(1)&gt;')
    result = service.parse_reviews(html_page(box, 1), SID, 1)
    assert result["items"][0]["content"] == '<img src=x onerror=alert(1)>'
    bad_link = box.replace("https://steamcommunity.com/app/220", "https://evil.example/app/220")
    assert service.parse_reviews(html_page(bad_link, 1), SID, 1)["status"] == "unavailable"


def test_fetch_uses_public_listing_without_api_key_and_sanitizes_errors(monkeypatch):
    fake = Mock()
    fake.get.return_value = httpx.Response(200, text=html_page(review_box(), 1), request=httpx.Request("GET", "https://steamcommunity.com/"))
    monkeypatch.setattr(profiles.steam_service, "_http_client", lambda: fake)
    assert service.fetch_reviews(SID, 1)["status"] == "ok"
    args, kwargs = fake.get.call_args
    assert args[0] == f"https://steamcommunity.com/profiles/{SID}/recommended/"
    assert kwargs["params"] == {"p": 1, "l": "english"}
    fake.get.side_effect = httpx.ConnectError("secret-value-in-error")
    assert service.fetch_reviews(SID, 1) == {"status": "unavailable"}


def test_reviews_require_authentication_verified_identity_and_valid_page(client, db):
    from tests.test_steam_auth import register
    assert client.get("/api/v1/steam/me/reviews").status_code == 401
    account = register(client)
    headers = {"Authorization": "Bearer " + account["access_token"]}
    assert client.get("/api/v1/steam/me/reviews", headers=headers).status_code == 403
    assert client.get("/api/v1/steam/me/reviews?page=0", headers=headers).status_code == 422


def test_reviews_are_cached_paged_and_do_not_create_ratings(client, db, player, source, monkeypatch):
    user, headers = player
    fetch = Mock(side_effect=lambda sid, page: service.parse_reviews(html_page(review_box(220 if page == 1 else 400), 2, 2 if page == 1 else None), sid, page))
    monkeypatch.setattr(service, "fetch_reviews", fetch)
    first = client.get("/api/v1/steam/me/reviews", headers=headers)
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    assert first.json()["items"][0]["name"] == "Half-Life 2"
    assert first.json()["items"][0]["game_id"] is None  # no necesita importar el juego
    client.get("/api/v1/steam/me/reviews", headers=headers)
    client.post("/api/v1/steam/me/reviews/sync", headers=headers)
    assert fetch.call_count == 1
    second = client.get("/api/v1/steam/me/reviews?page=2", headers=headers).json()
    assert second["items"][0]["appid"] == 400 and second["next_page"] is None
    assert fetch.call_count == 2
    assert not db.scalars(select(Rating)).all()
    profile = client.get("/api/v1/steam/me/profile", headers=headers).json()
    assert "user_reviews" not in profile["library"]
    assert profile["library"]["rated_count"] == 0


def test_library_refresh_preserves_review_cache(client, db, player, source, monkeypatch):
    user, headers = player
    monkeypatch.setattr(service, "fetch_reviews", lambda sid, page: service.parse_reviews(html_page(review_box(), 1), sid, page))
    client.get("/api/v1/steam/me/reviews", headers=headers)
    cache = db.get(SteamProfileCache, user.id)
    previous = deepcopy(cache.library["user_reviews"])
    cache.checked_at = 0; db.commit()
    client.post("/api/v1/steam/me/sync", headers=headers)
    assert db.get(SteamProfileCache, user.id).library["user_reviews"] == previous


def test_failed_refresh_keeps_previous_but_private_clears_all_pages(client, db, player, source, monkeypatch):
    user, headers = player
    monkeypatch.setattr(service, "fetch_reviews", lambda sid, page: service.parse_reviews(html_page(review_box(), 1), sid, page))
    client.get("/api/v1/steam/me/reviews", headers=headers)
    client.get("/api/v1/steam/me/reviews?page=2", headers=headers)
    def expire():
        cache = db.get(SteamProfileCache, user.id)
        library = deepcopy(cache.library)
        library["user_reviews"]["1"]["checked_at"] = 0
        cache.library = library; db.commit()
    expire()
    monkeypatch.setattr(service, "fetch_reviews", lambda *_: {"status": "unavailable"})
    failed = client.post("/api/v1/steam/me/reviews/sync", headers=headers).json()
    assert failed["status"] == "unavailable" and len(failed["items"]) == 1
    expire()
    monkeypatch.setattr(service, "fetch_reviews", lambda *_: {"status": "private"})
    private = client.post("/api/v1/steam/me/reviews/sync", headers=headers).json()
    assert private["status"] == "private" and private["items"] == []
    pages = db.get(SteamProfileCache, user.id).library["user_reviews"]
    assert list(pages) == ["1"] and "items" not in pages["1"]
