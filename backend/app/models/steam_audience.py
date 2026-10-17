"""Caché interna de observaciones públicas; nunca se expone por autor."""
from sqlalchemy import JSON, Float, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base


class SteamAudienceHistory(Base):
    __tablename__ = "steam_audience_histories"
    steam_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    checked_at: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20))
    # Sólo appid + voto explícito; no texto, alias, horas ni biblioteca privada.
    payload: Mapped[dict] = mapped_column(JSON)


class AudienceCollectionState(Base):
    __tablename__ = "audience_collection_state"
    key: Mapped[str] = mapped_column(String(150), primary_key=True)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    token: Mapped[str] = mapped_column(String(40), default="")
    identity_checks: Mapped[dict] = mapped_column(JSON, default=dict)
