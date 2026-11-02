from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.config import Settings
from app.db import build_engine, initialize_database, make_session_factory
from app.models import Analysis, AuditLog, Recommendation, lease_is_live, utcnow
from app import main as main_module
from app import worker, workflow


def add_analysis(client, **overrides):
    values = {
        "id": "job-1", "idempotency_key": "job-1", "request": {},
        "actor": "demo-analyst", "request_id": "worker-test", "status": "queued",
        "max_attempts": 2,
    }
    values.update(overrides)
    with client.app.state.factory.begin() as session:
        session.add(Analysis(**values))


def test_claim_is_atomic_and_increments_attempts(client):
    add_analysis(client)
    factory, engine = client.app.state.factory, client.app.state.engine
    settings = client.app.state.settings
    assert worker.claim_job(factory, engine, "job-1", settings, "worker-a") is True
    assert worker.claim_job(factory, engine, "job-1", settings, "worker-b") is False
    with factory() as session:
        row = session.get(Analysis, "job-1")
        assert row.status == "running"
        assert row.worker_id == "worker-a"
        assert row.attempts == 1
        assert row.lease_until is not None
        assert session.scalar(select(AuditLog).where(AuditLog.action == "analysis.claimed")) is not None


def test_expired_lease_is_requeued_but_live_lease_is_kept(client):
    now = utcnow()
    add_analysis(client, id="expired", idempotency_key="expired", status="running", attempts=1, lease_until=now - timedelta(seconds=1), worker_id="dead")
    add_analysis(client, id="live", idempotency_key="live", status="running", attempts=1, lease_until=now + timedelta(seconds=60), worker_id="alive")
    factory, engine = client.app.state.factory, client.app.state.engine
    assert worker.requeue_expired(factory, engine, now=now) == 1
    with factory() as session:
        expired = session.get(Analysis, "expired")
        live = session.get(Analysis, "live")
        assert expired.status == "queued" and expired.error["code"] == "lease_expired"
        assert expired.worker_id is None and expired.lease_until is None
        assert live.status == "running" and live.worker_id == "alive"
        assert session.scalar(select(AuditLog).where(AuditLog.action == "analysis.recovered")) is not None


def test_requeue_expired_dead_letters_after_max_attempts(client):
    first_now = utcnow()
    add_analysis(
        client, id="exhausted", idempotency_key="exhausted", status="running",
        attempts=1, max_attempts=2, worker_id="worker-a",
        lease_until=first_now - timedelta(seconds=1),
    )
    factory, engine = client.app.state.factory, client.app.state.engine
    settings = client.app.state.settings

    assert worker.requeue_expired(factory, engine, now=first_now) == 1
    with factory() as session:
        row = session.get(Analysis, "exhausted")
        assert row.status == "queued" and row.attempts == 1
        assert row.error["code"] == "lease_expired"
        assert row.error.get("dead_letter") is not True

    assert worker.claim_job(factory, engine, "exhausted", settings, "worker-b", now=first_now) is True
    second_now = first_now + timedelta(seconds=1)
    with factory.begin() as session:
        row = session.get(Analysis, "exhausted")
        row.lease_until = second_now - timedelta(seconds=1)

    assert worker.requeue_expired(factory, engine, now=second_now) == 1
    with factory() as session:
        row = session.get(Analysis, "exhausted")
        assert row.status == "failed"
        assert row.attempts == 2
        assert row.error["code"] == "lease_expired"
        assert row.error["dead_letter"] is True
        assert row.error["attempt"] == 2
        assert row.error["max_attempts"] == 2
        assert row.worker_id is None and row.lease_until is None and row.next_run_at is None
        assert session.scalar(select(AuditLog).where(
            AuditLog.action == "analysis.dead_letter", AuditLog.object_id == "exhausted",
        )) is not None

    # A dead-lettered row is no longer running, so later recovery passes leave
    # it untouched instead of requeueing it forever.
    assert worker.requeue_expired(factory, engine, now=second_now + timedelta(seconds=1)) == 0


