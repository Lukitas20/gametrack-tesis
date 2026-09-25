"""Solicitudes de amistad y autorización de participantes del recomendador."""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import case, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.friendship import Friendship
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.friendship import FriendsOut, FriendshipRequestOut, FriendUserOut

MAX_GROUP_FRIENDS = 4


def require_player(user: User) -> User:
    if not user.is_active or user.role != UserRole.PLAYER:
        raise HTTPException(status_code=403, detail="Las amistades son para cuentas de jugadores activas")
    return user


def _pair(user_id: int, other_id: int) -> tuple[int, int]:
    return min(user_id, other_id), max(user_id, other_id)


def _participant(user_id: int):
    return or_(Friendship.user_low_id == user_id, Friendship.user_high_id == user_id)


def _other_id(user_id: int):
    return case(
        (Friendship.user_low_id == user_id, Friendship.user_high_id),
        else_=Friendship.user_low_id,
    )


def _request_out(friendship: Friendship, other: User) -> FriendshipRequestOut:
    return FriendshipRequestOut(id=friendship.id, user=FriendUserOut.model_validate(other))


def list_friends(db: Session, user: User) -> FriendsOut:
    require_player(user)
    rows = db.execute(
        select(Friendship, User)
        .join(User, User.id == _other_id(user.id))
        .where(_participant(user.id), User.is_active.is_(True), User.role == UserRole.PLAYER)
        .order_by(User.username, User.id)
    )
    result = FriendsOut()
    for friendship, other in rows:
        if friendship.status == "accepted":
            result.friends.append(FriendUserOut.model_validate(other))
        elif friendship.requester_id == user.id:
            result.outgoing.append(_request_out(friendship, other))
        else:
            result.incoming.append(_request_out(friendship, other))
    return result


def request_friendship(db: Session, user: User, username: str) -> FriendshipRequestOut:
    require_player(user)
    other = db.scalar(
        select(User).where(
            User.username == username.strip(), User.is_active.is_(True), User.role == UserRole.PLAYER
        )
    )
    if other is None:
        raise HTTPException(status_code=404, detail="No encontramos ese jugador")
    if other.id == user.id:
        raise HTTPException(status_code=400, detail="No podés enviarte una solicitud")
    low, high = _pair(user.id, other.id)
    existing = db.scalar(
        select(Friendship).where(Friendship.user_low_id == low, Friendship.user_high_id == high)
    )
    if existing is not None:
        detail = (
            "Ya son amigos" if existing.status == "accepted" else
            "Ya hay una solicitud pendiente entre ustedes; quien la recibió debe aceptarla"
        )
        raise HTTPException(status_code=409, detail=detail)
    friendship = Friendship(user_low_id=low, user_high_id=high, requester_id=user.id)
    db.add(friendship)
    try:
        db.commit()
    except IntegrityError as error:
        # La restricción única también protege frente a solicitudes simultáneas.
        db.rollback()
        raise HTTPException(status_code=409, detail="Ya existe una solicitud entre ustedes") from error
    db.refresh(friendship)
    return _request_out(friendship, other)


def _pending_request(db: Session, user: User, request_id: int) -> Friendship:
    require_player(user)
    friendship = db.scalar(
        select(Friendship).where(
            Friendship.id == request_id, _participant(user.id), Friendship.status == "pending"
        )
    )
    if friendship is None:
        raise HTTPException(status_code=404, detail="La solicitud no existe")
    return friendship


def accept_request(db: Session, user: User, request_id: int) -> FriendshipRequestOut:
    friendship = _pending_request(db, user, request_id)
    if friendship.requester_id == user.id:
        raise HTTPException(status_code=403, detail="Solo quien recibió la solicitud puede aceptarla")
    other = db.get(User, friendship.requester_id)
    if other is None or not other.is_active or other.role != UserRole.PLAYER:
        raise HTTPException(status_code=404, detail="El jugador ya no está disponible")
    changed = db.execute(
        update(Friendship)
        .where(
            Friendship.id == request_id, Friendship.status == "pending",
            _participant(user.id), Friendship.requester_id != user.id,
        )
        .values(status="accepted", accepted_at=datetime.now(timezone.utc))
    )
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="La solicitud ya cambió; actualizá tus amigos")
    db.commit()
    return _request_out(friendship, other)


def delete_request(db: Session, user: User, request_id: int) -> None:
    require_player(user)
    removed = db.execute(
        delete(Friendship).where(
            Friendship.id == request_id, _participant(user.id), Friendship.status == "pending"
        )
    )
    if removed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=404, detail="La solicitud no existe")
    db.commit()


def remove_friend(db: Session, user: User, friend_id: int) -> None:
    require_player(user)
    low, high = _pair(user.id, friend_id)
    removed = db.execute(
        delete(Friendship).where(
            Friendship.user_low_id == low, Friendship.user_high_id == high,
            Friendship.status == "accepted",
        )
    )
    if removed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=404, detail="La amistad no existe")
    db.commit()


def resolve_group_members(db: Session, user: User, friend_ids: list[int]) -> list[User]:
    """Devuelve al anfitrión y amigos aceptados, nunca perfiles arbitrarios.

    Rechaza el grupo completo si algún ID no está autorizado. No devuelve
    una lista parcial que pueda ocultar la exclusión de un participante.
    """
    require_player(user)
    if any(type(friend_id) is not int or friend_id <= 0 for friend_id in friend_ids):
        raise HTTPException(status_code=422, detail="Los amigos deben identificarse con IDs válidos")
    unique_ids = list(dict.fromkeys(friend_ids))
    if len(unique_ids) > MAX_GROUP_FRIENDS:
        raise HTTPException(status_code=422, detail="Podés incluir hasta 4 amigos por grupo")
    if user.id in unique_ids:
        raise HTTPException(status_code=400, detail="Ya estás incluido en el grupo")
    if not unique_ids:
        return [user]
    friends = db.scalars(
        select(User)
        .join(Friendship, User.id == _other_id(user.id))
        .where(
            _participant(user.id), Friendship.status == "accepted", User.id.in_(unique_ids),
            User.is_active.is_(True), User.role == UserRole.PLAYER,
        )
    ).all()
    by_id = {friend.id: friend for friend in friends}
    if len(by_id) != len(unique_ids):
        raise HTTPException(
            status_code=403, detail="Solo podés incluir amigos que hayan aceptado tu solicitud y sigan activos"
        )
    return [user, *(by_id[friend_id] for friend_id in unique_ids)]
