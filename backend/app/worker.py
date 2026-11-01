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


def _failed_transition(
    row: Analysis, settings: Settings, now: datetime, *,
    recovered_before_finalize=False, error_override: dict | None = None,
) -> dict:
    """Apply the same retry/dead-letter rules as ``finalize_job``.

    ``run_analysis`` writes ``failed`` before ``finalize_job`` clears the
    lease. If the process exits in that window, recovery must make the same
    decision finalize would have made, otherwise a retryable failure stays
    failed forever and a non-retryable one never becomes an explicit dead
    letter.
    """
    error = dict(error_override if error_override is not None else (row.error or {}))
    code = error.get("code")
    retryable = code in RETRYABLE_CODES
    extra = {"recovered_before_finalize": True} if recovered_before_finalize else {}
    if retryable and row.attempts < row.max_attempts:
        delay = settings.analysis_retry_backoff_seconds * (2 ** max(0, row.attempts - 1))
        error = {
            **error, **extra,
            "retry_scheduled": True,
            "attempt": row.attempts,
            "max_attempts": row.max_attempts,
            "retry_in_seconds": delay,
        }
        return {
            "values": {
                "status": "queued",
                "next_run_at": now + timedelta(seconds=delay),
                "error": error,
            },
            "audit_action": "analysis.retry_scheduled",
            "audit_outcome": "degraded",
            "metric_event": "retry_scheduled",
            "metric_code": code or "unknown",
            "retryable": retryable,
        }
    error = {
        **error, **extra,
        "dead_letter": True,
        "attempt": row.attempts,
        "max_attempts": row.max_attempts,
    }
    return {
        "values": {
            "status": "failed",
            "next_run_at": None,
            "error": error,
        },
        "audit_action": "analysis.dead_letter",
        "audit_outcome": "failure",
        "metric_event": "dead_letter",
        "metric_code": code or "unknown",
        "retryable": retryable,
    }


def _recover_failed_jobs(session, now: datetime, settings: Settings) -> tuple[int, int]:
    """Recover failed rows whose worker died before finalize cleared the lease."""
    rows = session.scalars(
        select(Analysis).where(
            Analysis.status == "failed",
            Analysis.worker_id.is_not(None),
            or_(Analysis.lease_until.is_(None), Analysis.lease_until <= now),
        )
    ).all()
    requeued = 0
    dead_lettered = 0
    for row in rows:
        transition = _failed_transition(
            row, settings, now, recovered_before_finalize=True,
        )
        values = {
            **transition["values"],
            "lease_until": None,
            "worker_id": None,
            "updated_at": now,
        }
        changed = session.execute(
            update(Analysis)
            .where(
                Analysis.id == row.id,
                Analysis.status == "failed",
                Analysis.worker_id == row.worker_id,
                Analysis.attempts == row.attempts,
                or_(Analysis.lease_until.is_(None), Analysis.lease_until <= now),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            continue
        details = {
            "reason": "failed_before_finalize",
            "attempt": row.attempts,
            "max_attempts": row.max_attempts,
            "code": transition["metric_code"],
            "retryable": transition["retryable"],
        }
        if transition["values"]["status"] == "queued":
            details["retry_in_seconds"] = values["error"]["retry_in_seconds"]
            requeued += 1
        else:
            dead_lettered += 1
        record(
            session, "system", transition["audit_action"], "analysis", row.id,
            row.request_id, transition["audit_outcome"], details,
            tenant_id=row.tenant_id,
        )
        METRICS.inc(
            "analysis_jobs_total", event=transition["metric_event"],
            code=transition["metric_code"],
        )
    return requeued, dead_lettered


def requeue_expired(factory, engine, now: datetime | None = None, settings: Settings | None = None) -> int:
    """Return expired jobs to the queue, or dead-letter exhausted attempts.

    A job that has already used every allowed attempt must not be requeued
    forever after repeated worker crashes. Each transition is a conditional
    UPDATE so a heartbeat that extended the lease after the scan is not
    overwritten by a stale recovery decision. Failed rows that still carry a
    worker lease are included: they are the crash window between
    ``run_analysis`` writing ``failed`` and ``finalize_job`` clearing it.
    """
    now = now or utcnow()
    settings = settings or Settings()
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
        failed_requeued, failed_dead_lettered = _recover_failed_jobs(session, now, settings)
        if requeued:
            METRICS.inc("analysis_jobs_total", event="recovered", value=requeued)
        if dead_lettered:
            METRICS.inc("analysis_jobs_total", event="dead_letter", code="lease_expired", value=dead_lettered)
    return requeued + dead_lettered + failed_requeued + failed_dead_lettered


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
        values: dict = {"lease_until": None, "worker_id": None, "updated_at": now}
        audit_action: str | None = None
        audit_outcome = "success"
        metric_event: str | None = None
        metric_code: str | None = None
        retryable = False

        if status == "running":
            # The workflow returned without persisting a terminal state. Do not
            # leave a phantom running row that no worker can ever finish.
            transition = _failed_transition(
                row, settings, now,
                error_override={
                    "code": "worker_crashed",
                    "message": "分析任务在未写入终态的情况下结束",
                },
            )
        elif status == "failed":
            transition = _failed_transition(row, settings, now)
        else:
            transition = None

        if transition is None:
            values["next_run_at"] = None
        else:
            values.update(transition["values"])
            status = transition["values"]["status"]
            audit_action = transition["audit_action"]
            audit_outcome = transition["audit_outcome"]
            metric_event = transition["metric_event"]
            metric_code = transition["metric_code"]
            retryable = transition["retryable"]

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
                {"attempt": row.attempts, "max_attempts": row.max_attempts, "retry_in_seconds": values["error"]["retry_in_seconds"], "code": metric_code},
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
    requeue_expired(factory, engine, settings=settings)
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