def test_finalize_schedules_backoff_then_dead_letters(client):
    now = utcnow()
    add_analysis(client, id="retry", idempotency_key="retry", status="running", attempts=1, max_attempts=2, worker_id="w", lease_until=now + timedelta(seconds=60))
    with client.app.state.factory.begin() as session:
        row = session.get(Analysis, "retry")
        row.error = {"code": "workflow_failed", "message": "transient"}
    settings = client.app.state.settings
    settings.analysis_retry_backoff_seconds = 0
    assert worker.finalize_job(client.app.state.factory, client.app.state.engine, "retry", settings, now=now) == "queued"
    with client.app.state.factory() as session:
        row = session.get(Analysis, "retry")
        assert row.error["retry_scheduled"] is True
        assert row.next_run_at is not None and row.lease_until is None
        assert session.scalar(select(AuditLog).where(AuditLog.action == "analysis.retry_scheduled")) is not None

    with client.app.state.factory.begin() as session:
        row = session.get(Analysis, "retry")
        row.status = "running"
        row.attempts = 2
        row.error = {"code": "workflow_failed", "message": "still failing"}
    assert worker.finalize_job(client.app.state.factory, client.app.state.engine, "retry", settings, now=now) == "failed"
    with client.app.state.factory() as session:
        row = session.get(Analysis, "retry")
        assert row.error["dead_letter"] is True
        assert session.scalar(select(AuditLog).where(AuditLog.action == "analysis.dead_letter")) is not None


def test_worker_retries_transient_failure(client, monkeypatch):
    add_analysis(client, max_attempts=2)
    settings = client.app.state.settings
    settings.analysis_retry_backoff_seconds = 0
    calls = []

    def fake_run(factory, registry, analysis_id, settings, llm, claimed=False, **kwargs):
        calls.append(claimed)
        with factory.begin() as session:
            row = session.get(Analysis, analysis_id)
            if row.attempts == 1:
                row.status = "failed"
                row.error = {"code": "workflow_failed", "message": "transient"}
            else:
                row.status = "completed"
                row.result = {"engine": "deterministic-rules-v1"}

    monkeypatch.setattr(worker, "run_analysis", fake_run)
    factory, engine = client.app.state.factory, client.app.state.engine
    assert worker.run_due_jobs(factory, engine, client.app.state.registry, settings, None, worker_id="w") == 1
    with factory() as session:
        row = session.get(Analysis, "job-1")
        assert row.status == "queued" and row.attempts == 1
        assert row.error["retry_scheduled"] is True
    assert worker.run_due_jobs(factory, engine, client.app.state.registry, settings, None, worker_id="w") == 1
    with factory() as session:
        row = session.get(Analysis, "job-1")
        assert row.status == "completed"
        assert row.attempts == 2
        assert row.next_run_at is None
    assert calls == [True, True]


def test_finalize_rejects_stale_worker_and_attempt(client):
    now = utcnow()
    add_analysis(
        client, id="fenced", idempotency_key="fenced", status="running", attempts=2,
        worker_id="worker-b", lease_until=now + timedelta(seconds=60),
    )
    factory, engine, settings = (
        client.app.state.factory, client.app.state.engine, client.app.state.settings,
    )
    assert worker.finalize_job(
        factory, engine, "fenced", settings, now=now, worker_id="worker-a", attempt=2,
    ) is None
    assert worker.finalize_job(
        factory, engine, "fenced", settings, now=now, worker_id="worker-b", attempt=1,
    ) is None
    with factory() as session:
        row = session.get(Analysis, "fenced")
        assert row.status == "running" and row.worker_id == "worker-b"
        assert row.lease_until is not None

    with factory.begin() as session:
        session.get(Analysis, "fenced").status = "completed"
    assert worker.finalize_job(
        factory, engine, "fenced", settings, now=now, worker_id="worker-b", attempt=2,
    ) == "completed"
    with factory() as session:
        row = session.get(Analysis, "fenced")
        assert row.worker_id is None and row.lease_until is None


