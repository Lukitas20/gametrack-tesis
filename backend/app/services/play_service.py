from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select
from app.models import Game, GameList, GameListItem, GameStatus, ListType, PlayFeedback, Rating, SteamProfileCache
from app.schemas.game import GameSummary
from app.schemas.play import BacklogItem, BacklogOut, FeedbackOut, FeedbackSaved, ExperienceRow
from app.services.gametrack_score_service import score_context, score_game
from app.ml.recommender import _recommendable_game_filter


def save_feedback(db,user,game_id,payload):
    if db.get(Game,game_id) is None:raise HTTPException(404,'El juego no existe')
    item=db.scalar(select(PlayFeedback).where(PlayFeedback.user_id==user.id,PlayFeedback.game_id==game_id))
    if item is None:
        item=PlayFeedback(user_id=user.id,game_id=game_id);db.add(item)
    for key,value in payload.model_dump().items():setattr(item,key,value.strip() or None if key=='note' and value else value)
    item.updated_at=datetime.now(timezone.utc)
    learns=payload.played and payload.reason=='experience' and payload.enjoyment in {'liked','disliked'}
    if learns:
        item.taste_weight={'liked':.75,'mixed':0.,'disliked':-.75}[payload.enjoyment]
        item.taste_at=item.updated_at
    db.commit();db.refresh(item)
    return FeedbackSaved(feedback=FeedbackOut.model_validate(item),learning_applied=learns,
        message='Tu devolución ajustará las próximas sugerencias. Conservamos tus valoraciones de estrellas.' if learns else
        'Guardamos el contexto sin cambiar tus gustos. Una interrupción no cuenta como rechazo del juego.')


def feedback_history(db,user):
    rows=db.execute(select(PlayFeedback,Game).join(Game,Game.id==PlayFeedback.game_id)
        .where(PlayFeedback.user_id==user.id).order_by(PlayFeedback.updated_at.desc()).limit(40))
    return [ExperienceRow(game=GameSummary.model_validate(game),feedback=FeedbackOut.model_validate(feedback)) for feedback,game in rows]


def learning_context(db,user,engine=None):
    if not db.scalar(select(PlayFeedback.id).where(PlayFeedback.user_id==user.id,PlayFeedback.taste_at.is_not(None)).limit(1)):
        return None
    return score_context(db,user,engine=engine)


def backlog(db,user,mode='light',limit=12):
    pending=set(db.scalars(select(GameListItem.game_id).join(GameList,GameList.id==GameListItem.list_id)
        .where(GameList.user_id==user.id,GameList.list_type==ListType.BACKLOG)).all())
    cache=db.get(SteamProfileCache,user.id) if user.steam_verified else None
    valid=cache is not None and cache.steam_id==user.steam_id
    library=cache.library or {} if valid else {}
    status=library.get('status','unavailable') if user.steam_verified else 'not_linked'
    owned={}
    if status=='ok':
        owned={item['appid']:item.get('minutes') for item in library.get('items',[])
               if type(item.get('appid')) is int and type(item.get('minutes')) is int and item['minutes']>=0}
    games={};apps=sorted(owned);pids=sorted(pending)
    for ids,column in [(apps,Game.steam_app_id),(pids,Game.id)]:
        for start in range(0,len(ids),500):
            games.update({game.id:game for game in db.scalars(select(Game).where(column.in_(ids[start:start+500]),_recommendable_game_filter())).all()})
    matched={game.steam_app_id for game in games.values() if game.steam_app_id in owned}
    ratings={item.game_id:item for item in db.scalars(select(Rating).where(Rating.user_id==user.id)).all()}
    feedback={item.game_id:item for item in db.scalars(select(PlayFeedback).where(PlayFeedback.user_id==user.id)).all()}
    candidates=[]
    for game in games.values():
        steam=game.steam_app_id in owned;queued=game.id in pending
        minutes=owned.get(game.steam_app_id) if steam else None
        rating,experience=ratings.get(game.id),feedback.get(game.id)
        if rating and (rating.status in {GameStatus.COMPLETED,GameStatus.ABANDONED} or rating.score<=2):continue
        if experience and (experience.replay is False or (experience.taste_weight or 0)<0):continue
        if mode=='unplayed' and (not steam or minutes!=0 or (experience and experience.played)):continue
        if mode=='pending' and not queued:continue
        if mode=='light' and not (queued or (steam and minutes<=120)):continue
        if mode=='light' and not queued and experience and experience.played and (experience.minutes or 0)>120:continue
        candidates.append((game,steam,queued,minutes))
    context=score_context(db,user) if candidates else None
    rows=[]
    for game,steam,queued,minutes in candidates:
        score=score_game(game,context)
        reason=f'Steam registra {minutes} minutos jugados; todavía podés descubrirlo.' if steam else 'Lo guardaste en Pendientes; no verificamos que lo hayas comprado.'
        if queued and steam:reason+=' También está en tu lista de Pendientes.'
        source='steam_pending' if steam and queued else 'steam' if steam else 'pending'
        rows.append(BacklogItem(game=GameSummary.model_validate(game),score=score,source=source,minutes=minutes,steam_app_id=game.steam_app_id,reason=reason))
    rows.sort(key=lambda item:(-(item.score.value if item.score.value is not None else -1),item.minutes if item.minutes is not None else 999999,item.game.id))
    return BacklogOut(items=rows[:limit],eligible_count=len(rows),library_status=status,checked_at=cache.checked_at if valid else None,
        unmapped_games=len(set(owned)-matched),
        note='Ordenamos por tu GameTrackScore entre tus juegos de Steam con pocas horas y tus Pendientes. Excluimos completados, abandonados y rechazos explícitos. Las horas provienen de la última sincronización; no indican progreso de campaña.' if rows else
        'No encontramos juegos elegibles en la biblioteca sincronizada o en Pendientes. Podés sincronizar Steam o guardar títulos en esa lista. No rellenamos con juegos que no sean tuyos o que no hayas guardado.')
