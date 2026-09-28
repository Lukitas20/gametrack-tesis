"""Invitaciones compartibles. Abrir un enlace nunca acepta una amistad."""
import secrets
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.v1.endpoints.steam_auth import base_url
from app.db.database import get_db
from app.models import FriendInvite, Friendship, User, UserRole
from app.services import friendship_service

router = APIRouter(prefix="/friends", tags=["amigos"])


def _no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


def _inviter(db, token):
    invite = db.get(FriendInvite, token) if 32 <= len(token) <= 64 else None
    user = db.get(User, invite.user_id) if invite and invite.expires_at > int(time.time()) else None
    if not user or not user.is_active or user.role != UserRole.PLAYER:
        raise HTTPException(404, "Esta invitación venció o ya no está disponible")
    return user


@router.post("/invites")
def create_invite(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    friendship_service.require_player(user)
    invite = db.scalar(select(FriendInvite).where(FriendInvite.user_id == user.id))
    now = int(time.time())
    if invite and invite.expires_at <= now:
        db.delete(invite); db.flush(); invite = None
    if not invite:
        invite = FriendInvite(token=secrets.token_urlsafe(32), user_id=user.id, expires_at=now + 30*24*3600)
        db.add(invite)
        try:
            db.commit(); db.refresh(invite)
        except IntegrityError:
            db.rollback()
            invite = db.scalar(select(FriendInvite).where(FriendInvite.user_id == user.id))
            if not invite or invite.expires_at <= now:
                raise HTTPException(409, "La invitación cambió; intentá de nuevo") from None
    _no_cache(response)
    return {"url": base_url() + "/#/invitacion/" + invite.token, "expires_at": invite.expires_at,
            "public": urlsplit(base_url()).hostname not in {"localhost", "127.0.0.1", "::1"}}


@router.get("/invite/{token}")
def inspect_invite(token: str, response: Response, db: Session = Depends(get_db)):
    user = _inviter(db, token)
    _no_cache(response)
    return {"username": user.username, "name": user.full_name or user.steam_username or user.username}


@router.post("/invite/{token}/join")
def join_invite(token: str, response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    friendship_service.require_player(user)
    inviter = _inviter(db, token)
    if inviter.id == user.id:
        raise HTTPException(400, "Este es tu propio enlace de invitación")
    low, high = sorted((inviter.id, user.id))
    existing = db.scalar(select(Friendship).where(Friendship.user_low_id == low, Friendship.user_high_id == high))
    _no_cache(response)
    if existing:
        if existing.status == "pending" and existing.requester_id == inviter.id:
            friendship_service.accept_request(db, user, existing.id)
            return {"state": "accepted"}
        return {"state": existing.status}
    friendship_service.request_friendship(db, user, inviter.username)
    return {"state": "pending"}
