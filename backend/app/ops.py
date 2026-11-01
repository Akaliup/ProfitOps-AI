"""Tenant-scoped operational summary for the Agent ops page.

The summary is built from durable database rows only: analysis jobs, the
persisted LLM/tool trace, recommendations, executions and audit events.
Process-local Prometheus counters are intentionally excluded, so a restart
never makes the page look like historical data disappeared.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Analysis, AuditLog, Execution, Recommendation, utcnow
from .pricing import PRICING_SOURCE, PRICING_VERSION, estimate_cost_usd
from .views import iso_utc

JOB_STATUSES = ("queued", "running", "waiting_approval", "completed", "insufficient_data", "failed")
LLM_STATUSES = ("llm_success", "deterministic_fallback", "llm_failed", "not_requested")
RETRY_AUDIT_ACTIONS = ("analysis.retry_scheduled",)
DEAD_LETTER_AUDIT_ACTIONS = ("analysis.dead_letter",)


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _llm_run(row: Analysis) -> dict[str, Any] | None:
    result = row.result if isinstance(row.result, dict) else {}
    run = result.get("llm")
    return run if isinstance(run, dict) else None


def _dead_letter(row: Analysis) -> bool:
    error = row.error if isinstance(row.error, dict) else {}
    return row.status == "failed" and error.get("dead_letter") is True


def build_ops_summary(
    session: Session, tenant_id: str, *, window_hours: int = 24, now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate real records for one tenant.

    Every query is filtered by ``tenant_id``. The caller is responsible for
    the role check; this function never widens scope on its own.
    """
    end = now or utcnow()
    start = end - timedelta(hours=window_hours)
    rows = session.scalars(
        select(Analysis)
        .where(Analysis.tenant_id == tenant_id, Analysis.created_at >= start)
        .order_by(Analysis.created_at.desc())
    ).all()

    status_counts = Counter(row.status for row in rows)
    error_codes: Counter[str] = Counter()
    retry_count = 0
    dead_letter_count = 0
    llm_counts: Counter[str] = Counter()
    tool_calls = 0
    failed_tool_calls = 0
    tool_names: Counter[str] = Counter()
    failed_tool_names: Counter[str] = Counter()
    tool_rounds = 0
    termination_reasons: Counter[str] = Counter()
    native_runs = 0
    token_totals = {"prompt": 0, "completion": 0, "total": 0, "cached": 0}
    runs_with_usage = 0
    runs_without_usage = 0
    priced_runs = 0
    unpriced_runs = 0
    cost_total = 0.0
    pricing_versions: set[str] = set()
    recent_failures: list[dict[str, Any]] = []

    for row in rows:
        retry_count += max(0, row.attempts - 1)
        error = row.error if isinstance(row.error, dict) else {}
        if row.status == "failed":
            code = error.get("code")
            if isinstance(code, str) and code:
                error_codes[code] += 1
            if _dead_letter(row):
                dead_letter_count += 1
            if len(recent_failures) < 10:
                recent_failures.append({
                    "id": row.id,
                    "status": row.status,
                    "request_id": row.request_id,
                    "attempts": row.attempts,
                    "max_attempts": row.max_attempts,
                    "dead_letter": _dead_letter(row),
                    "error_code": code if isinstance(code, str) else None,
                    "error_message": error.get("message") if isinstance(error.get("message"), str) else None,
                    "updated_at": iso_utc(row.updated_at),
                    "created_at": iso_utc(row.created_at),
                })

        run = _llm_run(row)
        if run is None:
            continue
        status = run.get("status")
        if isinstance(status, str):
            llm_counts[status] += 1
        if run.get("native_tools") is True:
            native_runs += 1
        reason = run.get("termination_reason")
        if isinstance(reason, str) and reason:
            termination_reasons[reason] += 1

        calls = run.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                tool_calls += 1
                name = call.get("name") if isinstance(call.get("name"), str) else "unknown"
                tool_names[name] += 1
                if call.get("ok") is False:
                    failed_tool_calls += 1
                    failed_tool_names[name] += 1
        rounds = run.get("rounds")
        if isinstance(rounds, list):
            tool_rounds += len(rounds)

        usage = run.get("usage")
        if not isinstance(usage, dict):
            runs_without_usage += 1
            unpriced_runs += 1
            continue
        prompt = _number(usage.get("prompt_tokens"))
        completion = _number(usage.get("completion_tokens"))
        total = _number(usage.get("total_tokens"))
        cached = _number(usage.get("cached_tokens")) or 0
        if prompt is None and completion is None and total is None:
            runs_without_usage += 1
            unpriced_runs += 1
            continue
        runs_with_usage += 1
        token_totals["prompt"] += prompt or 0
        token_totals["completion"] += completion or 0
        token_totals["total"] += total if total is not None else (prompt or 0) + (completion or 0)
        token_totals["cached"] += cached
        estimate = estimate_cost_usd(run.get("model"), usage)
        if estimate is None:
            unpriced_runs += 1
        else:
            priced_runs += 1
            cost_total += float(estimate["amount_usd"])
            pricing_versions.add(str(estimate["pricing_version"]))

    if priced_runs == 0:
        cost_status = "partial" if unpriced_runs else "no_data"
    elif unpriced_runs:
        cost_status = "partial"
    else:
        cost_status = "complete"
    cost = {
        "status": cost_status,
        "currency": "USD",
        "amount_usd": round(cost_total, 6) if priced_runs else None,
        "priced_runs": priced_runs,
        "unpriced_runs": unpriced_runs,
        "pricing_version": pricing_versions.pop() if len(pricing_versions) == 1 else None,
        "source": PRICING_SOURCE,
        "note": (
            "Public list-price estimate from persisted token usage, not an invoice. "
            "A missing usage or price is reported as unpriced, never as zero cost."
        ),
    }

    retry_events = session.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.created_at >= start,
            AuditLog.action.in_(RETRY_AUDIT_ACTIONS),
        )
    ) or 0
    dead_letter_events = session.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.created_at >= start,
            AuditLog.action.in_(DEAD_LETTER_AUDIT_ACTIONS),
        )
    ) or 0

    recommendation_counts = dict(session.execute(
        select(Recommendation.status, func.count())
        .where(Recommendation.tenant_id == tenant_id)
        .group_by(Recommendation.status)
    ).all())
    execution_counts = dict(session.execute(
        select(Execution.status, func.count())
        .where(Execution.tenant_id == tenant_id)
        .group_by(Execution.status)
    ).all())

    return {
        "tenant_id": tenant_id,
        "generated_at": iso_utc(end),
        "window": {
            "hours": window_hours,
            "start": iso_utc(start),
            "end": iso_utc(end),
            "timezone": "UTC",
        },
        "source": {
            "kind": "database",
            "tables": ["analyses", "recommendations", "executions", "audit_logs"],
            "note": (
                "Counts come from durable tenant-scoped rows. Process-local Prometheus "
                "metrics are not presented as persistent history."
            ),
        },
        "jobs": {
            "total": len(rows),
            "by_status": {status: status_counts.get(status, 0) for status in JOB_STATUSES},
            "retry_count": retry_count,
            "retry_scheduled_events": retry_events,
            "dead_letter": dead_letter_count,
            "dead_letter_events": dead_letter_events,
            "error_codes": [
                {"code": code, "count": count} for code, count in error_codes.most_common()
            ],
        },
        "llm": {
            "runs": sum(llm_counts.values()),
            "by_status": {status: llm_counts.get(status, 0) for status in LLM_STATUSES},
            "native_tool_runs": native_runs,
            "termination_reasons": [
                {"reason": reason, "count": count}
                for reason, count in termination_reasons.most_common()
            ],
        },
        "tools": {
            "calls": tool_calls,
            "failed_calls": failed_tool_calls,
            "rounds": tool_rounds,
            "by_name": [
                {"name": name, "calls": count, "failed": failed_tool_names.get(name, 0)}
                for name, count in tool_names.most_common()
            ],
        },
        "tokens": {
            "runs_with_usage": runs_with_usage,
            "runs_without_usage": runs_without_usage,
            "prompt": token_totals["prompt"] if runs_with_usage else None,
            "completion": token_totals["completion"] if runs_with_usage else None,
            "total": token_totals["total"] if runs_with_usage else None,
            "cached": token_totals["cached"] if runs_with_usage else None,
        },
        "cost": cost,
        "approvals": {
            "current_state": True,
            "by_status": {
                status: recommendation_counts.get(status, 0)
                for status in ("pending_approval", "approved", "rejected", "executed", "execution_failed")
            },
        },
        "executions": {
            "current_state": True,
            "total": sum(execution_counts.values()),
            "by_status": dict(sorted(execution_counts.items())),
        },
        "recent_failures": recent_failures,
        "pricing": {"version": PRICING_VERSION, "source": PRICING_SOURCE},
        "notes": [
            "Window filters analysis jobs by created_at; approvals and executions show current state.",
            "Tool arguments and raw prompts are not returned; only names and counts.",
        ],
    }
