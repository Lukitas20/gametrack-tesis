"""Schemas Pydantic de usuarios y autenticación."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import UserRole
from app.schemas.game import GenreOut


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: EmailStr | None = None
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None
    role: UserRole = UserRole.PLAYER
    studio: str | None = None

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("La contraseña supera el máximo de 72 bytes; probá una frase más corta")
        return value

    @field_validator("username")
    @classmethod
    def trimmed_username(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("El usuario debe tener al menos 3 caracteres")
        return value


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
    steam_verified: bool = False

    # Géneros elegidos en el onboarding (ver User.genres).
    genres: list[GenreOut] = []


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


class ProfileUpdate(BaseModel):
    """Datos editables desde la pantalla de perfil.

    No incluye ``username`` (es el identificador de login, cambiarlo es un
    problema aparte) ni contraseña (no hay flujo de verificación todavía).
    """

    full_name: str | None = Field(default=None, max_length=150)
    email: EmailStr | None = None
    avatar_url: str | None = Field(default=None, max_length=500)
