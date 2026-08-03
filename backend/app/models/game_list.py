"""Listas de juegos creadas por los usuarios."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.db.types import enum_column
from app.models.enums import ListType

if TYPE_CHECKING:
    from app.models.game import Game
    from app.models.user import User


class GameList(Base):
    """Colección de juegos de un usuario.

    Cada usuario recibe automáticamente las listas Favoritos, Pendientes y
    Jugando; el resto son de tipo personalizada.
    """

    __tablename__ = "game_lists"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_list_user_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    list_type: Mapped[ListType] = mapped_column(
        enum_column(ListType), default=ListType.CUSTOM, index=True
    )
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    owner: Mapped[User] = relationship(back_populates="lists")
    items: Mapped[list[GameListItem]] = relationship(
        back_populates="game_list",
        cascade="all, delete-orphan",
        order_by="GameListItem.position",
        lazy="selectin",
    )

    @property
    def is_system(self) -> bool:
        return self.list_type is not ListType.CUSTOM

    def __repr__(self) -> str:
        return f"<GameList {self.id} '{self.name}' u={self.user_id}>"


class GameListItem(Base):
    """Entrada de un juego dentro de una lista."""

    __tablename__ = "game_list_items"
    __table_args__ = (
        UniqueConstraint("list_id", "game_id", name="uq_list_item"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(
        ForeignKey("game_lists.id", ondelete="CASCADE"), index=True
    )
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), index=True
    )

    position: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    game_list: Mapped[GameList] = relationship(back_populates="items")
    game: Mapped[Game] = relationship(back_populates="list_items")

    def __repr__(self) -> str:
        return f"<GameListItem list={self.list_id} game={self.game_id}>"