def test_process_claimed_job_does_not_finalize_reclaimed_job(client, monkeypatch):
    now = utcnow()
    add_analysis(
        client, status="running", attempts=1, worker_id="worker-a",
        lease_until=now + timedelta(seconds=60),
    )
    calls = []

    def fake_run(factory, registry, analysis_id, settings, llm, claimed=False, **kwargs):
        calls.append(kwargs)
        # Simulate the lease expiring while this worker is still running and a
        # newer worker reclaiming the job with the next attempt number.
        with factory.begin() as session:
            row = session.get(Analysis, analysis_id)
            row.worker_id = "worker-b"
            row.attempts = 2
            row.lease_until = utcnow() + timedelta(seconds=60)

    monkeypatch.setattr(worker, "run_analysis", fake_run)
    factory, engine, registry, settings = (
        client.app.state.factory, client.app.state.engine,
        client.app.state.registry, client.app.state.settings,
    )
    assert worker.process_claimed_job(
        factory, engine, registry, "job-1", settings, None, "worker-a",
    ) is None
    with factory() as session:
        row = session.get(Analysis, "job-1")
        assert row.status == "running"
        assert row.worker_id == "worker-b" and row.attempts == 2
        assert row.lease_until is not None
    assert calls == [{"worker_id": "worker-a", "expected_attempt": 1}]


def test_process_claimed_job_skips_work_it_does_not_own(client, monkeypatch):
    now = utcnow()
    add_analysis(
        client, status="running", attempts=1, worker_id="worker-b",
        lease_until=now + timedelta(seconds=60),
    )

    def explode(*args, **kwargs):
        raise AssertionError("must not run or finalize another worker's job")

    monkeypatch.setattr(worker, "run_analysis", explode)
    monkeypatch.setattr(worker, "finalize_job", explode)
    factory, engine, registry, settings = (
        client.app.state.factory, client.app.state.engine,
        client.app.state.registry, client.app.state.settings,
    )
    assert worker.process_claimed_job(
        factory, engine, registry, "job-1", settings, None, "worker-a",
    ) is None
    with factory() as session:
        row = session.get(Analysis, "job-1")
        assert row.status == "running" and row.worker_id == "worker-b"


def test_run_analysis_stops_writing_after_lease_is_reclaimed(client, monkeypatch):
    created = client.post("/api/analyses", json={}, headers={"Authorization": "Bearer demo-analyst"})
    assert created.status_code == 202
    analysis_id = created.json()["id"]
    factory, registry, settings = (
        client.app.state.factory, client.app.state.registry, client.app.state.settings,
    )
    now = utcnow()
    with factory.begin() as session:
        row = session.get(Analysis, analysis_id)
        # The API test client also runs the background workflow; reset it so
        # this test starts from a clean, freshly claimed job.
        row.steps = []
        row.snapshot = {}
        row.result = None
        row.recommendation_id = None
        row.status = "running"
        row.attempts = 1
        row.worker_id = "worker-a"
        row.lease_until = now + timedelta(seconds=60)

    original = registry.invoke
    stolen = []

    def invoke(name, arguments, context):
        if not stolen:
            stolen.append(name)
            with factory.begin() as session:
                row = session.get(Analysis, analysis_id)
                row.worker_id = "worker-b"
                row.attempts = 2
                row.lease_until = utcnow() + timedelta(seconds=60)
        return original(name, arguments, context)

    monkeypatch.setattr(registry, "invoke", invoke)
    workflow.run_analysis(
        factory, registry, analysis_id, settings, None, claimed=True,
        worker_id="worker-a", expected_attempt=1,
    )

    assert stolen == ["get_sales_metrics"]
    with factory() as session:
        row = session.get(Analysis, analysis_id)
        assert row.status == "running"
        assert row.worker_id == "worker-b" and row.attempts == 2
        assert row.lease_until is not None
        # The stale worker persisted its running step before the steal, but it
        # could not complete it or change the reclaimed job afterwards.
        assert [step["name"] for step in row.steps] == ["receive_request", "get_sales_metrics"]
        assert row.steps[0]["status"] == "completed"
        assert row.steps[1]["status"] == "running"


