"""Fixtures aislados, nunca entrenamiento ni observaciones del producto."""
import json
import time
from sqlalchemy import func, select
from app.models import (AudienceCollectionState, Game, Review, SteamAudienceHistory, Tag)
from app.services import steam_audience_service as svc
from app.schemas.analytics import SteamHistoryReport
from tests.test_api import auth, client, data, db


def setup_histories(db, data):
    target, other = data["breve"], data["largo"]
    target.developer = other.developer = "Estudio Test"
    target.steam_app_id, other.steam_app_id = 90001, 90002
    trait = Tag(slug="test-open", name="Open World", kind="community")
    a=Game(slug="history-a", name="Fixture A", steam_app_id=90003, genres=target.genres, tags=[trait])
    b=Game(slug="history-b", name="Fixture B", steam_app_id=90004, genres=target.genres, tags=[trait])
    db.add_all([a,b]);db.flush()
    for i in range(7):
        sid=str(77000000000000000+i)
        # Un autor con dos reseñas del conjunto sigue contando una sola vez.
        db.add(Review(game_id=target.id, source="steam", steam_author_id=sid, is_recommended=i!=0, content="Fixture"))
        db.add(Review(game_id=other.id, source="steam", steam_author_id=sid, is_recommended=True, content="Fixture"))
        items=[{"appid":90001,"recommended":True},{"appid":90002,"recommended":True},
               {"appid":90003,"recommended":True},{"appid":90004,"recommended":i<5}]
        db.add(SteamAudienceHistory(steam_id=sid,checked_at=time.time(),status="ok",payload={"items":items,"complete":True}))
    db.commit();return target


def test_cohorts_distinct_authors_explicit_other_likes_and_aggregate_only(db,data):
    setup_histories(db,data)
    report=svc.history_report(db,studio="Estudio Test")
    SteamHistoryReport.model_validate(report)
    assert report["identified_reviews"]==14 and report["identified_authors"]==7
    assert report["public_histories"]==7
    group=next(g for g in report["groups"] if g["key"]=="tag:Open World")
    assert group["size"]==5 and group["denominator"]==7
    assert group["reception"]==dict(recommended=4,not_recommended=0,mixed=1,unknown=0)
    assert len(group["examples"])==2 and all(g["authors"]==5 for g in group["examples"])
    serialized=json.dumps(report)
    assert "77000000000000000" not in serialized and "steam_id" not in serialized
    assert "Fixture\"" not in serialized  # No review text.
    assert svc.history_report(db,studio="Missing")["groups"]==[]


def test_expired_and_private_histories_do_not_supply_preferences(db,data):
    setup_histories(db,data)
    histories=list(db.scalars(select(SteamAudienceHistory)))
    histories[0].checked_at-=svc.TTL+1
    histories[1].status="private"
    histories[2].status="unavailable"
    db.commit()
    report=svc.history_report(db,studio="Estudio Test")
    assert report["public_histories"]==4 and report["pending_authors"]==1
    assert report["private_histories"]==1 and report["unavailable_histories"]==1
    assert report["groups"]==[]


def test_collection_bounds_cache_privacy_and_no_game_track_users(db,data,monkeypatch):
    target=setup_histories(db,data)
    db.query(SteamAudienceHistory).delete();db.commit()
    calls=[]
    def fetch(sid,page):
        calls.append((sid,page))
        return {"status":"ok","items":[{"appid":123,"is_recommended":True,"content":"Not retained","minutes":100}],"next_page":page+1}
    monkeypatch.setattr(svc.steam_review_service,"fetch_reviews",fetch)
    monkeypatch.setattr(svc.time,"sleep",lambda _:None)
    monkeypatch.setattr(svc,"repair_game_authors",lambda *_args,**_kwargs:None)
    result=svc.collect_step(db,game_id=target.id)
    assert result["status"]=="more" and result["profiles_fetched"]==svc.BATCH_SIZE
    assert len(calls)==svc.BATCH_SIZE*svc.MAX_PAGES
    assert db.scalar(select(func.count()).select_from(SteamAudienceHistory))==svc.BATCH_SIZE
    assert all(set(h.payload["items"][0])=={"appid","recommended"} and not h.payload["complete"] for h in db.scalars(select(SteamAudienceHistory)))
    svc.collect_step(db,game_id=target.id)
    calls.clear()
    assert svc.collect_step(db,game_id=target.id)["profiles_fetched"]==0 and calls==[]
    # A formerly public account now private loses all cached items.
    history=db.scalar(select(SteamAudienceHistory).order_by(SteamAudienceHistory.steam_id))
    history.checked_at-=svc.TTL+1;db.commit()
    monkeypatch.setattr(svc.steam_review_service,"fetch_reviews",lambda *_:{"status":"private"})
    svc.collect_step(db,game_id=target.id)
    db.refresh(history)
    assert history.status=="private" and history.payload["items"]==[]


def test_database_lease_avoids_duplicate_work(db,data,monkeypatch):
    target=setup_histories(db,data)
    db.add(AudienceCollectionState(key="public-steam-collection",lease_until=time.time()+100,token="other",identity_checks={}));db.commit()
    monkeypatch.setattr(svc.steam_review_service,"fetch_reviews",lambda *_:(_ for _ in ()).throw(AssertionError("HTTP not allowed")))
    assert svc.collect_step(db,game_id=target.id)==dict(status="busy",profiles_fetched=0)


def test_api_is_developer_only_read_get_and_no_private_ids(client,db,data,monkeypatch):
    target=setup_histories(db,data)
    path="/api/v1/analytics/audience"
    monkeypatch.setattr(svc.steam_review_service,"fetch_reviews",lambda *_:(_ for _ in ()).throw(AssertionError("GET must not use HTTP")))
    for method in (client.get,client.post):
        endpoint=path if method==client.get else path+"/collect"
        assert method(endpoint,params={"game_id":target.id}).status_code==401
        assert method(endpoint,params={"game_id":target.id},headers=auth(client,"jugadora")).status_code==403
    headers=auth(client,"dev")
    report=client.get(path,params={"studio":"Estudio Test"},headers=headers)
    assert report.status_code==200 and report.headers["cache-control"]=="no-store"
    assert report.json()["steam_histories"]["groups"]
    assert "77000000000000000" not in report.text
    assert client.post(path+"/collect",params={"game_id":target.id},headers=headers).json()["status"]=="ready"
    assert client.post(path+"/collect",params={"game_id":target.id,"studio":"Estudio Test"},headers=headers).status_code==400
    assert client.post(path+"/collect",params={"game_id":999999},headers=headers).status_code==404
