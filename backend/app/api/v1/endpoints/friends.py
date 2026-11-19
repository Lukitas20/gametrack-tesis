"""Amigos para compartir recomendaciones, con aceptación de solicitudes."""

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models import User
from app.schemas.friendship import FriendsOut, FriendshipRequestCreate, FriendshipRequestOut
from app.services import friendship_service

router = APIRouter(prefix="/friends", tags=["amigos"])


@router.get("", response_model=FriendsOut)
def my_friends(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> FriendsOut:
    return friendship_service.list_friends(db, user)


@router.post("/requests", response_model=FriendshipRequestOut, status_code=status.HTTP_201_CREATED)
def request_friend(
    data: FriendshipRequestCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FriendshipRequestOut:
    return friendship_service.request_friendship(db, user, data.username)


@router.post("/requests/{request_id}/accept", response_model=FriendshipRequestOut)
def accept_friend(
    request_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> FriendshipRequestOut:
    return friendship_service.accept_request(db, user, request_id)


@router.delete("/requests/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_request(
    request_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    friendship_service.delete_request(db, user, request_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{friend_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_friend(
    friend_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    friendship_service.remove_friend(db, user, friend_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
