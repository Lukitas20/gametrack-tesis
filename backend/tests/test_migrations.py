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
from sqlalchemy import create_engine, inspect, text

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


def test_identidad_autor_steam_amplia_sin_modificar_resenas(tmp_path) -> None:
    """Agregar una identidad opcional no inventa autores ni altera el texto."""
    path = tmp_path / "steam-authors.db"
    config = _alembic_config(f"sqlite:///{path}")
    command.upgrade(config, "c6d910a74e32")
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO games (id, name, slug, platforms, avg_rating, ratings_count, reviews_count) "
                                "VALUES (1, 'Fixture', 'fixture', '[]', 0, 0, 1)"))
        connection.execute(text("INSERT INTO reviews (id, game_id, content, source, author_name, steam_review_id, language, helpful_count, is_analyzed) "
                                "VALUES (1, 1, 'Texto original conservado', 'steam', 'Nombre observado', 'fixture-review', 'es', 0, 0)"))
    command.upgrade(config, "head")
    column = next(item for item in inspect(engine).get_columns("reviews") if item["name"] == "steam_author_id")
    assert column["nullable"] is True and str(column["type"]) == "VARCHAR(32)"
    with engine.connect() as connection:
        row = connection.execute(text("SELECT content, author_name, steam_review_id, steam_author_id FROM reviews WHERE id=1")).one()
    assert tuple(row) == ("Texto original conservado", "Nombre observado", "fixture-review", None)
    engine.dispose()


def test_experience_migration_preserves_existing_accounts_and_preferences(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "a2e7c91d6b40")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users (id, username, role, studio, is_active) VALUES (1, 'player', 'jugador', NULL, 1), (2, 'indie', 'desarrollador', 'Indie', 1)"))
        connection.execute(text("INSERT INTO genres (id, slug, name) VALUES (1, 'indie', 'Indie')"))
        connection.execute(text("INSERT INTO user_preferences (user_id, genre_id, weight) VALUES (1, 1, 1)"))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT username, developer_enabled, onboarding_step, studio FROM users ORDER BY id")).all()
        assert rows == [("player", 0, None, None), ("indie", 1, None, "Indie")]
        assert connection.scalar(text("SELECT count(*) FROM user_preferences")) == 1
        assert not connection.execute(text("PRAGMA foreign_key_check")).all()
    command.downgrade(config, "a2e7c91d6b40")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM users")) == 2
        assert connection.scalar(text("SELECT count(*) FROM user_preferences")) == 1
    engine.dispose()
