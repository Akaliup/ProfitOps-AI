from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import models  # noqa: E402,F401  (populates Base.metadata)
from app.config import Settings  # noqa: E402
from app.db import Base, build_engine  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Placeholder written in alembic.ini. It is not an explicit override.
_INI_PLACEHOLDER_URLS = {"sqlite:///./data/profitops.db"}


def resolve_migration_url(migration_config) -> str:
    """Pick the migration database without preferring the runtime default.

    Priority:
    1. Alembic ``-x url=`` or ``-x database_url=``
    2. ``sqlalchemy.url`` set on this Config when it is not the ini placeholder
    3. ``PROFITOPS_DATABASE_URL`` / ``ALEMBIC_DATABASE_URL``
    4. ``Settings().database_url``
    """
    x_args = context.get_x_argument(as_dictionary=True)
    x_url = (x_args.get("url") or x_args.get("database_url") or "").strip()
    if x_url:
        return x_url
    ini_url = (migration_config.get_main_option("sqlalchemy.url") or "").strip()
    if ini_url and ini_url not in _INI_PLACEHOLDER_URLS:
        return ini_url
    env_url = (os.environ.get("PROFITOPS_DATABASE_URL") or os.environ.get("ALEMBIC_DATABASE_URL") or "").strip()
    if env_url:
        return env_url
    return Settings().database_url


database_url = resolve_migration_url(config)

# ``replace("%", "%%")`` protects passwords that contain ConfigParser escapes.
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = build_engine(database_url)
    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
