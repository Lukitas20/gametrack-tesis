"""Configuración central de GameTrack.

Todos los valores tienen un default utilizable, de modo que el prototipo
arranca sin necesidad de crear un archivo .env.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR.parent / "frontend"


class Settings(BaseSettings):
    PROJECT_NAME: str = "GameTrack API"
    VERSION: str = "0.2.0"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"

    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'gametrack.db'}"
    SQL_ECHO: bool = False
    DB_AUTO_CREATE: bool = True
    DB_POOL_SIZE: int = Field(default=5, ge=1, le=100)
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0, le=100)
    DB_POOL_TIMEOUT: int = Field(default=30, ge=1, le=300)

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg2://" + value[len(prefix):]
        return value

    SECRET_KEY: str = "gametrack-dev-secret-cambiar-en-produccion-32chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    RAWG_API_KEY: str = ""
    RAWG_BASE_URL: str = "https://api.rawg.io/api"

    STEAM_API_KEY: str = ""
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    STEAM_API_BASE: str = "https://api.steampowered.com"
    STEAM_STORE_BASE: str = "https://store.steampowered.com/api"
    STEAM_REVIEWS_BASE: str = "https://store.steampowered.com/appreviews"
    STEAM_REVIEWS_IMPORT_LIMIT: int = 100
    STEAM_SYNC_TTL_MINUTES: int = 360

    STEAM_CATALOG_WORKER_ENABLED: bool = True
    STEAM_CATALOG_WORKER_MODE: Literal["embedded", "external", "scheduled"] = "embedded"
    STEAM_CATALOG_REQUESTED_ONLY: bool = False
    STEAM_CATALOG_PAGE_SIZE: int = Field(default=10000, ge=1, le=50000)
    STEAM_CATALOG_INTERVAL_MINUTES: int = Field(default=60, ge=1)
    STEAM_CATALOG_DETAIL_BATCH_SIZE: int = Field(default=5, ge=1, le=100)
    STEAM_CATALOG_REQUEST_DELAY_SECONDS: float = Field(default=2.0, ge=1, le=300)

    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ]

    REC_CONTENT_WEIGHT: float = 0.4
    REC_COLLAB_WEIGHT: float = 0.6
    REC_COLD_START_THRESHOLD: int = 3
    REC_DEFAULT_LIMIT: int = 10
    LOCAL_MODEL_PATH: str = str(DATA_DIR / "generated" / "local_model.npz")
    REVIEW_MODEL_PATH: str = str(BASE_DIR / "models" / "review_model.npz")
    STEAM_AFFINITY_MODEL_PATH: str = str(BASE_DIR / "models" / "steam_affinity.json")

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
