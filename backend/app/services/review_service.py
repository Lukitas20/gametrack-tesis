from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.review import Review
from app.schemas.review import ReviewCreate
from typing import Optional, List


def get_review(db: Session, review_id: int) -> Optional[Review]:
    return db.query(Review).filter(Review.id == review_id).first()


def get_reviews_by_game(db: Session, game_id: int, page: int = 1, page_size: int = 20) -> tuple:
    query = db.query(Review).filter(Review.game_id == game_id)
    total = query.count()
    results = query.order_by(Review.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return total, results


def get_reviews_by_user(db: Session, user_id: int, page: int = 1, page_size: int = 20) -> tuple:
    query = db.query(Review).filter(Review.user_id == user_id)
    total = query.count()
    results = query.order_by(Review.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return total, results


def get_user_review_for_game(db: Session, user_id: int, game_id: int) -> Optional[Review]:
    return db.query(Review).filter(Review.user_id == user_id, Review.game_id == game_id).first()


def create_review(db: Session, user_id: int, data: ReviewCreate) -> Review:
    review = Review(user_id=user_id, **data.model_dump())
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def update_review(db: Session, review: Review, data: dict) -> Review:
    for key, value in data.items():
        setattr(review, key, value)
    db.commit()
    db.refresh(review)
    return review


def delete_review(db: Session, review: Review) -> None:
    db.delete(review)
    db.commit()