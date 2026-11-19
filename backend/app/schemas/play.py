from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.game import GameSummary
from app.schemas.gametrack_score import GameTrackScore


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    played: bool = True
    enjoyment: Literal['liked','disliked','mixed','not_sure']
    reason: Literal['experience','technical','social','time','other'] = 'experience'
    replay: bool | None = None
    minutes: int | None = Field(default=None,ge=0,le=10080)
    note: str | None = Field(default=None,max_length=500)

    @model_validator(mode='after')
    def no_unplayed_opinion(self):
        if not self.played and (self.enjoyment != 'not_sure' or (self.minutes or 0)>0):
            raise ValueError('Si no llegaste a jugar, no corresponde valorar la experiencia ni registrar minutos.')
        return self


class FeedbackOut(FeedbackInput):
    model_config = ConfigDict(from_attributes=True)
    game_id: int
    updated_at: datetime


class FeedbackSaved(BaseModel):
    feedback: FeedbackOut
    learning_applied: bool
    message: str


class ExperienceRow(BaseModel):
    game: GameSummary
    feedback: FeedbackOut


class BacklogItem(BaseModel):
    game: GameSummary
    score: GameTrackScore
    source: Literal['steam','pending','steam_pending']
    minutes: int | None
    steam_app_id: int | None
    reason: str


class BacklogOut(BaseModel):
    items: list[BacklogItem]
    library_status: str
    checked_at: int | None
    unmapped_games: int
    eligible_count: int
    note: str