def test_create_recommendation_rejects_reclaimed_lease_before_write(client, monkeypatch):
    created = client.post("/api/analyses", json={}, headers={"Authorization": "Bearer demo-analyst"})
    assert created.status_code == 202
    analysis_id = created.json()["id"]
    factory, registry, settings = (
        client.app.state.factory, client.app.state.registry, client.app.state.settings,
    )
    now = utcnow()
    with factory.begin() as session:
        # The API background run already produced a recommendation; remove it
        # so this test starts from the same pre-write state as the old worker.
        session.execute(delete(Recommendation).where(Recommendation.analysis_id == analysis_id))
        row = session.get(Analysis, analysis_id)
        row.steps = []
        row.snapshot = {}
        row.result = None
        row.recommendation_id = None
        row.error = None
        row.status = "running"
        row.attempts = 1
        row.worker_id = "worker-a"
        row.lease_until = now + timedelta(seconds=60)

    original = registry.invoke
    calls = []

    def invoke(name, arguments, context):
        calls.append(name)
        if name == "create_recommendation":
            # Interleave a newer worker claiming the next attempt just before
            # the old worker enters the recommendation write.
            with factory.begin() as session:
                row = session.get(Analysis, analysis_id)
                row.worker_id = "worker-b"
                row.attempts = 2
                row.lease_until = utcnow() + timedelta(seconds=60)
        return original(name, arguments, context)

    monkeypatch.setattr(registry, "invoke", invoke)
    workflow.run_analysis(
        factory, registry, analysis_id, settings, None, claimed=True,
        worker_id="worker-a", expected_attempt=1,
    )

    assert "create_recommendation" in calls
    with factory() as session:
        assert session.scalar(select(Recommendation).where(
            Recommendation.analysis_id == analysis_id,
        )) is None
        row = session.get(Analysis, analysis_id)
        assert row.status == "running"
        assert row.worker_id == "worker-b" and row.attempts == 2
        assert row.recommendation_id is None
        # The step was started under the old lease, but the write itself was
        # rejected and the step was not marked completed.
        assert row.steps[-1]["name"] == "create_recommendation"
        assert row.steps[-1]["status"] == "running"


def test_lease_predicate_treats_exact_expiry_as_expired(client):
    """The boundary belongs to expiry, matching ``requeue_expired``'s ``<= now``."""
    boundary = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    add_analysis(
        client, id="boundary", idempotency_key="boundary", status="running",
        attempts=1, worker_id="worker-a", lease_until=boundary,
    )
    factory = client.app.state.factory

    def owned(now) -> bool:
        with factory() as session:
            return session.scalar(select(Analysis.id).where(
                Analysis.id == "boundary", lease_is_live(now),
            )) is not None

    assert owned(boundary) is False
    assert owned(boundary - timedelta(microseconds=1)) is True
    assert owned(boundary + timedelta(microseconds=1)) is False


def test_expired_lease_stops_writes_without_takeover(client, monkeypatch):
    """An expired lease blocks result writes even before anyone reclaims the job."""
    created = client.post("/api/analyses", json={}, headers={"Authorization": "Bearer demo-analyst"})
    assert created.status_code == 202
    analysis_id = created.json()["id"]
    factory, registry, settings = (
        client.app.state.factory, client.app.state.registry, client.app.state.settings,
    )
    now = utcnow()
    with factory.begin() as session:
        row = session.get(Analysis, analysis_id)
        row.steps = []
        row.snapshot = {}
        row.result = None
        row.recommendation_id = None
        row.error = None
        row.status = "running"
        row.attempts = 1
        row.worker_id = "worker-a"
        row.lease_until = now + timedelta(seconds=60)

    original = registry.invoke
    expired = []

    def invoke(name, arguments, context):
        if not expired:
            expired.append(name)
            # The lease runs out while the step is executing. No newer worker
            # claims the row: worker_id and attempts stay untouched, so only a
            # lease check can reject the write.
            with factory.begin() as session:
                session.get(Analysis, analysis_id).lease_until = utcnow() - timedelta(seconds=1)
        return original(name, arguments, context)

    monkeypatch.setattr(registry, "invoke", invoke)
    workflow.run_analysis(
        factory, registry, analysis_id, settings, None, claimed=True,
        worker_id="worker-a", expected_attempt=1,
    )

    assert expired == ["get_sales_metrics"]
    with factory() as session:
        row = session.get(Analysis, analysis_id)
        assert row.status == "running"
        assert row.worker_id == "worker-a" and row.attempts == 1
        # The step started under a live lease, but its result was not persisted.
        assert row.result is None
        assert "raw" not in row.snapshot and "report" not in row.snapshot
        assert [step["name"] for step in row.steps] == ["receive_request", "get_sales_metrics"]
        assert row.steps[-1]["status"] == "running"
        # A stale worker never revives its own lease.
        assert session.scalar(select(Analysis.id).where(
            Analysis.id == analysis_id, lease_is_live(utcnow()),
        )) is None


