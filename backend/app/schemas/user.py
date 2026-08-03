"""Schemas Pydantic de usuarios y autenticación."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import UserRole


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: EmailStr | None = None
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None
    role: UserRole = UserRole.PLAYER
    studio: str | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None
    full_name: str | None
    avatar_url: str | None
    role: UserRole
    studio: str | None
    created_at: datetime

    # Cuenta de Steam vinculada, si la hay.
    steam_id: str | None = None
    steam_username: str | None = None
    steam_avatar_url: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class SteamLinkRequest(BaseModel):
    """SteamID64: 17 dígitos, el identificador que usa la Web API."""

    steam_id: str = Field(min_length=17, max_length=20, pattern=r"^\d+$")


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class GenrePreference(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    genre_id: int
    weight: float


class PreferencesUpdate(BaseModel):
    """Géneros elegidos en el onboarding, usados para el arranque en frío."""

    genre_ids: list[int] = Field(min_length=1)
