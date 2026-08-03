"""Verifica que Alembic y los modelos describan el mismo esquema.

Una migración que no coincide con el ORM es peor que no tener migraciones: da
una falsa sensación de control y falla recién en el despliegue. Este test crea
una base aplicando las migraciones y otra con ``create_all``, y compara las dos
estructuras resultantes.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.db.base import Base

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture
def schemas(tmp_path):
    """Devuelve (esquema por migraciones, esquema por create_all)."""
    migrated_path = tmp_path / "migrated.db"
    created_path = tmp_path / "created.db"
    migrated_url = f"sqlite:///{migrated_path}"

    # env.py respeta la URL que se le fija acá y sólo cae en la de la
    # aplicación cuando no hay ninguna, así que la base de desarrollo no se
    # toca.
    command.upgrade(_alembic_config(migrated_url), "head")

    created_engine = create_engine(f"sqlite:///{created_path}")
    Base.metadata.create_all(created_engine)

    return inspect(create_engine(migrated_url)), inspect(created_engine)


def test_las_migraciones_crean_las_mismas_tablas(schemas) -> None:
    migrated, created = schemas
    expected = set(created.get_table_names())
    actual = set(migrated.get_table_names()) - {"alembic_version"}
    assert actual == expected


def test_las_migraciones_crean_las_mismas_columnas(schemas) -> None:
    migrated, created = schemas
    for table in sorted(created.get_table_names()):
        expected = {
            (column["name"], str(column["type"]))
            for column in created.get_columns(table)
        }
        actual = {
            (column["name"], str(column["type"]))
            for column in migrated.get_columns(table)
        }
        assert actual == expected, f"difieren las columnas de {table}"


def test_las_migraciones_crean_los_mismos_indices(schemas) -> None:
    migrated, created = schemas
    for table in sorted(created.get_table_names()):
        expected = {
            (index["name"], tuple(index["column_names"]))
            for index in created.get_indexes(table)
        }
        actual = {
            (index["name"], tuple(index["column_names"]))
            for index in migrated.get_indexes(table)
        }
        assert actual == expected, f"difieren los índices de {table}"
