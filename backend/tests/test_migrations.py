from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

pytest.importorskip("alembic")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def test_alembic_baseline_upgrade_and_downgrade(tmp_path, monkeypatch):
    database = tmp_path / "migration.db"
    monkeypatch.setenv("PROFITOPS_DATABASE_URL", f"sqlite:///{database}")
    config = Config(str(ROOT / "backend" / "alembic.ini"))

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database}")
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert {"tenants", "users", "analyses", "recommendations", "executions", "knowledge_documents", "alembic_version"} <= tables

    command.downgrade(config, "base")
    engine = create_engine(f"sqlite:///{database}")
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert "analyses" not in tables
    assert "tenants" not in tables
