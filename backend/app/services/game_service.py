from sqlalchemy.orm import Session
from sqlalchemy import or_
from app.models.game import Game
from app.schemas.game import GameCreate
from typing import Optional


def get_game(db: Session, game_id: int) -> Optional[Game]:
    return db.query(Game).filter(Game.id == game_id).first()


def get_game_by_steam_id(db: Session, steam_app_id: int) -> Optional[Game]:
    return db.query(Game).filter(Game.steam_app_id == steam_app_id).first()


def get_games(db: Session, page: int = 1, page_size: int = 20, search: Optional[str] = None, genre: Optional[str] = None) -> tuple:
    query = db.query(Game)

    if search:
        query = query.filter(Game.title.ilike(f"%{search}%"))

    if genre:
        query = query.filter(Game.genres.any(genre))

    total = query.count()
    results = query.offset((page - 1) * page_size).limit(page_size).all()
    return total, results


def create_game(db: Session, data: GameCreate) -> Game:
    game = Game(**data.model_dump())
    db.add(game)
    db.commit()
    db.refresh(game)
    return game


def update_game_rating(db: Session, game_id: int) -> Optional[Game]:
    from app.models.review import Review
    from sqlalchemy import func

    game = get_game(db, game_id)
    if not game:
        return None

    result = db.query(func.avg(Review.rating), func.count(Review.id)).filter(Review.game_id == game_id).first()
    avg_rating, total = result

    game.internal_rating = round(float(avg_rating), 2) if avg_rating else None
    game.total_reviews = total or 0
    db.commit()
    db.refresh(game)
    return game