def test_create_recommendation_rejects_expired_lease_without_takeover(client, monkeypatch):
    created = client.post("/api/analyses", json={}, headers={"Authorization": "Bearer demo-analyst"})
    assert created.status_code == 202
    analysis_id = created.json()["id"]
    factory, registry, settings = (
        client.app.state.factory, client.app.state.registry, client.app.state.settings,
    )
    now = utcnow()
    with factory.begin() as session:
        session.execute(delete(Recommendation).where(Recommendation.analysis_id == analysis_id))
        row = session.get(Analysis, analysis_id)
        row.steps = []
        row.snapshot = {}
        row.result = None
        row.recommendation_id = None
        row.error = None
        row.status = "running"
        row.attempts = 1
        row.worker_id = "worker-a"
        row.lease_until = now + timedelta(seconds=60)

    original = registry.invoke
    calls = []

    def invoke(name, arguments, context):
        calls.append(name)
        if name == "create_recommendation":
            # Expire the lease in the same worker/attempt, without a takeover.
            with factory.begin() as session:
                session.get(Analysis, analysis_id).lease_until = utcnow() - timedelta(seconds=1)
        return original(name, arguments, context)

    monkeypatch.setattr(registry, "invoke", invoke)
    workflow.run_analysis(
        factory, registry, analysis_id, settings, None, claimed=True,
        worker_id="worker-a", expected_attempt=1,
    )

    assert "create_recommendation" in calls
    with factory() as session:
        assert session.scalar(select(Recommendation).where(
            Recommendation.analysis_id == analysis_id,
        )) is None
        row = session.get(Analysis, analysis_id)
        assert row.recommendation_id is None
        assert row.status == "running"
        assert row.worker_id == "worker-a" and row.attempts == 1
        assert row.steps[-1]["name"] == "create_recommendation"
        assert row.steps[-1]["status"] == "running"


def test_assert_owned_rejects_expired_lease_before_audit(client, monkeypatch):
    created = client.post("/api/analyses", json={}, headers={"Authorization": "Bearer demo-analyst"})
    assert created.status_code == 202
    analysis_id = created.json()["id"]
    factory, registry, settings = (
        client.app.state.factory, client.app.state.registry, client.app.state.settings,
    )
    now = utcnow()
    with factory.begin() as session:
        session.execute(delete(AuditLog).where(AuditLog.object_id == analysis_id))
        row = session.get(Analysis, analysis_id)
        row.steps = []
        row.snapshot = {}
        row.result = None
        row.recommendation_id = None
        row.error = None
        row.status = "running"
        row.attempts = 1
        row.worker_id = "worker-a"
        row.lease_until = now + timedelta(seconds=60)

    original_plan = workflow.plan_action
    expired = []

    def plan(report, findings):
        if not expired:
            expired.append(True)
            with factory.begin() as session:
                session.get(Analysis, analysis_id).lease_until = utcnow() - timedelta(seconds=1)
        return original_plan(report, findings)

    monkeypatch.setattr(workflow, "plan_action", plan)
    workflow.run_analysis(
        factory, registry, analysis_id, settings, None, claimed=True,
        worker_id="worker-a", expected_attempt=1,
    )

    assert expired
    with factory() as session:
        row = session.get(Analysis, analysis_id)
        # assert_owned runs before the LLM audit, so the expired lease kept the
        # audit row out entirely.
        assert session.scalar(select(AuditLog).where(
            AuditLog.object_id == analysis_id,
            AuditLog.action == "analysis.llm_fallback",
        )) is None
        assert row.steps[-1]["name"] == "llm_root_cause_analysis"
        assert row.steps[-1]["status"] == "running"


