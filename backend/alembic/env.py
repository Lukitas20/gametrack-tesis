"""Entorno de Alembic.

Dos diferencias respecto de la plantilla por defecto:

* La URL de la base sale de ``app.core.config`` en lugar de ``alembic.ini``,
  así hay una sola fuente de verdad y funciona igual con SQLite o PostgreSQL.
* Se importa ``app.db.base``, que registra **todos** los modelos. Importar
  sólo algunos hace que ``--autogenerate`` interprete las tablas faltantes
  como eliminadas y escriba migraciones que borran datos.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.base import Base  # noqa: F401  (registra todas las tablas)

config = context.config

# La URL sale de la configuración de la aplicación, salvo que quien invoca ya
# haya fijado una explícitamente (los tests apuntan a una base temporal). El
# valor que trae alembic.ini es el placeholder de la plantilla, no una URL
# real, así que no cuenta como "fijada".
_configured_url = config.get_main_option("sqlalchemy.url", "") or ""
if not _configured_url or _configured_url.startswith("driver://"):
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# SQLite no soporta ALTER TABLE para casi nada: en modo batch Alembic recrea
# la tabla y copia los datos. Sin esto, cualquier migración que modifique una
# columna falla con el motor por defecto del prototipo.
RENDER_AS_BATCH = settings.is_sqlite


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=RENDER_AS_BATCH,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=RENDER_AS_BATCH,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
