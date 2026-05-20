from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class GameBase(BaseModel):
    title: str
    description: Optional[str] = None
    short_description: Optional[str] = None
    developer: Optional[str] = None
    publisher: Optional[str] = None
    genres: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    platforms: Optional[List[str]] = None
    price_usd: Optional[float] = None
    is_free: bool = False
    cover_image_url: Optional[str] = None
    header_image_url: Optional[str] = None
    metacritic_score: Optional[int] = None


class GameCreate(GameBase):
    steam_app_id: Optional[int] = None
    rawg_id: Optional[int] = None


class GameResponse(GameBase):
    id: int
    steam_app_id: Optional[int]
    rawg_id: Optional[int]
    internal_rating: Optional[float]
    total_reviews: int
    steam_positive_reviews: int
    steam_negative_reviews: int
    created_at: datetime

    class Config:
        from_attributes = True


class GameListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[GameResponse]