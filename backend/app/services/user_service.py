"""Lógica de negocio de usuarios."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models import Genre, User, UserPreference
from app.schemas.user import UserCreate


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def create_user(db: Session, data: UserCreate) -> User:
    user = User(
        username=data.username,
        email=data.email,
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
        role=data.role,
        studio=data.studio,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = get_user_by_username(db, username)
    if not user or not user.hashed_password or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def set_preferences(db: Session, user: User, genre_ids: list[int]) -> User:
    """Reemplaza las preferencias de género del usuario por las indicadas."""
    genres = db.scalars(select(Genre).where(Genre.id.in_(genre_ids))).all()
    user.preferences = [UserPreference(genre=genre, weight=1.0) for genre in genres]
    db.commit()
    db.refresh(user)
    return user


def update_profile(
    db: Session,
    user: User,
    full_name: str | None,
    email: str | None,
    avatar_url: str | None,
) -> User:
    """Actualiza sólo los campos que vinieron en el pedido (``None`` = sin tocar)."""
    if full_name is not None:
        user.full_name = full_name
    if email is not None:
        existing = get_user_by_email(db, email)
        if existing and existing.id != user.id:
            raise ValueError("Ese email ya está en uso por otra cuenta")
        user.email = email
    if avatar_url is not None:
        user.avatar_url = avatar_url
    db.commit()
    db.refresh(user)
    return user
