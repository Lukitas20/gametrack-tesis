from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class ReviewCreate(BaseModel):
    game_id: int
    rating: float = Field(..., ge=1.0, le=10.0)
    title: Optional[str] = None
    body: Optional[str] = None
    is_recommended: Optional[bool] = None
    playtime_at_review: Optional[int] = None


class ReviewResponse(BaseModel):
    id: int
    user_id: int
    game_id: int
    rating: float
    title: Optional[str]
    body: Optional[str]
    is_recommended: Optional[bool]
    playtime_at_review: Optional[int]
    sentiment_score: Optional[float]
    created_at: datetime

    class Config:
        from_attributes = True