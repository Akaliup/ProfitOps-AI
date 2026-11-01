from datetime import timedelta

from sqlalchemy import delete, select

from app.models import Analysis, AuditLog, Recommendation, utcnow
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
