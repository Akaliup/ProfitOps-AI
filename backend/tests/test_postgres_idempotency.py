"""PostgreSQL regressions for tenant-scoped idempotency keys.

These tests need ``PROFITOPS_TEST_POSTGRES_URL``. They run in a disposable
schema so the developer database is never modified.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError

from app.db import initialize_database, make_session_factory
from app.models import Analysis, Execution, Recommendation


@contextmanager
def _postgres_schema_engine():
    url = os.environ["PROFITOPS_TEST_POSTGRES_URL"]
    schema = f"profitops_idempotency_{uuid4().hex[:12]}"
    admin = create_engine(url, pool_pre_ping=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.skipif(
    not os.getenv("PROFITOPS_TEST_POSTGRES_URL"),
    reason="PROFITOPS_TEST_POSTGRES_URL is not set",
)
def test_postgres_tenant_scoped_idempotency_and_long_keys():
    with _postgres_schema_engine() as engine:
        initialize_database(engine)
        factory = make_session_factory(engine)
        long_analysis_key = "acme:" + "k" * 100
        long_execution_key = "acme:" + "e" * 100

        with factory.begin() as session:
            session.add_all([
                Analysis(
                    id="pg-a-demo", tenant_id="demo",
                    idempotency_key="acme:shared", request={},
                    actor="demo-analyst", request_id="pg-1",
                ),
                Analysis(
                    id="pg-a-acme", tenant_id="acme",
                    idempotency_key="acme:shared", request={},
                    actor="acme-analyst", request_id="pg-2",
                ),
                Analysis(
                    id="pg-a-long", tenant_id="acme",
                    idempotency_key=long_analysis_key, request={},
                    actor="acme-analyst", request_id="pg-3",
                ),
            ])

        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                session.add(Analysis(
                    id="pg-a-duplicate", tenant_id="acme",
                    idempotency_key="acme:shared", request={},
                    actor="acme-analyst", request_id="pg-4",
                ))

        with factory.begin() as session:
            session.add_all([
                Recommendation(
                    id="pg-r-demo", tenant_id="demo", analysis_id="pg-a-demo",
                    status="approved", title="demo", body={}, action={},
                ),
                Recommendation(
                    id="pg-r-acme", tenant_id="acme", analysis_id="pg-a-acme",
                    status="approved", title="acme", body={}, action={},
                ),
                Recommendation(
                    id="pg-r-long", tenant_id="acme", analysis_id="pg-a-long",
                    status="approved", title="long", body={}, action={},
                ),
            ])
            session.flush()
            session.add_all([
                Execution(
                    id="pg-e-demo", tenant_id="demo", recommendation_id="pg-r-demo",
                    idempotency_key="acme:exec", status="executed", simulation=True,
                    actor="demo-approver", request_id="pg-5",
                ),
                Execution(
                    id="pg-e-acme", tenant_id="acme", recommendation_id="pg-r-acme",
                    idempotency_key="acme:exec", status="executed", simulation=True,
                    actor="acme-approver", request_id="pg-6",
                ),
                Execution(
                    id="pg-e-long", tenant_id="acme", recommendation_id="pg-r-long",
                    idempotency_key=long_execution_key, status="executed", simulation=True,
                    actor="acme-approver", request_id="pg-7",
                ),
            ])

        with factory.begin() as session:
            session.add(Analysis(
                id="pg-a-duplicate-execution", tenant_id="acme",
                idempotency_key="acme:duplicate-execution", request={},
                actor="acme-analyst", request_id="pg-8",
            ))
            session.add(Recommendation(
                id="pg-r-duplicate-execution", tenant_id="acme",
                analysis_id="pg-a-duplicate-execution", status="approved",
                title="duplicate", body={}, action={},
            ))
        with pytest.raises(IntegrityError):
            with factory.begin() as session:
                session.add(Execution(
                    id="pg-e-duplicate", tenant_id="acme",
                    recommendation_id="pg-r-duplicate-execution",
                    idempotency_key="acme:exec", status="executed", simulation=True,
                    actor="acme-approver", request_id="pg-9",
                ))

        with factory() as session:
            assert session.scalar(select(func.count()).select_from(Analysis)) == 4
            assert session.scalar(select(func.count()).select_from(Execution)) == 3
            assert session.get(Analysis, "pg-a-long").idempotency_key == long_analysis_key
            assert session.get(Execution, "pg-e-long").idempotency_key == long_execution_key
