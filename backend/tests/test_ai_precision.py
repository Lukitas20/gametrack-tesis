"""Regression checks for signed personal evidence and local comparisons."""
import socket
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from tests.test_score_steam_history import history, set_library
from app.api.deps import get_current_user
from app.api.v1.endpoints.recommendations import router
from app.db.database import get_db
from app.models import Review, Rating, PlayFeedback
from app.ml.recommender import recommend_for_user, invalidate_engine
from app.services import gametrack_score_service as scores
from app.services.game_comparison_service import compare_games
from app.services.game_explanation_service import explain_game


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Local analysis must not contact an external AI")
    monkeypatch.setattr(socket, "create_connection", forbidden)


def add_review(db, user, game, recommended, source="user"):
    review = Review(user_id=user.id, game_id=game.id, source=source,
                    content="Experiencia guardada por el autor", is_recommended=recommended)
    db.add(review); db.commit()
    return review


def test_own_reviews_learn_positive_and_negative_without_stars(history):
    db, user, _, games, *_ = history
    set_library(db, user, {"status":"ok", "items":[]})
    add_review(db,user,games[0],True)
    add_review(db,user,games[1],False)
    assert scores.game_score(db,user,games[2].id).affinity > scores.game_score(db,user,games[3].id).affinity + 50
    context = scores.score_context(db,user)
    assert context["gametrack_review_count"] == 2 and context["history_size"] == 2
    assert context["signals"][games[0].id]["source"] == "gametrack_review"
    assert db.scalar(select(func.count()).select_from(Rating)) == 0
    result = explain_game(db,user,games[3].id,"Comparalo con mi historial")
    assert any("no recomendaste en tu reseña de GameTrack" in point.text for point in result.answer)
    assert result.score.model_dump() == scores.game_score(db,user,games[3].id).model_dump()


@pytest.mark.parametrize("source,recommended", [("demo",True),("steam",True),("user",None)])
def test_does_not_learn_demo_imported_or_unexpressed_opinions(history,source,recommended):
    db,user,_,games,*_ = history
    set_library(db,user,{"status":"ok","items":[]})
    before=scores.game_score(db,user,games[2].id).model_dump()
    add_review(db,user,games[0],recommended,source)
    assert scores.game_score(db,user,games[2].id).model_dump() == before


def test_other_users_reviews_are_not_personal_evidence(history):
    db,user,other,games,*_ = history
    before=scores.game_score(db,user,games[2].id).affinity
    add_review(db,other,games[0],False)
    assert scores.game_score(db,user,games[2].id).affinity == before
    assert scores.score_context(db,user)["gametrack_review_count"] == 0


def test_stars_precede_own_review_and_own_review_precedes_hours(history):
    db,user,_,games,*_ = history
    set_library(db,user,{"status":"ok","items":[{"appid":100,"minutes":90000}]})
    add_review(db,user,games[0],False)
    assert scores.game_score(db,user,games[0].id).affinity == 0
    db.add(Rating(user_id=user.id,game_id=games[0].id,score=5));db.commit();invalidate_engine()
    own=scores.game_score(db,user,games[0].id)
    assert own.value == 100 and own.confidence == "known"
    assert scores.score_context(db,user)["history_size"] == 1


def test_edit_review_changes_automatic_recommendations_immediately(history):
    db,user,_,games,*_ = history
    set_library(db,user,{"status":"ok","items":[]})
    positive=add_review(db,user,games[0],True)
    negative=add_review(db,user,games[1],False)
    def ranks():
        return {r.game_id:r.score for r in recommend_for_user(db,user,limit=10)}
    first=ranks()
    assert first[games[2].id] > first[games[3].id]
    assert games[0].id not in first and games[1].id not in first
    positive.is_recommended=False;negative.is_recommended=True;db.commit()
    second=ranks()
    assert second[games[3].id] > second[games[2].id]
    assert any("reseñas de GameTrack" in s for r in recommend_for_user(db,user) for s in r.signals)


def test_automatic_steam_recommendations_use_signed_history(history):
    db,user,_,games,*_ = history
    recs=recommend_for_user(db,user,limit=10)
    ranked={r.game_id:r for r in recs}
    assert ranked[games[2].id].score > ranked[games[3].id].score
    assert not {games[0].id,games[1].id} & ranked.keys()
    assert all(abs(sum(r.components.values())-r.score)<.0001 for r in recs)


