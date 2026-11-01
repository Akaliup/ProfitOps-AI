"""Durable analysis job runtime.

The API persists a queued ``Analysis`` row before any work starts. A worker
claims it with a conditional UPDATE, keeps a lease alive while the LangGraph
workflow runs, and requeues expired leases after a crash. Retryable failures
are scheduled with exponential backoff; exhausted jobs stay failed and are
marked as dead letters. Only the simulation workflow is executed here.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
from datetime import datetime, timedelta
from time import perf_counter

from sqlalchemy import func, or_, select, update

from .audit import record
from .config import Settings
from .db import begin_write
from .errors import LeaseLost
from .models import Analysis, utcnow
from .observability import METRICS
from .workflow import run_analysis

logger = logging.getLogger("profitops.worker")

RETRYABLE_CODES = frozenset({
    "workflow_failed",
    "step_timeout",
    "database_locked",
    "worker_crashed",
})


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


def _claim_values(settings: Settings, worker_id: str, now: datetime) -> dict:
    return {
        "status": "running",
        "attempts": Analysis.attempts + 1,
        "worker_id": worker_id,
        "lease_until": now + timedelta(seconds=settings.analysis_lease_seconds),
        "next_run_at": None,
        "error": None,
        "updated_at": now,
    }


def claim_job(factory, engine, analysis_id: str, settings: Settings, worker_id: str, now: datetime | None = None) -> bool:
    """Atomically claim one specific queued analysis."""
    now = now or utcnow()
    with factory.begin() as session:
        if engine is not None:
            begin_write(session, engine)
        claimed = session.execute(
            update(Analysis)
            .where(Analysis.id == analysis_id, Analysis.status == "queued")
            .values(**_claim_values(settings, worker_id, now))
        )
        if claimed.rowcount != 1:
            return False
        row = session.get(Analysis, analysis_id)
        record(
            session, "system", "analysis.claimed", "analysis", analysis_id,
            row.request_id, "success",
            {"worker_id": worker_id, "attempt": row.attempts, "max_attempts": row.max_attempts},
            tenant_id=row.tenant_id,
        )
        METRICS.inc("analysis_jobs_total", event="claimed")
        return True


def claim_next_job(factory, engine, settings: Settings, worker_id: str, now: datetime | None = None) -> str | None:
    """Pick the oldest due queued job and claim it with a conditional UPDATE."""
    now = now or utcnow()
    with factory.begin() as session:
        if engine is not None:
            begin_write(session, engine)
        candidate = session.execute(
            select(Analysis.id)
            .where(
                Analysis.status == "queued",
                or_(Analysis.next_run_at.is_(None), Analysis.next_run_at <= now),
            )
            .order_by(Analysis.created_at.asc(), Analysis.id.asc())
            .limit(1)
        ).scalar_one_or_none()
        if candidate is None:
            return None
        claimed = session.execute(
            update(Analysis)
            .where(Analysis.id == candidate, Analysis.status == "queued")
            .values(**_claim_values(settings, worker_id, now))
        )
        if claimed.rowcount != 1:
            return None
        row = session.get(Analysis, candidate)
        record(
            session, "system", "analysis.claimed", "analysis", candidate,
            row.request_id, "success",
            {"worker_id": worker_id, "attempt": row.attempts, "max_attempts": row.max_attempts},
            tenant_id=row.tenant_id,
        )
        METRICS.inc("analysis_jobs_total", event="claimed")
        return candidate


def requeue_expired(factory, engine, now: datetime | None = None) -> int:
    """Return expired jobs to the queue, or dead-letter exhausted attempts.

    A job that has already used every allowed attempt must not be requeued
    forever after repeated worker crashes. Each transition is a conditional
    UPDATE so a heartbeat that extended the lease after the scan is not
    overwritten by a stale recovery decision.
    """
    now = now or utcnow()
    requeued = 0
    dead_lettered = 0
    with factory.begin() as session:
        if engine is not None:
            begin_write(session, engine)
        rows = session.scalars(
            select(Analysis).where(
                Analysis.status == "running",
                or_(Analysis.lease_until.is_(None), Analysis.lease_until <= now),
            )
        ).all()
        for row in rows:
            row_id = row.id
            request_id = row.request_id
            tenant_id = row.tenant_id
            attempts = row.attempts
            max_attempts = row.max_attempts
            owner = row.worker_id
            exhausted = attempts >= max_attempts
            if exhausted:
                values = {
                    "status": "failed",
                    "next_run_at": None,
                    "lease_until": None,
                    "worker_id": None,
                    "updated_at": now,
                    "error": {
                        "code": "lease_expired",
                        "message": "执行租约连续过期，任务已进入死信",
                        "dead_letter": True,
                        "attempt": attempts,
                        "max_attempts": max_attempts,
                    },
                }
            else:
                values = {
                    "status": "queued",
                    "next_run_at": now,
                    "lease_until": None,
                    "worker_id": None,
                    "updated_at": now,
                    "error": {
                        "code": "lease_expired",
                        "message": "执行租约已过期，任务重新排队",
                        "attempt": attempts,
                        "max_attempts": max_attempts,
                    },
                }
            changed = session.execute(
                update(Analysis)
                .where(
                    Analysis.id == row_id,
                    Analysis.status == "running",
                    Analysis.attempts == attempts,
                    Analysis.worker_id == owner,
                    or_(Analysis.lease_until.is_(None), Analysis.lease_until <= now),
                )
                .values(**values)
                # SQLite returns naive datetimes while ``now`` is aware; do not
                # ask the ORM to evaluate the lease predicate in Python.
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                continue
            if exhausted:
                record(
                    session, "system", "analysis.dead_letter", "analysis", row_id,
                    request_id, "failure",
                    {
                        "attempt": attempts,
                        "max_attempts": max_attempts,
                        "code": "lease_expired",
                        "retryable": False,
                    },
                    tenant_id=tenant_id,
                )
                dead_lettered += 1
            else:
                record(
                    session, "system", "analysis.recovered", "analysis", row_id,
                    request_id, "degraded",
                    {
                        "reason": "lease_expired",
                        "attempts": attempts,
                        "max_attempts": max_attempts,
                    },
                    tenant_id=tenant_id,
                )
                requeued += 1
        if requeued:
            METRICS.inc("analysis_jobs_total", event="recovered", value=requeued)
        if dead_lettered:
            METRICS.inc("analysis_jobs_total", event="dead_letter", code="lease_expired", value=dead_lettered)
    return requeued + dead_lettered


class LeaseHeartbeat:
    """Extend a running job's lease while its workflow is still executing."""

    def __init__(
        self, factory, analysis_id: str, worker_id: str, lease_seconds: float,
        attempt: int | None = None,
    ):
        self.factory = factory
        self.analysis_id = analysis_id
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.attempt = attempt
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="analysis-heartbeat", daemon=True)

    def _run(self):
        interval = max(1.0, self.lease_seconds / 3)
        while not self._stop.wait(interval):
            try:
                with self.factory.begin() as session:
                    stmt = update(Analysis).where(
                        Analysis.id == self.analysis_id,
                        Analysis.status == "running",
                        Analysis.worker_id == self.worker_id,
                    )
                    if self.attempt is not None:
                        stmt = stmt.where(Analysis.attempts == self.attempt)
                    session.execute(
                        stmt.values(lease_until=utcnow() + timedelta(seconds=self.lease_seconds))
                    )
            except Exception:
                logger.warning(
                    "lease_heartbeat_failed",
                    extra={"analysis_id": self.analysis_id, "worker_id": self.worker_id},
                    exc_info=True,
                )

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        self._thread.join(timeout=2)
        return False