def test_heartbeat_does_not_renew_expired_or_taken_over_lease(client):
    now = utcnow()
    add_analysis(
        client, id="delayed", idempotency_key="delayed", status="running",
        attempts=1, worker_id="worker-a", lease_until=now - timedelta(seconds=1),
    )
    add_analysis(
        client, id="taken", idempotency_key="taken", status="running",
        attempts=2, worker_id="worker-b", lease_until=now + timedelta(seconds=60),
    )
    factory = client.app.state.factory
    with factory() as session:
        delayed_before = session.get(Analysis, "delayed").lease_until
        taken_before = session.get(Analysis, "taken").lease_until

    # A delayed beat that finds an expired lease must not renew it.
    assert worker.LeaseHeartbeat(factory, "delayed", "worker-a", 30, attempt=1)._beat() is False
    # A stale worker must not touch the newer worker's lease.
    assert worker.LeaseHeartbeat(factory, "taken", "worker-a", 30, attempt=1)._beat() is False

    with factory() as session:
        assert session.get(Analysis, "delayed").lease_until == delayed_before
        taken = session.get(Analysis, "taken")
        assert taken.lease_until == taken_before
        assert taken.worker_id == "worker-b" and taken.attempts == 2


def test_heartbeat_renews_a_live_lease(client):
    now = utcnow()
    add_analysis(
        client, id="live", idempotency_key="live", status="running",
        attempts=1, worker_id="worker-a", lease_until=now + timedelta(seconds=5),
    )
    factory = client.app.state.factory
    assert worker.LeaseHeartbeat(factory, "live", "worker-a", 30, attempt=1)._beat() is True
    with factory() as session:
        assert session.scalar(select(Analysis.id).where(
            Analysis.id == "live", lease_is_live(utcnow() + timedelta(seconds=20)),
        )) is not None


def test_manual_retry_endpoint_requeues_failed_job(client, analyst):
    created = client.post("/api/analyses", json={}, headers=analyst).json()
    with client.app.state.factory.begin() as session:
        row = session.get(Analysis, created["id"])
        row.status = "failed"
        row.error = {"code": "source_failed", "message": "forced"}
        row.attempts = 1
        row.max_attempts = 1
    retried = client.post(f"/api/analyses/{created['id']}/retry", headers=analyst)
    assert retried.status_code == 202
    assert retried.json()["status"] == "queued"
    detail = client.get(f"/api/analyses/{created['id']}", headers=analyst).json()
    assert detail["status"] == "waiting_approval"
    assert detail["attempts"] == 2
    assert detail["max_attempts"] == 2


def test_manual_retry_rejects_non_failed_job(client, analyst):
    created = client.post("/api/analyses", json={}, headers=analyst).json()
    response = client.post(f"/api/analyses/{created['id']}/retry", headers=analyst)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "analysis_not_retryable"


def test_manual_retry_does_not_overwrite_running_or_completed(client, analyst):
    created = client.post("/api/analyses", json={}, headers=analyst).json()
    factory = client.app.state.factory
    with factory.begin() as session:
        row = session.get(Analysis, created["id"])
        row.status = "running"
        row.attempts = 1
        row.worker_id = "worker-a"
        row.lease_until = utcnow() + timedelta(seconds=60)
    running = client.post(f"/api/analyses/{created['id']}/retry", headers=analyst)
    assert running.status_code == 409
    with factory() as session:
        row = session.get(Analysis, created["id"])
        assert row.status == "running"
        assert row.worker_id == "worker-a" and row.lease_until is not None

    with factory.begin() as session:
        row = session.get(Analysis, created["id"])
        row.status = "completed"
        row.worker_id = None
        row.lease_until = None
    completed = client.post(f"/api/analyses/{created['id']}/retry", headers=analyst)
    assert completed.status_code == 409
    with factory() as session:
        assert session.get(Analysis, created["id"]).status == "completed"


