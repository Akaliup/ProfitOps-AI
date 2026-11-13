"""Migration tests build the database from the real pre-tenancy schema.

``legacy_schema.legacy_metadata`` is the schema shipped before commit
0f21732. It deliberately has no ``tenant_id``, ``attempts``, ``lease_until``,
or durable-job columns, so these tests exercise the path a real old database
takes: ``stamp 0001_baseline`` followed by ``upgrade head``.
"""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

pytest.importorskip("alembic")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.db import make_session_factory  # noqa: E402
from app.models import KNOWLEDGE_ACTIVE_SHA_INDEX, Analysis  # noqa: E402
from legacy_schema import (  # noqa: E402
    LEGACY_ACTIVE_SHA_INDEX,
    legacy_metadata,
    seed_legacy_rows,
)

ROOT = Path(__file__).resolve().parents[2]
BUSINESS_TABLES = (
    "stores", "products", "orders", "daily_operations", "knowledge_documents",
    "knowledge_chunks", "analyses", "recommendations", "executions", "audit_logs",
)


def _alembic_config() -> Config:
    return Config(str(ROOT / "backend" / "alembic.ini"))


def test_alembic_baseline_upgrade_and_downgrade(tmp_path, monkeypatch):
    database = tmp_path / "migration.db"
    monkeypatch.setenv("PROFITOPS_DATABASE_URL", f"sqlite:///{database}")
    config = _alembic_config()

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


def _create_legacy_database(path: Path):
    engine = create_engine(f"sqlite:///{path}")
    legacy_metadata().create_all(engine)
    seed_legacy_rows(engine)
    return engine


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