def finalize_job(
    factory, engine, analysis_id: str, settings: Settings, now: datetime | None = None,
    worker_id: str | None = None, attempt: int | None = None,
) -> str | None:
    """Clear the lease and schedule a retry or dead-letter a failed job.

    When ``worker_id``/``attempt`` are provided, the write is fenced: a worker
    whose lease expired and whose job was reclaimed cannot finalize the newer
    worker's run.
    """
    now = now or utcnow()
    with factory.begin() as session:
        if engine is not None:
            begin_write(session, engine)
        row = session.get(Analysis, analysis_id)
        if row is None:
            return None
        if worker_id is not None and (
            row.worker_id != worker_id or (attempt is not None and row.attempts != attempt)
        ):
            return None

        old_status = row.status
        status = old_status
        error = dict(row.error or {})
        if status == "running":
            # The workflow returned without persisting a terminal state. Do not
            # leave a phantom running row that no worker can ever finish.
            status = "failed"
            error = {
                "code": "worker_crashed",
                "message": "分析任务在未写入终态的情况下结束",
            }

        values: dict = {"lease_until": None, "worker_id": None, "updated_at": now}
        audit_action: str | None = None
        audit_outcome = "success"
        metric_event: str | None = None
        metric_code: str | None = None

        if status != "failed":
            values["next_run_at"] = None
        else:
            code = error.get("code")
            retryable = code in RETRYABLE_CODES
            if retryable and row.attempts < row.max_attempts:
                delay = settings.analysis_retry_backoff_seconds * (2 ** max(0, row.attempts - 1))
                status = "queued"
                error = {
                    **error,
                    "retry_scheduled": True,
                    "attempt": row.attempts,
                    "max_attempts": row.max_attempts,
                    "retry_in_seconds": delay,
                }
                values.update(status=status, next_run_at=now + timedelta(seconds=delay), error=error)
                audit_action = "analysis.retry_scheduled"
                audit_outcome = "degraded"
                metric_event = "retry_scheduled"
                metric_code = code or "unknown"
            else:
                error = {
                    **error,
                    "dead_letter": True,
                    "attempt": row.attempts,
                    "max_attempts": row.max_attempts,
                }
                values.update(status="failed", error=error)
                audit_action = "analysis.dead_letter"
                audit_outcome = "failure"
                metric_event = "dead_letter"
                metric_code = code or "unknown"

        stmt = update(Analysis).where(
            Analysis.id == analysis_id, Analysis.status == old_status,
        )
        if worker_id is not None:
            stmt = stmt.where(Analysis.worker_id == worker_id)
        if attempt is not None:
            stmt = stmt.where(Analysis.attempts == attempt)
        finalized = session.execute(stmt.values(**values))
        if finalized.rowcount != 1:
            return None

        if audit_action is None:
            METRICS.inc("analysis_jobs_total", event="finished", status=status)
            return status

        if audit_action == "analysis.retry_scheduled":
            record(
                session, "system", audit_action, "analysis", analysis_id,
                row.request_id, audit_outcome,
                {"attempt": row.attempts, "max_attempts": row.max_attempts, "retry_in_seconds": error["retry_in_seconds"], "code": metric_code},
                tenant_id=row.tenant_id,
            )
        else:
            record(
                session, "system", audit_action, "analysis", analysis_id,
                row.request_id, audit_outcome,
                {"attempt": row.attempts, "max_attempts": row.max_attempts, "code": metric_code, "retryable": retryable},
                tenant_id=row.tenant_id,
            )
        METRICS.inc("analysis_jobs_total", event=metric_event, code=metric_code)
        return status


