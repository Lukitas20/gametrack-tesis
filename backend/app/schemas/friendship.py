"""Identidad pública mínima para solicitudes y recomendaciones entre amigos."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FriendUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str | None = None
    avatar_url: str | None = None


class FriendshipRequestCreate(BaseModel):
    username: str = Field(min_length=1, max_length=50)

    @field_validator("username", mode="before")
    @classmethod
    def trim_username(cls, value):
        return value.strip() if isinstance(value, str) else value


class FriendshipRequestOut(BaseModel):
    id: int
    user: FriendUserOut


class FriendsOut(BaseModel):
    friends: list[FriendUserOut] = Field(default_factory=list)
    incoming: list[FriendshipRequestOut] = Field(default_factory=list)
    outgoing: list[FriendshipRequestOut] = Field(default_factory=list)
