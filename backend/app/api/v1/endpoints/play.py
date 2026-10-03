from fastapi import APIRouter,Depends,Query,Response
from typing import Literal
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.db.database import get_db
from app.models import User,PlayFeedback
from app.schemas.play import FeedbackInput,FeedbackOut,FeedbackSaved,ExperienceRow,BacklogOut
from app.services.friendship_service import require_player
from app.services.play_service import backlog,feedback_history,save_feedback

router=APIRouter(prefix='/play',tags=['experiencias-y-pendientes'])

def player(user:User=Depends(get_current_user)):
    require_player(user);return user

@router.get('/feedback',response_model=list[ExperienceRow])
def history(response:Response,user:User=Depends(player),db:Session=Depends(get_db)):
    response.headers['Cache-Control']='no-store';return feedback_history(db,user)

@router.get('/feedback/{game_id}',response_model=FeedbackOut|None)
def latest(game_id:int,response:Response,user:User=Depends(player),db:Session=Depends(get_db)):
    response.headers['Cache-Control']='no-store'
    return db.scalar(select(PlayFeedback).where(PlayFeedback.user_id==user.id,PlayFeedback.game_id==game_id))

@router.put('/feedback/{game_id}',response_model=FeedbackSaved)
def save(game_id:int,payload:FeedbackInput,response:Response,user:User=Depends(player),db:Session=Depends(get_db)):
    response.headers['Cache-Control']='no-store';return save_feedback(db,user,game_id,payload)

@router.get('/backlog',response_model=BacklogOut)
def get_backlog(response:Response,mode:Literal['light','unplayed','pending']='light',limit:int=Query(12,ge=1,le=24),user:User=Depends(player),db:Session=Depends(get_db)):
    response.headers['Cache-Control']='no-store';return backlog(db,user,mode,limit)
