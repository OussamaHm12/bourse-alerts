"""Alembic environment.

Two things here are deliberate.

**The URL comes from the application, never from alembic.ini.** `settings.database_url`
is the one place that knows where the database is, and it is a *relative* SQLite path
(`sqlite:///data/market.db`) that resolves to the volume inside the container and to a
local file on a laptop. A URL duplicated into alembic.ini would drift from it, and the
failure mode is migrating the wrong database while reporting success — the same trap
`railway run` sets (AUDIT_TECHNIQUE.md §10).

**`render_as_batch` is on for SQLite.** SQLite cannot ALTER or DROP a column in place;
Alembic's batch mode emulates it by recreating the table and copying the data. Without
it, the first migration that touches an existing column fails on the only backend
production actually runs on.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from moroccan_stock_intelligence.config import settings
from moroccan_stock_intelligence.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Escaped: ConfigParser reads % as interpolation, and a Postgres password may
# legitimately contain one.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

target_metadata = Base.metadata


def _is_sqlite() -> bool:
    return settings.database_url.startswith("sqlite")


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(),
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
            render_as_batch=_is_sqlite(),
            # Catches a column whose type drifted, which otherwise only surfaces as
            # a runtime error once the other backend is in play.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
