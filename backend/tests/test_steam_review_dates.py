from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from tests.test_steam import db
from app.models import Game, Review
from app.services import steam_service


@pytest.mark.parametrize('value',[None,True,'1700000000',0,-1,1e30,float('nan'),float('inf'),4102444800])
def test_invalid_date_is_unknown(value):
    assert steam_service._review_publication_date(value) is None


def test_original_date_survives_import_and_repairs_existing_at_cap(db, monkeypatch):
    monkeypatch.setattr(steam_service.settings,'STEAM_REVIEWS_IMPORT_LIMIT',1)
    monkeypatch.setattr(steam_service,'get_player_summaries_batch',lambda _: {})
    raw={'recommendationid':'one','review':'La historia es excelente, muy entretenida.',
         'timestamp_created':1700000000,'voted_up':True}
    monkeypatch.setattr(steam_service,'get_app_reviews',lambda *a,**k:[raw])
    game=Game(slug='dated',name='Dated',steam_app_id=1);db.add(game);db.commit()
    assert steam_service.import_reviews(db,game,1) == 1
    row=db.scalar(select(Review).where(Review.game_id==game.id))
    expected=datetime.fromtimestamp(1700000000,timezone.utc).replace(tzinfo=None)
    assert row.published_at.replace(tzinfo=None) == expected
    assert row.created_at.year != 2023
    row.published_at=None;db.commit()
    assert steam_service.import_reviews(db,game,1) == 0
    db.refresh(row)
    assert row.published_at.replace(tzinfo=None) == expected
    assert db.scalar(select(func.count(Review.id))) == 1
    # Una fecha ya conocida no depende de posteriores respuestas de Steam.
    monkeypatch.setattr(steam_service,'get_app_reviews',lambda *a,**k:pytest.fail('no hace falta volver a consultar'))
    assert steam_service.import_reviews(db,game,1) == 0
