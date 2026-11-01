"""Scope idempotency keys by tenant and widen their storage.

Old databases enforced a global unique constraint on ``idempotency_key`` and
stored at most 100 characters. Non-demo tenants prefix the stored key with
their tenant id, so two tenants could collide on the same stored string and a
valid 100-character client key could exceed the column limit.

Revision 0002 drops the legacy global uniqueness, widens both columns to 200
characters, and adds ``(tenant_id, idempotency_key)`` uniqueness for analyses
and executions. Existing rows are preserved; the old global uniqueness means
the new composite constraint cannot already be violated.

SQLite cannot drop an inline unique constraint, so the two tables are rebuilt
with ``batch_alter_table``. PostgreSQL uses in-place ``ALTER TABLE``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db import upgrade_legacy_schema
from app.models import Analysis, Execution

revision: str = "0002_tenant_scoped_idempotency"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ANALYSES_UNIQUE = "uq_analyses_tenant_idempotency"
EXECUTIONS_UNIQUE = "uq_executions_tenant_idempotency"


def _unique_constraints(bind, table: str) -> list[dict]:
    return list(sa.inspect(bind).get_unique_constraints(table))


def _unique_indexes(bind, table: str) -> list[dict]:
    return [
        item for item in sa.inspect(bind).get_indexes(table)
        if item.get("unique")
    ]


def _upgrade_postgres(bind) -> None:
    for table, column, constraint in (
        ("analyses", "idempotency_key", ANALYSES_UNIQUE),
        ("executions", "idempotency_key", EXECUTIONS_UNIQUE),
    ):
        constraints = _unique_constraints(bind, table)
        legacy = [
            item for item in constraints
            if item["column_names"] == [column] and item.get("name")
        ]
        composite = [
            item for item in constraints
            if item["column_names"] == ["tenant_id", column]
        ]
        for item in legacy:
            op.drop_constraint(item["name"], table, type_="unique")
        for item in _unique_indexes(bind, table):
            if (
                item["column_names"] == [column] and item.get("name")
                and not item.get("duplicates_constraint")
            ):
                op.drop_index(item["name"], table_name=table)
        op.alter_column(
            table, column,
            existing_type=sa.String(100), type_=sa.String(200), existing_nullable=False,
        )
        if not composite:
            op.create_unique_constraint(constraint, table, ["tenant_id", column])


def _recreate_sqlite(table: str, copy_from: sa.Table) -> None:
    # SQLite's table rebuild drops the old table. Child rows (recommendations
    # reference analyses; executions reference recommendations) would otherwise
    # fail that DROP while foreign keys are enforced. ``upgrade()`` turns
    # enforcement off on this connection before any DML, because the pragma is
    # ignored inside a transaction and the knowledge dedupe above opens one.
    bind = op.get_bind()
    with op.batch_alter_table(table, copy_from=copy_from, recreate="always"):
        pass
    # The rebuild does not recreate indexes that exist only in the supplied
    # table definition (notably ``index=True`` columns such as tenant_id).
    # Restore every declared index that the rebuilt table is missing.
    existing = {item["name"] for item in sa.inspect(bind).get_indexes(table)}
    for index in copy_from.indexes:
        if index.name and index.name not in existing:
            op.create_index(
                index.name, table, [column.name for column in index.columns],
                unique=index.unique,
            )


def _upgrade_sqlite() -> None:
    _recreate_sqlite("analyses", Analysis.__table__)
    _recreate_sqlite("executions", Execution.__table__)


def _legacy_analyses_table() -> sa.Table:
    table = sa.Table(
        "analyses", sa.MetaData(),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(40), nullable=False, server_default=sa.text("'demo'")),
        sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("recommendation_id", sa.String(36), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Index("ix_analyses_tenant_id", table.c.tenant_id)
    sa.Index("ix_analyses_status_next_run", table.c.status, table.c.next_run_at)
    return table


def _legacy_executions_table() -> sa.Table:
    table = sa.Table(
        "executions", sa.MetaData(),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(40), nullable=False, server_default=sa.text("'demo'")),
        sa.Column(
            "recommendation_id", sa.String(36),
            sa.ForeignKey("recommendations.id"), nullable=False, unique=True,
        ),
        sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("simulation", sa.Boolean(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Index("ix_executions_tenant_id", table.c.tenant_id)
    return table


def _downgrade_sqlite() -> None:
    _recreate_sqlite("analyses", _legacy_analyses_table())
    _recreate_sqlite("executions", _legacy_executions_table())


def _downgrade_postgres(bind) -> None:
    for table, column, constraint in (
        ("analyses", "idempotency_key", ANALYSES_UNIQUE),
        ("executions", "idempotency_key", EXECUTIONS_UNIQUE),
    ):
        constraints = _unique_constraints(bind, table)
        composite = [
            item for item in constraints
            if item["column_names"] == ["tenant_id", column] and item.get("name")
        ]
        legacy = [
            item for item in constraints
            if item["column_names"] == [column]
        ]
        for item in composite:
            op.drop_constraint(item["name"], table, type_="unique")
        op.alter_column(
            table, column,
            existing_type=sa.String(200), type_=sa.String(100), existing_nullable=False,
        )
        if not legacy:
            op.create_unique_constraint(f"{table}_{column}_key", table, [column])


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite ignores this pragma inside a transaction, and the knowledge
        # dedupe below issues UPDATEs that open one. Set it before any DML:
        # both table rebuilds drop a parent table while child rows still
        # reference it. Every row is copied to the replacement table, so the
        # relationships stay intact. The pragma is connection-scoped; the
        # engine's next connection turns enforcement back on.
        bind.execute(sa.text("PRAGMA foreign_keys=OFF"))
    # A genuine pre-tenancy database has no tenant_id or durable-job columns.
    # It can only be stamped as 0001_baseline, and stamping runs no DDL, so the
    # baseline shape must be materialized here before the constraint changes.
    upgrade_legacy_schema(bind)
    if bind.dialect.name == "sqlite":
        _upgrade_sqlite()
    else:
        _upgrade_postgres(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # Same parent-table rebuild as the upgrade: disable enforcement before
        # any DML in this migration's transaction.
        bind.execute(sa.text("PRAGMA foreign_keys=OFF"))
        _downgrade_sqlite()
    else:
        _downgrade_postgres(bind)
