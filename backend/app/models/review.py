from sqlalchemy import Column, Integer, Float, Boolean, DateTime, Text, ForeignKey, String, UniqueConstraint
from sqlalchemy.sql import func
from app.db.database import Base


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("user_id", "game_id", name="uq_user_game_review"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    game_id = Column(Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True)
    rating = Column(Float, nullable=False)
    title = Column(String(255), nullable=True)
    body = Column(Text, nullable=True)
    is_recommended = Column(Boolean, nullable=True)
    playtime_at_review = Column(Integer, nullable=True)
    sentiment_score = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class UserGameInteraction(Base):
    __tablename__ = "user_game_interactions"
    __table_args__ = (UniqueConstraint("user_id", "game_id", name="uq_user_game_interaction"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    game_id = Column(Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True)
    playtime_minutes = Column(Integer, default=0)
    is_wishlisted = Column(Boolean, default=False)
    is_owned = Column(Boolean, default=False)
    last_played_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())