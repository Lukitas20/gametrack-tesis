"""Punto único de acceso al metadata completo.

Importar ``app.db.base`` garantiza que todos los modelos estén registrados
antes de llamar a ``init_db``. Usarlo desde scripts y tests en lugar de
importar ``Base`` directamente desde ``app.db.database``.
"""

from app.db.database import Base, SessionLocal, engine, get_db
from app.models import *  # noqa: F401,F403  (registra todas las tablas)

__all__ = ["Base", "SessionLocal", "engine", "get_db", "init_db", "drop_db"]


def init_db() -> None:
    """Crea todas las tablas que todavía no existan."""
    Base.metadata.create_all(bind=engine)


def drop_db() -> None:
    """Elimina todas las tablas. Solo para desarrollo y tests."""
    Base.metadata.drop_all(bind=engine)