def test_concurrent_manual_retry_only_one_transition(client, analyst, monkeypatch):
    created = client.post("/api/analyses", json={}, headers=analyst).json()
    factory = client.app.state.factory
    with factory.begin() as session:
        row = session.get(Analysis, created["id"])
        row.status = "failed"
        row.error = {"code": "source_failed", "message": "forced"}
        row.attempts = 1
        row.max_attempts = 1
    # Keep the row queued after the accepted request so the race is observable.
    monkeypatch.setattr(main_module, "process_analysis_job", lambda *args, **kwargs: None)

    barrier = Barrier(2)

    def retry(_):
        barrier.wait(timeout=10)
        return client.post(f"/api/analyses/{created['id']}/retry", headers=analyst)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(retry, range(2)))

    assert sorted(response.status_code for response in responses) == [202, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "analysis_not_retryable"
    with factory() as session:
        row = session.get(Analysis, created["id"])
        assert row.status == "queued"
        assert row.worker_id is None and row.lease_until is None
        assert row.attempts == 1 and row.max_attempts == 2
        assert session.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "analysis.requeued",
            AuditLog.object_id == created["id"],
        )) == 1


def test_failed_job_left_by_crash_before_finalize_is_recovered(client, monkeypatch):
    now = utcnow()
    add_analysis(
        client, status="running", attempts=1, max_attempts=2,
        worker_id="worker-a", lease_until=now + timedelta(seconds=60),
    )
    factory, engine, registry, settings = (
        client.app.state.factory, client.app.state.engine,
        client.app.state.registry, client.app.state.settings,
    )
    settings.analysis_retry_backoff_seconds = 0

    def fake_run(factory, registry, analysis_id, settings, llm, claimed=False, **kwargs):
        # Mirrors run_analysis persisting failed, then returning.
        with factory.begin() as session:
            row = session.get(Analysis, analysis_id)
            row.status = "failed"
            row.error = {"code": "workflow_failed", "message": "transient"}

    def crash_before_finalize(*args, **kwargs):
        raise SystemExit("process exited before finalize_job")

    monkeypatch.setattr(worker, "run_analysis", fake_run)
    monkeypatch.setattr(worker, "finalize_job", crash_before_finalize)
    with pytest.raises(SystemExit):
        worker.process_claimed_job(
            factory, engine, registry, "job-1", settings, None, "worker-a",
        )

    with factory() as session:
        row = session.get(Analysis, "job-1")
        assert row.status == "failed"
        assert row.worker_id == "worker-a" and row.lease_until is not None

    with factory.begin() as session:
        session.get(Analysis, "job-1").lease_until = now - timedelta(seconds=1)
    assert worker.requeue_expired(factory, engine, now=now, settings=settings) == 1
    with factory() as session:
        row = session.get(Analysis, "job-1")
        assert row.status == "queued"
        assert row.error["retry_scheduled"] is True
        assert row.error["recovered_before_finalize"] is True
        assert row.worker_id is None and row.lease_until is None
        assert row.next_run_at is not None
        assert session.scalar(select(AuditLog).where(
            AuditLog.action == "analysis.retry_scheduled",
            AuditLog.object_id == "job-1",
        )) is not None


