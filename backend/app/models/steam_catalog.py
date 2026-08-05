"""Estado de la sincronizacion progresiva con el catalogo de Steam."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.game import Game


class SteamCatalogEntry(Base):
    """Vincula un AppID del indice oficial con su ficha local enriquecida."""

    __tablename__ = "steam_catalog_entries"

    steam_app_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), unique=True, index=True
    )
    last_modified: Mapped[int | None] = mapped_column(Integer, index=True)
    metadata_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    game: Mapped[Game] = relationship(back_populates="steam_catalog_entry")


class SteamSyncState(Base):
    """Checkpoint para reanudar una descarga paginada sin repetirla."""

    __tablename__ = "steam_sync_states"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    last_appid: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    if_modified_since: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SteamEnrichmentState(Base):
    """Estado persistente de la carga diferida de una ficha y sus reseñas.

    Vive en una tabla separada para poder distinguir una ficha cuya metadata
    ya está completa de otra cuyas reseñas de Steam todavía no se consultaron.
    """

    __tablename__ = "steam_enrichment_states"

    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", index=True
    )
    reviews_imported: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SteamCatalogTarget(Base):
    """Selección estable y ordenada para una carga local (p. ej. top 5.000).

    Persistir el ranking permite interrumpir el comando y continuarlo otro día
    sin que un cambio en la tienda reemplace juegos a mitad del proceso.
    """

    __tablename__ = "steam_catalog_targets"

    plan_key: Mapped[str] = mapped_column(String(60), primary_key=True)
    steam_app_id: Mapped[int] = mapped_column(
        ForeignKey("steam_catalog_entries.steam_app_id", ondelete="CASCADE"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(Integer, index=True)
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
