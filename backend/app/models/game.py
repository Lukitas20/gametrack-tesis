from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, ARRAY
from sqlalchemy.sql import func
from app.db.database import Base


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True, index=True)
    steam_app_id = Column(Integer, unique=True, nullable=True, index=True)
    rawg_id = Column(Integer, unique=True, nullable=True, index=True)
    title = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    short_description = Column(String(500), nullable=True)
    cover_image_url = Column(Text, nullable=True)
    header_image_url = Column(Text, nullable=True)
    developer = Column(String(255), nullable=True)
    publisher = Column(String(255), nullable=True)
    release_date = Column(DateTime(timezone=True), nullable=True)
    genres = Column(ARRAY(String), nullable=True)
    tags = Column(ARRAY(String), nullable=True)
    platforms = Column(ARRAY(String), nullable=True)
    metacritic_score = Column(Integer, nullable=True)
    steam_positive_reviews = Column(Integer, default=0)
    steam_negative_reviews = Column(Integer, default=0)
    average_playtime_minutes = Column(Integer, default=0)
    price_usd = Column(Float, nullable=True)
    is_free = Column(Boolean, default=False)
    internal_rating = Column(Float, nullable=True)
    total_reviews = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())