from sqlalchemy.orm import Session
from app.models.user import User
from app.schemas.user import UserCreate
from app.core.security import hash_password, verify_password


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def get_user_by_steam_id(db: Session, steam_id: str) -> User | None:
    return db.query(User).filter(User.steam_id == steam_id).first()


def create_user(db: Session, data: UserCreate) -> User:
    user = User(
        username=data.username,
        email=data.email,
        hashed_password=hash_password(data.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = get_user_by_username(db, username)
    if not user or not user.hashed_password:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def get_or_create_steam_user(db: Session, steam_id: str, steam_username: str, avatar_url: str) -> User:
    user = get_user_by_steam_id(db, steam_id)
    if user:
        user.steam_username = steam_username
        user.steam_avatar_url = avatar_url
        db.commit()
        db.refresh(user)
        return user
    user = User(
        username=f"steam_{steam_id}",
        steam_id=steam_id,
        steam_username=steam_username,
        steam_avatar_url=avatar_url,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
