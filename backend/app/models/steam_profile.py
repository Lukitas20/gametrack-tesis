"""Caché privada por usuario; nunca se convierte el tiempo jugado en ratings."""
from sqlalchemy import ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base


class SteamProfileCache(Base):
    __tablename__ = "steam_profile_cache"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    steam_id: Mapped[str] = mapped_column(String(20))
    checked_at: Mapped[int] = mapped_column(Integer, default=0)
    library: Mapped[dict] = mapped_column(JSON, default=dict)
    friends: Mapped[dict] = mapped_column(JSON, default=dict)


class FriendInvite(Base):
    __tablename__ = "friend_invites"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    expires_at: Mapped[int] = mapped_column(Integer)
