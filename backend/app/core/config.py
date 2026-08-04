"""Configuración central de GameTrack.

Todos los valores tienen un default utilizable, de modo que el prototipo
arranca sin necesidad de crear un archivo .env.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/  -> raíz del proyecto Python (config.py está en backend/app/core/)
BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
# El frontend es estático y lo sirve la misma aplicación FastAPI.
FRONTEND_DIR = BASE_DIR.parent / "frontend"


class Settings(BaseSettings):
    PROJECT_NAME: str = "GameTrack API"
    VERSION: str = "0.2.0"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"

    # --- Base de datos -----------------------------------------------------
    # SQLite por defecto: el prototipo no requiere infraestructura externa.
    # Para PostgreSQL basta con exportar:
    #   DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/gametrack
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'gametrack.db'}"
    SQL_ECHO: bool = False

    # --- Seguridad ---------------------------------------------------------
    SECRET_KEY: str = "gametrack-dev-secret-cambiar-en-produccion-32chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    # --- Integraciones externas -------------------------------------------
    # Opcional: si está presente, el seed puede importar juegos reales desde RAWG.
    RAWG_API_KEY: str = ""
    RAWG_BASE_URL: str = "https://api.rawg.io/api"

    # Steam. La ficha pública de un juego no necesita clave; sí la necesita
    # consultar la biblioteca de un usuario (GetOwnedGames).
    STEAM_API_KEY: str = ""
    STEAM_API_BASE: str = "https://api.steampowered.com"
    STEAM_STORE_BASE: str = "https://store.steampowered.com/api"
    # Reseñas públicas de un juego: no está bajo /api, es un endpoint aparte.
    STEAM_REVIEWS_BASE: str = "https://store.steampowered.com/appreviews"
    # Cuántas reseñas reales se traen al importar un juego nuevo.
    STEAM_REVIEWS_IMPORT_LIMIT: int = 100

    # --- Frontend / CORS ---------------------------------------------------
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ]

    # --- Motor de recomendación -------------------------------------------
    # Pesos del enfoque híbrido (se normalizan si no suman 1).
    REC_CONTENT_WEIGHT: float = 0.4
    REC_COLLAB_WEIGHT: float = 0.6
    # Cantidad mínima de ratings de un usuario para salir del cold start.
    REC_COLD_START_THRESHOLD: int = 3
    REC_DEFAULT_LIMIT: int = 10

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
