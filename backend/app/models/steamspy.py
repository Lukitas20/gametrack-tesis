"""Cola de ingesta de SteamSpy: qué juegos enriquecer y en qué estado están.

SteamSpy limita a ~1 pedido por segundo el endpoint por juego, así que
enriquecer el catálogo lleva horas y puede cortarse en cualquier momento
(bloqueo, caída de SteamSpy, cierre de la máquina). La cola vive en la base
justamente por eso: el estado sobrevive al proceso, y el worker retoma donde
quedó en lugar de arrancar de cero.

Una fila por AppID, con el crudo de la respuesta guardado además del parseo:
si mañana cambia cómo se normalizan los votos de las etiquetas, se reprocesa
desde ``raw`` sin volver a pedirle nada a SteamSpy.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# Estados posibles de una fila. Deliberadamente NO hay un estado "no
# disponible": un fallo de transporte deja la fila en ``pending`` con
# ``attempts`` incrementado, porque no saber no es lo mismo que saber que no
# está (mismo invariante que ``steam_service.SteamUnavailable``).
SYNC_PENDING = "pending"    # todavía no se enriqueció (o falló y se reintenta)
SYNC_DONE = "done"          # enriquecido con éxito
SYNC_SKIPPED = "skipped"    # SteamSpy respondió que no hay datos: no es un juego


class SteamSpySync(Base):
    """Estado de enriquecimiento (nivel 1) de un AppID contra SteamSpy."""

    __tablename__ = "steamspy_sync"

    # Clave natural: el AppID de Steam. No hay dos filas para el mismo juego.
    steam_app_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False
    )
    name: Mapped[str] = mapped_column(String(200))

    # Dueños estimados (punto medio de la banda de SteamSpy). El worker
    # procesa en orden descendente: enriquecer primero lo más jugado hace que
    # el catálogo útil crezca lo más rápido posible.
    priority: Mapped[int] = mapped_column(BigInteger, default=0, index=True)

    status: Mapped[str] = mapped_column(String(12), default=SYNC_PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    # Respuesta cruda de ``request=appdetails``, tal como llegó.
    raw: Mapped[dict | None] = mapped_column(JSON)

    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<SteamSpySync {self.steam_app_id} {self.status} p={self.priority}>"