def process_claimed_job(factory, engine, registry, analysis_id: str, settings: Settings, llm, worker_id: str):
    """Run an already-claimed job and finalize its queue state."""
    started = perf_counter()
    final_status: str | None = None
    attempt: int | None = None
    owned = False
    try:
        with factory() as session:
            row = session.get(Analysis, analysis_id)
            if row is not None and row.status == "running" and row.worker_id == worker_id:
                attempt = row.attempts
                owned = True
        if not owned:
            return None
        with LeaseHeartbeat(
            factory, analysis_id, worker_id, settings.analysis_lease_seconds, attempt=attempt,
        ):
            try:
                run_analysis(
                    factory, registry, analysis_id, settings, llm, claimed=True,
                    worker_id=worker_id, expected_attempt=attempt,
                )
            except LeaseLost:
                logger.warning(
                    "analysis_lease_lost",
                    extra={"analysis_id": analysis_id, "worker_id": worker_id, "attempt": attempt},
                )
            except Exception as exc:
                logger.exception(
                    "analysis_worker_crashed",
                    extra={"analysis_id": analysis_id, "worker_id": worker_id},
                )
                with factory.begin() as session:
                    failed = session.execute(
                        update(Analysis)
                        .where(
                            Analysis.id == analysis_id,
                            Analysis.status == "running",
                            Analysis.worker_id == worker_id,
                            Analysis.attempts == attempt,
                        )
                        .values(
                            status="failed",
                            error={
                                "code": "worker_crashed",
                                "message": "分析任务执行器异常终止",
                                "exception_type": type(exc).__name__,
                            },
                            updated_at=utcnow(),
                        )
                    )
                    if failed.rowcount == 1:
                        row = session.get(Analysis, analysis_id)
                        record(
                            session, "system", "analysis.failed", "analysis", analysis_id,
                            row.request_id, "failure", row.error,
                            tenant_id=row.tenant_id,
                        )
    finally:
        if owned:
            final_status = finalize_job(
                factory, engine, analysis_id, settings,
                worker_id=worker_id, attempt=attempt,
            )
            METRICS.observe(
                "analysis_job_duration_seconds", perf_counter() - started,
                status=final_status or "unknown",
            )


