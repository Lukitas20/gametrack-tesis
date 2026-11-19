"""Configuración portable sin leer credenciales ni usar bases reales."""
import pytest

from app.core.config import Settings


@pytest.mark.parametrize("prefix", ["postgres://", "postgresql://", "postgresql+psycopg2://"])
def test_postgres_provider_urls_preserve_encoded_credentials(prefix):
    suffix = "user:test%25%40pass@db:5432/catalog?sslmode=require"
    config = Settings(_env_file=None, DATABASE_URL=prefix + suffix)
    assert config.DATABASE_URL == "postgresql+psycopg2://" + suffix
    assert not config.is_sqlite


def test_local_defaults_remain_embedded():
    config = Settings(_env_file=None, DATABASE_URL="sqlite://")
    assert config.is_sqlite
    assert config.DB_AUTO_CREATE
    assert config.STEAM_CATALOG_WORKER_MODE == "embedded"


def test_typo_in_worker_mode_cannot_silently_stop_sync():
    with pytest.raises(ValueError):
        Settings(_env_file=None, STEAM_CATALOG_WORKER_MODE="externl")
