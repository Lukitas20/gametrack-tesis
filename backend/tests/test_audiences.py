"""Fixtures aislados: prueban agrupación y privacidad, no evalúan el producto."""
from app.models import GameList, GameListItem, Rating, Review, User, UserPreference, UserRole
from app.services.audience_service import audience_report
from tests.test_api import auth, client, data, db


def test_scope_distinct_people_sources_preferences_and_private_lists(db,data):
    game=data["breve"]
    users=[User(username=f"audience-test-{i}",preferences_source="manual") for i in range(6)]
    excluded=User(username="legacy-audience-test",bio="Perfil de gusto: prueba aislada")
    db.add_all(users+[excluded]);db.flush()
    for user in users:
        db.add(Rating(user_id=user.id,game_id=game.id,score=4))
        db.add(UserPreference(user_id=user.id,genre_id=game.genres[0].id))
    db.add(Rating(user_id=excluded.id,game_id=game.id,score=5))
    # Un segundo juego no debe afectar el conteo del primero.
    db.add(Rating(user_id=data["player"].id,game_id=data["largo"].id,score=1))
    hidden=GameList(user_id=data["player"].id,name="private",is_public=False)
    db.add(hidden);db.flush();db.add(GameListItem(list_id=hidden.id,game_id=game.id))
    db.add_all([Review(game_id=game.id,source="steam",content="Fixture aislado",is_recommended=True) for _ in range(7)])
    db.add(Review(game_id=game.id,source="synthetic",content="Excluir"));db.commit()
    report=audience_report(db,game_id=game.id)
    assert report["gametrack_users"]==6 and report["steam_reviews"]==7
    assert report["coverage"]["ratings"]==6 and report["coverage"]["public_lists"]==0
    groups={group["key"]:group for group in report["groups"]}
    assert groups["rating_high"]["size"]==6
    assert not any(key.startswith("steam_") for key in groups)
    assert report["steam_histories"]["identified_authors"]==0
    assert groups["rating_low"]["size"] is None
    assert any(group["source"]=="Preferencias manuales GameTrack" and group["size"]==6 for group in report["groups"])
    assert not any(user.username in str(report) for user in users)
    assert "steam_author_id" not in str(report) and "user_id" not in str(report)


def test_empty_scope_does_not_return_whole_platform(db,data):
    report=audience_report(db,studio="Estudio sin observaciones")
    assert report["games"]==report["gametrack_users"]==report["steam_reviews"]==0
    assert all(group["size"] is None for group in report["groups"])


def test_api_keeps_developer_permissions_and_report_integration(client,db,data,monkeypatch):
    data["breve"].developer="Estudio Test";db.commit()
    monkeypatch.setattr("app.services.steam_service.maybe_refresh",lambda *_:True)
    path=f"/api/v1/analytics/games/{data['breve'].id}"
    assert client.get(path).status_code==401
    assert client.get(path,headers=auth(client,"jugadora")).status_code==403
    response=client.get(path,headers=auth(client,"dev"))
    assert response.status_code==200 and response.json()["audience"]["games"]==1
    assert response.headers["cache-control"]=="no-store"
    studio=client.get("/api/v1/analytics/studio",headers=auth(client,"dev"))
    assert studio.status_code==200 and studio.json()["audience"]["games"]==1
    assert studio.headers["cache-control"]=="no-store"