def process_analysis_job(factory, engine, registry, analysis_id: str, settings: Settings, llm, worker_id: str | None = None):
    """Claim and run one specific job. Used by the HTTP BackgroundTasks path."""
    worker_id = worker_id or worker_identity()
    if not claim_job(factory, engine, analysis_id, settings, worker_id):
        return None
    return process_claimed_job(factory, engine, registry, analysis_id, settings, llm, worker_id)


def refresh_queue_depth(factory, settings: Settings) -> None:
    """Publish the durable queue depth after a worker poll."""
    if not settings.metrics_enabled:
        return
    with factory() as session:
        queued = session.scalar(
            select(func.count()).select_from(Analysis).where(Analysis.status == "queued")
        ) or 0
        running = session.scalar(
            select(func.count()).select_from(Analysis).where(Analysis.status == "running")
        ) or 0
    METRICS.set_gauge("analysis_queue_depth", queued, state="queued")
    METRICS.set_gauge("analysis_queue_depth", running, state="running")


def run_due_jobs(factory, engine, registry, settings: Settings, llm, worker_id: str | None = None, limit: int = 1) -> int:
    """Requeue expired leases, then process up to ``limit`` due jobs."""
    worker_id = worker_id or worker_identity()
    requeue_expired(factory, engine)
    processed = 0
    for _ in range(limit):
        analysis_id = claim_next_job(factory, engine, settings, worker_id)
        if analysis_id is None:
            break
        process_claimed_job(factory, engine, registry, analysis_id, settings, llm, worker_id)
        processed += 1
    refresh_queue_depth(factory, settings)
    return processed


class AnalysisWorker:
    """Small in-process worker loop. Run one process-wide instance in lifespan."""

    def __init__(self, factory, engine, registry, settings: Settings, llm):
        self.factory = factory
        self.engine = engine
        self.registry = registry
        self.settings = settings
        self.llm = llm
        self.worker_id = worker_identity()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="analysis-worker", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)

    def run_once(self) -> int:
        return run_due_jobs(
            self.factory, self.engine, self.registry, self.settings, self.llm,
            worker_id=self.worker_id, limit=1,
        )

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("analysis_worker_loop_failed")
            self._stop.wait(self.settings.analysis_worker_poll_seconds)
