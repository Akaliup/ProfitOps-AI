from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, MetaData,
    String, Table, create_engine, inspect, select, text,
)
from sqlalchemy.exc import IntegrityError

pytest.importorskip("alembic")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.db import make_session_factory  # noqa: E402
from app.models import Analysis  # noqa: E402

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


def _legacy_schema():
    """The pre-0002 schema: global unique keys capped at 100 characters."""
    metadata = MetaData()
    analyses = Table(
        "analyses", metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(40), nullable=False, server_default=text("'demo'")),
        Column("idempotency_key", String(100), nullable=False, unique=True),
        Column("request", JSON, nullable=False),
        Column("actor", String(80), nullable=False),
        Column("request_id", String(64), nullable=False),
        Column("status", String(30), nullable=False),
        Column("steps", JSON, nullable=False),
        Column("snapshot", JSON, nullable=False),
        Column("result", JSON, nullable=True),
        Column("error", JSON, nullable=True),
        Column("recommendation_id", String(36), nullable=True),
        Column("attempts", Integer, nullable=False, server_default=text("0")),
        Column("max_attempts", Integer, nullable=False, server_default=text("3")),
        Column("next_run_at", DateTime(timezone=True), nullable=True),
        Column("lease_until", DateTime(timezone=True), nullable=True),
        Column("worker_id", String(80), nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    Index("ix_analyses_tenant_id", analyses.c.tenant_id)
    Index("ix_analyses_status_next_run", analyses.c.status, analyses.c.next_run_at)
    recommendations = Table(
        "recommendations", metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(40), nullable=False, server_default=text("'demo'")),
        Column("analysis_id", String(36), ForeignKey("analyses.id"), nullable=False, unique=True),
        Column("status", String(30), nullable=False),
        Column("title", String(200), nullable=False),
        Column("body", JSON, nullable=False),
        Column("action", JSON, nullable=False),
        Column("approved_by", String(80), nullable=True),
        Column("approval_reason", String, nullable=True),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    executions = Table(
        "executions", metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(40), nullable=False, server_default=text("'demo'")),
        Column(
            "recommendation_id", String(36),
            ForeignKey("recommendations.id"), nullable=False, unique=True,
        ),
        Column("idempotency_key", String(100), nullable=False, unique=True),
        Column("status", String(30), nullable=False),
        Column("simulation", Boolean, nullable=False),
        Column("result", JSON, nullable=True),
        Column("error", JSON, nullable=True),
        Column("actor", String(80), nullable=False),
        Column("request_id", String(64), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    Index("ix_executions_tenant_id", executions.c.tenant_id)
    return metadata


def test_alembic_upgrades_legacy_idempotency_schema(tmp_path, monkeypatch):
    database = tmp_path / "legacy-idempotency.db"
    engine = create_engine(f"sqlite:///{database}")
    _legacy_schema().create_all(engine)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO analyses (id, tenant_id, idempotency_key, request, actor, request_id, "
            "status, steps, snapshot, created_at, updated_at) VALUES "
            "('legacy-analysis', 'demo', 'legacy-key', '{}', 'demo-analyst', 'migration', "
            "'completed', '[]', '{}', :now, :now)"
        ), {"now": now})
        connection.execute(text(
            "INSERT INTO recommendations (id, tenant_id, analysis_id, status, title, body, action, "
            "created_at, updated_at) VALUES "
            "('legacy-recommendation', 'demo', 'legacy-analysis', 'executed', 'legacy', "
            "'{}', '{}', :now, :now)"
        ), {"now": now})
        connection.execute(text(
            "INSERT INTO executions (id, tenant_id, recommendation_id, idempotency_key, status, "
            "simulation, actor, request_id, created_at) VALUES "
            "('legacy-execution', 'demo', 'legacy-recommendation', 'legacy-exec-key', "
            "'executed', 1, 'demo-approver', 'migration', :now)"
        ), {"now": now})
    engine.dispose()

    monkeypatch.setenv("PROFITOPS_DATABASE_URL", f"sqlite:///{database}")
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    command.stamp(config, "0001_baseline")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    inspector = inspect(engine)
    analyses_columns = {column["name"]: column for column in inspector.get_columns("analyses")}
    executions_columns = {column["name"]: column for column in inspector.get_columns("executions")}
    assert analyses_columns["idempotency_key"]["type"].length == 200
    assert executions_columns["idempotency_key"]["type"].length == 200

    analyses_unique = {
        tuple(constraint["column_names"]) for constraint in inspector.get_unique_constraints("analyses")
    }
    executions_unique = {
        tuple(constraint["column_names"]) for constraint in inspector.get_unique_constraints("executions")
    }
    assert ("tenant_id", "idempotency_key") in analyses_unique
    assert ("idempotency_key",) not in analyses_unique
    assert ("tenant_id", "idempotency_key") in executions_unique
    assert ("idempotency_key",) not in executions_unique
    assert ("recommendation_id",) in executions_unique

    analyses_indexes = {index["name"] for index in inspector.get_indexes("analyses")}
    executions_indexes = {index["name"] for index in inspector.get_indexes("executions")}
    assert {"ix_analyses_tenant_id", "ix_analyses_status_next_run"} <= analyses_indexes
    assert "ix_executions_tenant_id" in executions_indexes

    factory = make_session_factory(engine)
    with factory() as session:
        assert session.get(Analysis, "legacy-analysis").idempotency_key == "legacy-key"
        assert session.execute(text(
            "SELECT idempotency_key FROM executions WHERE id='legacy-execution'"
        )).scalar_one() == "legacy-exec-key"
    with factory.begin() as session:
        session.add_all([
            Analysis(
                id="same-key-demo", tenant_id="demo", idempotency_key="shared-key",
                request={}, actor="demo-analyst", request_id="migration",
            ),
            Analysis(
                id="same-key-acme", tenant_id="acme", idempotency_key="shared-key",
                request={}, actor="acme-analyst", request_id="migration",
            ),
            Analysis(
                id="long-key", tenant_id="acme",
                idempotency_key="acme:" + "k" * 100,
                request={}, actor="acme-analyst", request_id="migration",
            ),
        ])
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(Analysis(
                id="duplicate-key", tenant_id="acme", idempotency_key="shared-key",
                request={}, actor="acme-analyst", request_id="migration",
            ))
    with factory() as session:
        assert session.scalar(select(Analysis).where(Analysis.id == "long-key")).idempotency_key == "acme:" + "k" * 100
    engine.dispose()
