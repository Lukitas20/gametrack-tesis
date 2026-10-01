from datetime import date, datetime, timedelta, timezone
import httpx
import pytest
from app.models import Game, Genre, SteamCatalogEntry
from app.services import home_release_service as releases
from tests.test_api import client, db  # noqa: F401


def test_home_separates_unreleased_games_and_filters_critics_and_genres(client, db):
    genre = Genre(slug="indie", name="Indie")
    today = date.today()
    available = Game(slug="available", name="Disponible", released=today, metacritic=90, ratings_count=100, background_image="https://example.com/a.jpg", genres=[genre])
    future = Game(slug="future", name="Futuro", released=today + timedelta(days=10), metacritic=99, ratings_count=200, genres=[genre])
    unknown = Game(slug="unknown", name="Sin fecha", metacritic=None, ratings_count=50, genres=[genre])
    weak = Game(slug="weak", name="Nota baja", released=today, metacritic=40, ratings_count=10)
    stub = Game(slug="stub", name="Pendiente", steam_app_id=10, genres=[genre])
    software = Game(slug="software", name="No juego", steam_app_id=11, steam_synced_at=datetime.now(timezone.utc), genres=[genre])
    utility = Genre(slug="utilidades", name="Utilidades")
    legacy_software = Game(slug="legacy-software", name="Herramienta importada", steam_app_id=12,
        steam_synced_at=datetime.now(timezone.utc), genres=[genre, utility])
    db.add_all([available, future, unknown, weak, stub, software, legacy_software])
    db.add(SteamCatalogEntry(appid=11, status="non_game", last_seen_at=datetime.now(timezone.utc)))
    db.commit()
    response = client.get("/api/v1/home").json()
    assert [game["id"] for game in response["critica"]] == [available.id]
    for key in ["populares", "mejor_valorados", "recientes", "destacados"]:
        ids = {game["id"] for game in response[key]}
        assert future.id not in ids and stub.id not in ids and software.id not in ids
        assert legacy_software.id not in ids
    collection = next(item for item in response["colecciones"] if item["key"] == "indie")
    assert collection["slug"] == "indie" and collection["count"] == 2
    assert {game["id"] for game in collection["games"]} == {available.id, unknown.id}
    filtered = client.get("/api/v1/games", params={"genre":collection["slug"]}).json()
    assert all(game["id"] in {item["id"] for item in filtered["items"]} for game in collection["games"])


@pytest.fixture
def release_cache(monkeypatch):
    monkeypatch.setattr(releases, "_cached", None)
    monkeypatch.setattr(releases, "_retry_at", 0)
    clock = [100000]
    monkeypatch.setattr(releases.time, "time", lambda:clock[0])
    return clock


def test_steam_agenda_verifies_games_keeps_date_precision_and_deduplicates(monkeypatch, release_cache, client):
    calls = []
    def handler(request):
        if "search/results" in request.url.path:
            return httpx.Response(200, json={"items":[
                {"logo":"https://shared.fastly.steamstatic.com/steam/apps/1/a.jpg"},
                {"logo":"https://shared.fastly.steamstatic.com/steam/apps/1/a.jpg"},
                {"logo":"https://shared.fastly.steamstatic.com/steam/apps/2/b.jpg"},
                {"logo":"https://shared.fastly.steamstatic.com/steam/apps/3/c.jpg"},
                {"logo":"https://evil.example/apps/4/x.jpg"}]})
        appid = int(request.url.params["appids"]); calls.append(appid)
        return httpx.Response(200,json={str(appid):{"success":True,"data":{
            "type":"demo" if appid == 2 else "game", "name":"Juego anunciado",
            "header_image":"https://shared.akamai.steamstatic.com/steam/apps/1/header.jpg",
            "release_date":{"coming_soon":appid != 3,"date":"2027"}}}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        monkeypatch.setattr(releases,"_http_client",lambda:http)
        response = client.get("/api/v1/home/upcoming")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok" and data["source"] == "steam"
    assert data["items"][0]["release_label"] == "2027"
    assert len(data["items"]) == 1 and sorted(calls) == [1,2,3]
    assert data["items"][0]["store_url"] == "https://store.steampowered.com/app/1/"


def test_agenda_cache_stale_values_and_retry_cooldown(monkeypatch, release_cache):
    sample = [{"name":"Esperado"}]
    calls = []
    monkeypatch.setattr(releases,"_fetch",lambda:calls.append(1) or sample)
    assert releases.upcoming_releases()["status"] == "ok"
    assert releases.upcoming_releases()["items"] == sample and len(calls) == 1
    release_cache[0] += releases.TTL + 1
    def outage():
        calls.append(1)
        raise httpx.ConnectError("sin red")
    monkeypatch.setattr(releases,"_fetch",outage)
    assert releases.upcoming_releases()["status"] == "stale"
    assert releases.upcoming_releases()["items"] == sample and len(calls) == 2
    release_cache[0] += 86400
    assert releases.upcoming_releases()["status"] == "unavailable"
    assert releases.upcoming_releases()["items"] == [] and len(calls) == 3


@pytest.mark.parametrize("payload",[None,{}, {"items":[]}, {"items":[{"logo":"http://evil.example/apps/1/a.png"}]}])
def test_bad_agenda_has_honest_empty_state_and_no_repeated_network(monkeypatch, release_cache, payload):
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200,json=payload)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        monkeypatch.setattr(releases,"_http_client",lambda:http)
        assert releases.upcoming_releases()["status"] == "unavailable"
        assert releases.upcoming_releases()["items"] == []
        assert len(calls) == 1


def test_empty_catalog_is_valid_and_limit_is_enforced(client):
    response = client.get("/api/v1/home")
    assert response.status_code == 200 and response.json()["colecciones"] == []
    assert client.get("/api/v1/home?limit=0").status_code == 422
