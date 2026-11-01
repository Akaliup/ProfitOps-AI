"""PostgreSQL migration tests against the real pre-tenancy schema.

They need ``PROFITOPS_TEST_POSTGRES_URL`` and run in a disposable schema, so a
developer database is never modified. The fixture comes from
``legacy_schema``: no ``tenant_id`` or durable-job columns exist before the
migration.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

pytest.importorskip("alembic")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from legacy_schema import (  # noqa: E402
    LEGACY_ACTIVE_SHA_INDEX,
    legacy_metadata,
    seed_legacy_rows,
)

ROOT = Path(__file__).resolve().parents[2]
POSTGRES_URL = os.getenv("PROFITOPS_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="PROFITOPS_TEST_POSTGRES_URL is not set",
)
BUSINESS_TABLES = (
    "stores", "products", "orders", "daily_operations", "knowledge_documents",
    "knowledge_chunks", "analyses", "recommendations", "executions", "audit_logs",
)


@contextmanager
def _postgres_schema():
    schema = f"profitops_migration_{uuid4().hex[:12]}"
    admin = create_engine(POSTGRES_URL, pool_pre_ping=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(POSTGRES_URL).set(
        query={**make_url(POSTGRES_URL).query, "options": f"-csearch_path={schema}"}
    )
    schema_url = url.render_as_string(hide_password=False)
    engine = create_engine(schema_url, pool_pre_ping=True)
    try:
        yield engine, schema_url
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _row_counts(engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in BUSINESS_TABLES
        }


def _insert_analysis(engine, analysis_id: str, tenant_id: str, key: str) -> None:
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO analyses (id, tenant_id, idempotency_key, request, actor, "
            "request_id, status, steps, snapshot, created_at, updated_at) VALUES "
            "(:id, :tenant_id, :key, '{}', 'demo-analyst', 'migration', 'completed', "
            "'[]', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), {"id": analysis_id, "tenant_id": tenant_id, "key": key})


def test_postgres_stamped_legacy_database_upgrades_in_place(monkeypatch):
    with _postgres_schema() as (engine, schema_url):
        legacy_metadata().create_all(engine)
        seed_legacy_rows(engine)
        before = _row_counts(engine)
        legacy_columns = {column["name"] for column in inspect(engine).get_columns("analyses")}
        assert "tenant_id" not in legacy_columns
        assert "lease_until" not in legacy_columns

        monkeypatch.setenv("PROFITOPS_DATABASE_URL", schema_url)
        config = Config(str(ROOT / "backend" / "alembic.ini"))
        command.stamp(config, "0001_baseline")
        command.upgrade(config, "head")

        inspector = inspect(engine)
        analyses_columns = {column["name"]: column for column in inspector.get_columns("analyses")}
        assert {"tenant_id", "attempts", "max_attempts", "next_run_at", "lease_until", "worker_id"} <= set(analyses_columns)
        assert analyses_columns["idempotency_key"]["type"].length == 200
        executions_columns = {column["name"]: column for column in inspector.get_columns("executions")}
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

        operations_unique = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("daily_operations")
        }
        assert ("store_id", "product_id", "date") in operations_unique

        analyses_indexes = {index["name"] for index in inspector.get_indexes("analyses")}
        knowledge_indexes = {index["name"] for index in inspector.get_indexes("knowledge_documents")}
        assert {"ix_analyses_tenant_id", "ix_analyses_status_next_run"} <= analyses_indexes
        assert "uq_knowledge_documents_tenant_active_sha" in knowledge_indexes
        assert LEGACY_ACTIVE_SHA_INDEX not in knowledge_indexes

        assert _row_counts(engine) == before
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT tenant_id, idempotency_key FROM analyses WHERE id='legacy-analysis'"
            )).one() == ("demo", "legacy-key")
            assert connection.execute(text(
                "SELECT analysis_id FROM recommendations WHERE id='legacy-recommendation'"
            )).scalar_one() == "legacy-analysis"
            assert connection.execute(text(
                "SELECT recommendation_id FROM executions WHERE id='legacy-execution'"
            )).scalar_one() == "legacy-recommendation"
            assert connection.execute(text(
                "SELECT object_id FROM audit_logs WHERE id='legacy-audit'"
            )).scalar_one() == "legacy-recommendation"
            assert connection.execute(text(
                "SELECT tenant_id FROM knowledge_documents WHERE id='legacy-document'"
            )).scalar_one() == "demo"
            # PostgreSQL enforces the restored FK relationships.
            assert connection.execute(text(
                "SELECT COUNT(*) FROM recommendations r "
                "LEFT JOIN analyses a ON a.id = r.analysis_id WHERE a.id IS NULL"
            )).scalar_one() == 0
            assert connection.execute(text(
                "SELECT COUNT(*) FROM executions e "
                "LEFT JOIN recommendations r ON r.id = e.recommendation_id WHERE r.id IS NULL"
            )).scalar_one() == 0

        _insert_analysis(engine, "same-key-demo", "demo", "shared-key")
        _insert_analysis(engine, "same-key-acme", "acme", "shared-key")
        _insert_analysis(engine, "long-key", "acme", "acme:" + "k" * 100)
        with pytest.raises(IntegrityError):
            _insert_analysis(engine, "duplicate-key", "acme", "shared-key")

        counts_after_first = _row_counts(engine)
        command.upgrade(config, "head")
        assert _row_counts(engine) == counts_after_first
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM schema_versions")).scalar_one() == 1
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "0002_tenant_scoped_idempotency"
            )


def test_postgres_downgrade_refuses_to_collapse_tenant_keys(monkeypatch):
    with _postgres_schema() as (engine, schema_url):
        legacy_metadata().create_all(engine)
        seed_legacy_rows(engine)

        monkeypatch.setenv("PROFITOPS_DATABASE_URL", schema_url)
        config = Config(str(ROOT / "backend" / "alembic.ini"))
        command.stamp(config, "0001_baseline")
        command.upgrade(config, "head")
        _insert_analysis(engine, "acme-shared-key", "acme", "legacy-key")

        with pytest.raises(IntegrityError):
            command.downgrade(config, "0001_baseline")

        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT tenant_id FROM analyses WHERE idempotency_key='legacy-key' ORDER BY tenant_id"
            )).scalars().all() == ["acme", "demo"]
        analyses_unique = {
            tuple(constraint["column_names"])
            for constraint in inspect(engine).get_unique_constraints("analyses")
        }
        assert ("tenant_id", "idempotency_key") in analyses_unique
