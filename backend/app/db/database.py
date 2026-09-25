"""Motor de base de datos y sesión de SQLAlchemy."""

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

# check_same_thread=False es necesario para que SQLite funcione con el
# thread pool de FastAPI. No aplica a PostgreSQL.
connect_args = {"check_same_thread": False, "timeout": 30} if settings.is_sqlite else {}

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.SQL_ECHO,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


if settings.is_sqlite:
    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record):
        # Lecturas de la app y escritura del worker pueden convivir. En una
        # base :memory: SQLite conserva automáticamente su modo de memoria.
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


class Base(DeclarativeBase):
    """Clase base declarativa para todos los modelos."""


def get_db() -> Generator[Session, None, None]:
    """Dependencia de FastAPI que provee una sesión por request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
