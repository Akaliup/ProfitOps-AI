from __future__ import annotations

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

import os

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Prioritize explicit URL from Alembic -x, process environment, or programmatic config
x_args = context.get_x_argument(as_dictionary=True)
explicit_url = (
    x_args.get("url")
    or x_args.get("database_url")
    or os.environ.get("PROFITOPS_DATABASE_URL")
    or os.environ.get("ALEMBIC_DATABASE_URL")
)
ini_url = config.get_main_option("sqlalchemy.url")
if explicit_url:
    database_url = explicit_url
elif ini_url and ini_url != "sqlite:///./data/profitops.db":
    database_url = ini_url
else:
    database_url = Settings().database_url

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