def test_confidence_distinguishes_weak_data_from_own_opinion(history):
    db,user,other,games,*_ = history
    assert scores.game_score(db,other,games[2].id).confidence == "low"
    assert scores.game_score(db,user,games[2].id).confidence == "medium"
    assert scores.game_score(db,user,games[5].id).confidence == "low"
    assert scores.game_score(db,user,games[0].id).confidence == "known"
    result=explain_game(db,user,games[2].id,"¿Qué tan confiable es esta recomendación?")
    assert any(p.text == result.score.confidence_message for p in result.answer)


def test_comparison_uses_same_scores_and_sources_without_writing_opinions(history):
    db,user,_,games,*_ = history
    def counts():
        return [db.scalar(select(func.count()).select_from(model)) for model in (Rating,Review,PlayFeedback)]
    before=counts()
    result=compare_games(db,user,[games[2].id,games[3].id])
    assert result.preferred_game_id == games[2].id
    assert counts() == before
    for item in result.items:
        assert item.explanation.score.model_dump() == scores.game_score(db,user,item.game.id).model_dump()
        assert all(set(point.reference_ids)<={r.id for r in item.explanation.references}
                   for point in item.explanation.positives+item.explanation.cautions)
    assert result.engine == "local"
    assert compare_games(db,user,[games[2].id,games[4].id]).preferred_game_id is None


def test_comparison_abstains_with_weak_history(history):
    db,_,other,games,*_ = history
    result=compare_games(db,other,[games[2].id,games[3].id])
    assert result.preferred_game_id is None and "falta evidencia" in result.conclusion


def test_comparison_api_validates_and_does_not_cache_private_result(history, monkeypatch):
    from app.services import steam_review_service
    monkeypatch.setattr(steam_review_service, "prepare_personal_history", lambda db, user: None)
    db,_,other,games,*_ = history
    app=FastAPI();app.include_router(router)
    app.dependency_overrides[get_db]=lambda:db
    with TestClient(app) as client:
        assert client.post('/recommendations/compare',json={"game_ids":[1,2]}).status_code == 401
        app.dependency_overrides[get_current_user]=lambda:other
        for ids in ([games[2].id,games[2].id],[0,1],[1],[1,2,3],[True,2],["1",2]):
            assert client.post('/recommendations/compare',json={"game_ids":ids}).status_code == 422
        assert client.post('/recommendations/compare',json={"game_ids":[games[2].id,999999]}).status_code == 404
        result=client.post('/recommendations/compare',json={"game_ids":[games[2].id,games[3].id]})
        assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
        assert result.json()['preferred_game_id'] is None


def test_comparison_blocks_developer_role(history):
    from app.models import UserRole
    db,_,other,games,*_ = history
    other.role=UserRole.DEVELOPER
    with pytest.raises(HTTPException) as error:
        compare_games(db,other,[games[2].id,games[3].id])
    assert error.value.status_code == 403


def test_prelaunch_learns_own_reviews_but_keeps_quality_unknown(history):
    from app.services.prelaunch_score_service import build_preview
    db,user,_,games,*_ = history
    set_library(db,user,{"status":"ok","items":[]})
    review=add_review(db,user,games[0],True)
    announced={"appid":999,"name":"Anuncio", "genres":["Acción"],"tags":["Tactical Shooter"],
               "release_label":"Por anunciar","cached_at":1700000000}
    first=build_preview(announced,scores.score_context(db,user))
    review.is_recommended=False;db.commit()
    second=build_preview(announced,scores.score_context(db,user))
    assert first.score.affinity > second.score.affinity
    assert any("no recomendaste en tu reseña de GameTrack" in point.text for point in second.cautions)
    assert second.score.preliminary and second.score.metascore is None


def test_home_reports_personal_signals_without_recomputing_profile(history,monkeypatch):
    db,_,other,games,*_ = history
    add_review(db,other,games[0],True)
    original=scores.score_context
    calls=[]
    def counted(*args,**kwargs):
        calls.append(True)
        return original(*args,**kwargs)
    monkeypatch.setattr(scores,"score_context",counted)
    app=FastAPI();app.include_router(router)
    app.dependency_overrides[get_db]=lambda:db
    app.dependency_overrides[get_current_user]=lambda:other
    with TestClient(app) as client:
        response=client.get('/recommendations?strategy=auto')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['history_size'] == 1 and calls == [True]
    assert games[0].id not in {item['game']['id'] for item in response.json()['items']}