def test_stamped_true_legacy_database_upgrades_in_place(tmp_path, monkeypatch):
    database = tmp_path / "legacy.db"
    engine = _create_legacy_database(database)
    before = _row_counts(engine)
    legacy_columns = {column["name"] for column in inspect(engine).get_columns("analyses")}
    assert "tenant_id" not in legacy_columns
    assert "attempts" not in legacy_columns
    assert "lease_until" not in legacy_columns
    engine.dispose()

    monkeypatch.setenv("PROFITOPS_DATABASE_URL", f"sqlite:///{database}")
    config = _alembic_config()
    command.stamp(config, "0001_baseline")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    inspector = inspect(engine)
    analyses_columns = {column["name"]: column for column in inspector.get_columns("analyses")}
    assert {"tenant_id", "attempts", "max_attempts", "next_run_at", "lease_until", "worker_id"} <= set(analyses_columns)
    assert analyses_columns["idempotency_key"]["type"].length == 200

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

    # Pre-existing business constraints are not rebuilt away.
    operations_unique = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("daily_operations")
    }
    assert ("store_id", "product_id", "date") in operations_unique

    analyses_indexes = {index["name"] for index in inspector.get_indexes("analyses")}
    executions_indexes = {index["name"] for index in inspector.get_indexes("executions")}
    knowledge_indexes = {index["name"] for index in inspector.get_indexes("knowledge_documents")}
    assert {"ix_analyses_tenant_id", "ix_analyses_status_next_run"} <= analyses_indexes
    assert "ix_executions_tenant_id" in executions_indexes
    assert KNOWLEDGE_ACTIVE_SHA_INDEX in knowledge_indexes
    assert LEGACY_ACTIVE_SHA_INDEX not in knowledge_indexes

    # Every business row survived, and the old foreign-key relationships are
    # still intact (the migration never drops or recreates business tables).
    assert _row_counts(engine) == before
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT tenant_id, idempotency_key FROM analyses WHERE id='legacy-analysis'"
        )).one() == ("demo", "legacy-key")
        assert connection.execute(text(
            "SELECT tenant_id, analysis_id FROM recommendations WHERE id='legacy-recommendation'"
        )).one() == ("demo", "legacy-analysis")
        assert connection.execute(text(
            "SELECT tenant_id, recommendation_id FROM executions WHERE id='legacy-execution'"
        )).one() == ("demo", "legacy-recommendation")
        assert connection.execute(text(
            "SELECT tenant_id, object_id FROM audit_logs WHERE id='legacy-audit'"
        )).one() == ("demo", "legacy-recommendation")
        assert connection.execute(text(
            "SELECT tenant_id FROM knowledge_documents WHERE id='legacy-document'"
        )).scalar_one() == "demo"
        assert connection.execute(text(
            "SELECT document_id FROM knowledge_chunks WHERE id='legacy-chunk'"
        )).scalar_one() == "legacy-document"
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []

    # The new composite key permits the same key in two tenants, rejects it
    # twice in one tenant, and accepts a key longer than the old 100-char cap.
    _insert_analysis(engine, "same-key-demo", "demo", "shared-key")
    _insert_analysis(engine, "same-key-acme", "acme", "shared-key")
    _insert_analysis(engine, "long-key", "acme", "acme:" + "k" * 100)
    with pytest.raises(IntegrityError):
        _insert_analysis(engine, "duplicate-key", "acme", "shared-key")

    # The knowledge uniqueness is now per tenant and still only covers active
    # rows: another tenant may own the same content, and inactive duplicates
    # are history rather than conflicts.
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO knowledge_documents (id, tenant_id, title, source, version, content, "
            "content_sha256, metadata_json, active, created_at, updated_at) VALUES "
            "('acme-document', 'acme', 'Acme SOP', 'acme', 'v1', 'legacy content', :sha, "
            "'{}', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), {"sha": "a" * 64})
        connection.execute(text(
            "INSERT INTO knowledge_documents (id, tenant_id, title, source, version, content, "
            "content_sha256, metadata_json, active, created_at, updated_at) VALUES "
            "('inactive-duplicate', 'demo', 'Old SOP', 'demo', 'v0', 'legacy content', :sha, "
            "'{}', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), {"sha": "a" * 64})
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO knowledge_documents (id, tenant_id, title, source, version, content, "
                "content_sha256, metadata_json, active, created_at, updated_at) VALUES "
                "('demo-duplicate', 'demo', 'Duplicate', 'demo', 'v1', 'legacy content', :sha, "
                "'{}', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {"sha": "a" * 64})

    # Re-running the upgrade is a no-op: no duplicate columns, indexes, or
    # rows, and no second schema version.
    counts_after_first = _row_counts(engine)
    engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database}")
    assert _row_counts(engine) == counts_after_first
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM schema_versions")).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0004_intelligence"
        )
    assert "import_batches" in inspect(engine).get_table_names()
    engine.dispose()


def test_downgrade_refuses_to_collapse_tenant_keys_and_keeps_rows(tmp_path, monkeypatch):
    database = tmp_path / "legacy-downgrade.db"
    engine = _create_legacy_database(database)
    engine.dispose()

    monkeypatch.setenv("PROFITOPS_DATABASE_URL", f"sqlite:///{database}")
    config = _alembic_config()
    command.stamp(config, "0001_baseline")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    _insert_analysis(engine, "acme-shared-key", "acme", "legacy-key")

    with pytest.raises(IntegrityError, match=r"UNIQUE constraint failed: .*analyses\.idempotency_key"):
        command.downgrade(config, "0001_baseline")

    # The failed downgrade must not drop either tenant's row or the composite
    # constraint that makes the data representable.
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT tenant_id FROM analyses WHERE idempotency_key='legacy-key' ORDER BY tenant_id"
        )).scalars().all() == ["acme", "demo"]
    inspector = inspect(engine)
    analyses_unique = {
        tuple(constraint["column_names"]) for constraint in inspector.get_unique_constraints("analyses")
    }
    assert ("tenant_id", "idempotency_key") in analyses_unique
    engine.dispose()


def test_legacy_database_with_duplicate_active_documents_soft_deletes_extras(tmp_path, monkeypatch):
    """A pre-index database may hold duplicates; migration keeps every row."""
    database = tmp_path / "legacy-duplicates.db"
    engine = _create_legacy_database(database)
    with engine.begin() as connection:
        # Databases older than the legacy unique index could already hold
        # duplicates. Dropping it reproduces that state without inventing a
        # different table shape.
        connection.execute(text(f"DROP INDEX {LEGACY_ACTIVE_SHA_INDEX}"))
        connection.execute(text(
            "INSERT INTO knowledge_documents (id, title, source, version, content, content_sha256, "
            "metadata_json, active, created_at, updated_at) VALUES "
            "('duplicate-document', 'Duplicate SOP', 'legacy', 'v1', 'legacy content', :sha, "
            "'{}', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), {"sha": "a" * 64})
    engine.dispose()

    monkeypatch.setenv("PROFITOPS_DATABASE_URL", f"sqlite:///{database}")
    config = _alembic_config()
    command.stamp(config, "0001_baseline")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, active FROM knowledge_documents WHERE content_sha256=:sha ORDER BY id"
        ), {"sha": "a" * 64}).all()
        assert len(rows) == 2
        assert sum(1 for _, active in rows if active) == 1
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
    engine.dispose()