def test_recovery_applies_retry_and_dead_letter_rules_to_failed_rows(client):
    now = utcnow()
    expired = now - timedelta(seconds=1)
    add_analysis(
        client, id="retryable", idempotency_key="retryable", status="failed",
        attempts=1, max_attempts=2, worker_id="dead", lease_until=expired,
        error={"code": "workflow_failed", "message": "transient"},
    )
    add_analysis(
        client, id="exhausted", idempotency_key="exhausted", status="failed",
        attempts=2, max_attempts=2, worker_id="dead", lease_until=expired,
        error={"code": "workflow_failed", "message": "still failing"},
    )
    add_analysis(
        client, id="deterministic", idempotency_key="deterministic", status="failed",
        attempts=1, max_attempts=2, worker_id="dead", lease_until=expired,
        error={"code": "llm_failed", "message": "deterministic"},
    )
    factory, engine, settings = (
        client.app.state.factory, client.app.state.engine, client.app.state.settings,
    )
    settings.analysis_retry_backoff_seconds = 0

    assert worker.requeue_expired(factory, engine, now=now, settings=settings) == 3
    with factory() as session:
        retryable = session.get(Analysis, "retryable")
        exhausted = session.get(Analysis, "exhausted")
        deterministic = session.get(Analysis, "deterministic")
        assert retryable.status == "queued"
        assert retryable.error["retry_scheduled"] is True
        assert retryable.error["recovered_before_finalize"] is True
        assert retryable.worker_id is None and retryable.lease_until is None
        for row in (exhausted, deterministic):
            assert row.status == "failed"
            assert row.error["dead_letter"] is True
            assert row.error["recovered_before_finalize"] is True
            assert row.worker_id is None and row.lease_until is None
        assert session.scalar(select(AuditLog).where(
            AuditLog.action == "analysis.retry_scheduled",
            AuditLog.object_id == "retryable",
        )) is not None
        dead_letters = session.scalars(select(AuditLog).where(
            AuditLog.action == "analysis.dead_letter",
            AuditLog.object_id.in_(["exhausted", "deterministic"]),
        )).all()
        assert len(dead_letters) == 2


def test_disabled_worker_leaves_post_and_retry_queued(tmp_path):
    """Disabling the in-process worker must also disable request BackgroundTasks."""
    app = main_module.create_app(Settings(
        database_url=f"sqlite:///{tmp_path / 'disabled.db'}",
        auto_seed=True,
        llm_api_key="",
        analysis_worker_enabled=False,
        analysis_max_attempts=1,
    ))
    assert app.state.inline_jobs is False
    headers = {"Authorization": "Bearer demo-analyst"}
    with TestClient(app) as http:
        created = http.post("/api/analyses", headers=headers, json={})
        assert created.status_code == 202
        detail = http.get(f"/api/analyses/{created.json()['id']}", headers=headers).json()
        assert detail["status"] == "queued", detail
        assert detail["steps"] == []
        assert detail["recommendation_id"] is None

        with app.state.factory.begin() as session:
            session.add(Analysis(
                id="retry-disabled", idempotency_key="retry-disabled", request={},
                actor="demo-analyst", request_id="retry-disabled", status="failed",
                attempts=1, max_attempts=1,
                error={"code": "source_failed", "message": "forced"},
            ))
        retried = http.post("/api/analyses/retry-disabled/retry", headers=headers)
        assert retried.status_code == 202
        body = http.get("/api/analyses/retry-disabled", headers=headers).json()
        assert body["status"] == "queued", body
        assert body["steps"] == []
        assert body["recommendation_id"] is None


def test_worker_cli_once_claims_and_finalizes_one_job(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("PROFITOPS_DATABASE_URL", url)
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("PROFITOPS_LLM_API_KEY", "")
    monkeypatch.setattr(worker, "build_llm_provider", lambda settings: None)

    engine = build_engine(url)
    initialize_database(engine)
    factory = make_session_factory(engine)
    with factory.begin() as session:
        session.add(Analysis(
            id="cli-job", idempotency_key="cli-job", request={},
            actor="demo-analyst", request_id="cli-test", status="queued",
            max_attempts=3,
        ))
    engine.dispose()

    calls = []

    def fake_run(factory, registry, analysis_id, settings, llm, claimed=False, **kwargs):
        calls.append((analysis_id, claimed, kwargs.get("worker_id"), kwargs.get("expected_attempt")))
        with factory.begin() as session:
            row = session.get(Analysis, analysis_id)
            row.status = "completed"
            row.result = {"engine": "cli-test"}

    monkeypatch.setattr(worker, "run_analysis", fake_run)

    assert worker.main(["--once"]) == 0
    output = capsys.readouterr().out
    assert "processed 1 job(s)" in output
    assert calls and calls[0][0] == "cli-job" and calls[0][1] is True

    engine = build_engine(url)
    factory = make_session_factory(engine)
    with factory() as session:
        row = session.get(Analysis, "cli-job")
        assert row.status == "completed"
        assert row.attempts == 1
        assert row.worker_id is None and row.lease_until is None
        assert row.result == {"engine": "cli-test"}
    engine.dispose()
