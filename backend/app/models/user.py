from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.sql import func
from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    hashed_password = Column(String(255), nullable=True)

    # Steam integration
    steam_id = Column(String(20), unique=True, nullable=True, index=True)
    steam_username = Column(String(100), nullable=True)
    steam_avatar_url = Column(Text, nullable=True)

    is_active = Column(Boolean, default=True)
    is_developer = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
