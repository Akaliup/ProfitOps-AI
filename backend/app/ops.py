"""Tenant-scoped operational summary for the Agent ops page.

The summary is built from durable database rows only: analysis jobs, the
persisted LLM/tool audit events, recommendations, executions and audit events.
Process-local Prometheus counters are intentionally excluded, so a restart
never makes the page look like historical data disappeared.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .models import Analysis, AuditLog, Execution, Recommendation, utcnow
from .pricing import PRICING_SOURCE, PRICING_VERSION, estimate_cost_usd
from .views import iso_utc

JOB_STATUSES = ("queued", "running", "waiting_approval", "completed", "insufficient_data", "failed")
LLM_STATUSES = ("llm_success", "deterministic_fallback", "llm_failed", "not_requested")
LLM_AUDIT_ACTIONS = ("analysis.llm_succeeded", "analysis.llm_fallback", "analysis.llm_failed")
RETRY_AUDIT_ACTIONS = ("analysis.retry_scheduled",)
DEAD_LETTER_AUDIT_ACTIONS = ("analysis.dead_letter",)


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _result_run(result: Any) -> dict[str, Any] | None:
    """Return the latest persisted LLM run from an analysis result."""
    if not isinstance(result, dict):
        return None
    run = result.get("llm")
    return run if isinstance(run, dict) else None


def _is_model_call(run: dict[str, Any]) -> bool:
    """Whether a persisted LLM record represents an actual provider call.

    ``not_requested`` jobs never reach the LLM node. A fallback created with
    ``configured=false`` (or ``reason=not_configured``) is also not a call, so
    it must not enter token/cost denominators. A failed run with no attempts,
    no usage and no native rounds failed before any provider request.
    """
    status = run.get("status")
    if status == "not_requested":
        return False
    if run.get("configured") is False:
        return False
    if status == "deterministic_fallback" and run.get("reason") == "not_configured":
        return False
    if status == "llm_success":
        return True
    attempts = _number(run.get("attempts")) or 0
    rounds = run.get("rounds")
    has_rounds = isinstance(rounds, list) and bool(rounds)
    if attempts == 0 and run.get("usage") is None and not has_rounds:
        return False
    return True


def _call_usages(run: dict[str, Any]) -> list[Any]:
    """Return one usage value per logical provider call.

    Native function calling issues one provider request per tool round; each
    round carries its own usage. Non-native runs issue one request. A native
    run without persisted rounds is treated as one call for legacy records.
    """
    if run.get("native_tools") is True:
        rounds = run.get("rounds")
        if isinstance(rounds, list) and rounds:
            return [
                item.get("usage") if isinstance(item, dict) else None
                for item in rounds
            ]
    return [run.get("usage")]


def _provider_request_count(run: dict[str, Any], logical_calls: int) -> int:
    """Actual HTTP requests, including retries inside the provider client.

    ``attempts`` is persisted as the sum of each provider request's HTTP
    attempts. It can exceed the number of logical calls when the provider
    retries a transport or retryable HTTP error. Older records may have no
    attempts, so the logical call count is the floor.
    """
    attempts = _number(run.get("attempts")) or 0
    if run.get("native_tools") is True:
        return max(attempts, logical_calls)
    return max(attempts, 1)


def _same_model_call(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Match the success audit and its terminal fallback for one model call.

    ``verify_llm`` audits a successful call before evidence validation and then
    audits a fallback/failure for the same call when references are invalid.
    The terminal event keeps the same usage and trace, so those two audit rows
    describe one model call, not two.
    """
    keys = (
        "configured", "provider", "model", "attempts", "latency_ms", "usage",
        "native_tools", "termination_reason", "rounds", "tool_calls",
    )
    return all(first.get(key) == second.get(key) for key in keys)


def _dead_letter(error: Any) -> bool:
    return isinstance(error, dict) and error.get("dead_letter") is True


def build_ops_summary(
    session: Session, tenant_id: str, *, window_hours: int = 24, now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate real records for one tenant.

    Every query is filtered by ``tenant_id``. The caller is responsible for
    the role check; this function never widens scope on its own.
    """
    end = now or utcnow()
    start = end - timedelta(hours=window_hours)
    base_filter = (
        Analysis.tenant_id == tenant_id,
        Analysis.created_at >= start,
        Analysis.created_at <= end,
    )

    # Base job metrics are computed by the database; no full Analysis ORM rows
    # are loaded for the window.
    total_jobs = int(session.scalar(
        select(func.count()).select_from(Analysis).where(*base_filter)
    ) or 0)
    status_counts = Counter(dict(session.execute(
        select(Analysis.status, func.count()).where(*base_filter).group_by(Analysis.status)
    ).all()))
    retry_count = int(session.scalar(
        select(func.coalesce(
            func.sum(case((Analysis.attempts > 1, Analysis.attempts - 1), else_=0)), 0,
        )).where(*base_filter)
    ) or 0)

    error_codes: Counter[str] = Counter()
    dead_letter_count = 0
    recent_failures: list[dict[str, Any]] = []

    # Stream only the columns needed for failure counts and the bounded recent
    # list. The list is capped at 10, but counts still cover the whole window.
    failed_stmt = (
        select(
            Analysis.id, Analysis.status, Analysis.request_id, Analysis.attempts,
            Analysis.max_attempts, Analysis.error, Analysis.created_at, Analysis.updated_at,
        )
        .where(*base_filter, Analysis.status == "failed")
        .order_by(Analysis.created_at.desc(), Analysis.id.desc())
    )
    for row in session.execute(failed_stmt).yield_per(200):
        error = row.error if isinstance(row.error, dict) else {}
        code = error.get("code")
        if isinstance(code, str) and code:
            error_codes[code] += 1
        if _dead_letter(error):
            dead_letter_count += 1
        if len(recent_failures) < 10:
            recent_failures.append({
                "id": row.id,
                "status": row.status,
                "request_id": row.request_id,
                "attempts": row.attempts,
                "max_attempts": row.max_attempts,
                "dead_letter": _dead_letter(error),
                "error_code": code if isinstance(code, str) else None,
                "error_message": error.get("message") if isinstance(error.get("message"), str) else None,
                "updated_at": iso_utc(row.updated_at),
                "created_at": iso_utc(row.created_at),
            })

    llm_counts: Counter[str] = Counter()
    tool_names: Counter[str] = Counter()
    failed_tool_names: Counter[str] = Counter()
    termination_reasons: Counter[str] = Counter()
    token_totals = {"prompt": 0, "completion": 0, "total": 0, "cached": 0}
    pricing_versions: set[str] = set()
    llm_stats = {
        "runs": 0,
        "model_calls": 0,
        "provider_requests": 0,
        "provider_retries": 0,
        "no_model_call_runs": 0,
        "native_runs": 0,
        "tool_calls": 0,
        "failed_tool_calls": 0,
        "tool_rounds": 0,
        "runs_with_usage": 0,
        "runs_without_usage": 0,
        "priced_runs": 0,
        "unpriced_runs": 0,
        "priced_calls": 0,
        "unpriced_calls": 0,
        "cost_total": 0.0,
    }

    def consume_run(run: dict[str, Any]) -> None:
        """Aggregate one logical LLM record, including no-call records."""
        llm_stats["runs"] += 1
        status = run.get("status")
        if isinstance(status, str) and status:
            llm_counts[status] += 1
        if not _is_model_call(run):
            llm_stats["no_model_call_runs"] += 1
            return

        call_usages = _call_usages(run)
        logical_calls = len(call_usages)
        llm_stats["model_calls"] += logical_calls
        provider_requests = _provider_request_count(run, logical_calls)
        llm_stats["provider_requests"] += provider_requests
        llm_stats["provider_retries"] += max(0, provider_requests - logical_calls)

        if run.get("native_tools") is True:
            llm_stats["native_runs"] += 1
        reason = run.get("termination_reason")
        if isinstance(reason, str) and reason:
            termination_reasons[reason] += 1

        calls = run.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                llm_stats["tool_calls"] += 1
                name = call.get("name") if isinstance(call.get("name"), str) else "unknown"
                tool_names[name] += 1
                if call.get("ok") is False:
                    llm_stats["failed_tool_calls"] += 1
                    failed_tool_names[name] += 1
        rounds = run.get("rounds")
        if isinstance(rounds, list):
            llm_stats["tool_rounds"] += len(rounds)

        # Token totals use the aggregate persisted usage. Cost is computed per
        # logical call below, so a missing round can never be hidden by the
        # known tokens of another round.
        usage = run.get("usage")
        prompt = completion = total = None
        cached = 0
        if isinstance(usage, dict):
            prompt = _number(usage.get("prompt_tokens"))
            completion = _number(usage.get("completion_tokens"))
            total = _number(usage.get("total_tokens"))
            cached = _number(usage.get("cached_tokens")) or 0
        if prompt is None and completion is None and total is None:
            llm_stats["runs_without_usage"] += 1
        else:
            llm_stats["runs_with_usage"] += 1
            token_totals["prompt"] += prompt or 0
            token_totals["completion"] += completion or 0
            token_totals["total"] += total if total is not None else (prompt or 0) + (completion or 0)
            token_totals["cached"] += cached

        run_unpriced = 0
        for call_usage in call_usages:
            estimate = (
                estimate_cost_usd(run.get("model"), call_usage)
                if isinstance(call_usage, dict) else None
            )
            if estimate is None:
                run_unpriced += 1
                llm_stats["unpriced_calls"] += 1
                continue
            llm_stats["priced_calls"] += 1
            llm_stats["cost_total"] += float(estimate["amount_usd"])
            pricing_versions.add(str(estimate["pricing_version"]))
        if run_unpriced:
            llm_stats["unpriced_runs"] += 1
        else:
            llm_stats["priced_runs"] += 1

    # LLM history comes from durable audit events. A success event followed by
    # a terminal event for the same call (invalid evidence references) is
    # collapsed into the terminal event, so usage/tools/cost are counted once.
    # Keep one pending event of each kind per analysis so a pair is collapsed
    # even when both rows share a timestamp and are read in either order.
    pending_success: dict[str, dict[str, Any]] = {}
    pending_terminal: dict[str, dict[str, Any]] = {}

    def consume_event(object_id: str, details: Any) -> None:
        run = details if isinstance(details, dict) else {}
        if run.get("status") == "llm_success":
            terminal = pending_terminal.pop(object_id, None)
            if terminal is not None and _same_model_call(run, terminal):
                consume_run(terminal)
                return
            if terminal is not None:
                consume_run(terminal)
            previous = pending_success.pop(object_id, None)
            if previous is not None:
                consume_run(previous)
            pending_success[object_id] = run
            return
        success = pending_success.pop(object_id, None)
        if success is not None and _same_model_call(success, run):
            consume_run(run)
            return
        if success is not None:
            consume_run(success)
        previous = pending_terminal.pop(object_id, None)
        if previous is not None:
            consume_run(previous)
        pending_terminal[object_id] = run

    llm_audit_stmt = (
        select(AuditLog.object_id, AuditLog.details)
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.object_type == "analysis",
            AuditLog.action.in_(LLM_AUDIT_ACTIONS),
            AuditLog.created_at >= start,
            AuditLog.created_at <= end,
        )
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
    )
    for object_id, details in session.execute(llm_audit_stmt).yield_per(500):
        consume_event(str(object_id), details)
    for details in pending_success.values():
        consume_run(details)
    for details in pending_terminal.values():
        consume_run(details)

    # Compatibility for analyses written before LLM audit events existed (or
    # by older code paths). Only analyses with no LLM audit event in this
    # window use result.llm, so the same run is never counted twice.
    audit_exists = (
        select(AuditLog.id)
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.object_type == "analysis",
            AuditLog.object_id == Analysis.id,
            AuditLog.action.in_(LLM_AUDIT_ACTIONS),
            AuditLog.created_at >= start,
            AuditLog.created_at <= end,
        )
        .exists()
    )
    legacy_stmt = select(Analysis.id, Analysis.result).where(*base_filter, ~audit_exists)
    for _, result in session.execute(legacy_stmt).yield_per(200):
        run = _result_run(result)
        if run is not None:
            consume_run(run)

    model_calls = llm_stats["model_calls"]
    priced_runs = llm_stats["priced_runs"]
    unpriced_runs = llm_stats["unpriced_runs"]
    priced_calls = llm_stats["priced_calls"]
    unpriced_calls = llm_stats["unpriced_calls"]
    cost_total = round(llm_stats["cost_total"], 6)
    if model_calls == 0:
        cost_status = "no_data"
        amount_usd = None
    elif unpriced_calls:
        cost_status = "partial"
        amount_usd = None
    else:
        cost_status = "complete"
        amount_usd = cost_total
    cost = {
        "status": cost_status,
        "currency": "USD",
        "amount_usd": amount_usd,
        # A known subtotal is useful for diagnostics, but it is never presented
        # as the total when any actual call is unpriced.
        "known_subtotal_usd": cost_total if priced_calls else None,
        "priced_runs": priced_runs,
        "unpriced_runs": unpriced_runs,
        "priced_calls": priced_calls,
        "unpriced_calls": unpriced_calls,
        "pricing_version": next(iter(pricing_versions)) if len(pricing_versions) == 1 else None,
        "source": PRICING_SOURCE,
        "note": (
            "Public list-price estimate from persisted LLM audit events, not an invoice. "
            "Native tool rounds and non-native calls are priced separately; a run is complete "
            "only when every actual provider response has usage and a versioned price. "
            "Runs with no model call are excluded from token and cost denominators. "
            "Any missing usage or price makes the total unknown; known_subtotal_usd is separate."
        ),
    }

    retry_events = session.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.object_type == "analysis",
            AuditLog.created_at >= start,
            AuditLog.created_at <= end,
            AuditLog.action.in_(RETRY_AUDIT_ACTIONS),
        )
    ) or 0
    dead_letter_events = session.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.object_type == "analysis",
            AuditLog.created_at >= start,
            AuditLog.created_at <= end,
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
            "total": total_jobs,
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
            "runs": llm_stats["runs"],
            "model_calls": model_calls,
            "provider_requests": llm_stats["provider_requests"],
            "provider_retries": llm_stats["provider_retries"],
            "no_model_call_runs": llm_stats["no_model_call_runs"],
            "by_status": {status: llm_counts.get(status, 0) for status in LLM_STATUSES},
            "native_tool_runs": llm_stats["native_runs"],
            "termination_reasons": [
                {"reason": reason, "count": count}
                for reason, count in termination_reasons.most_common()
            ],
        },
        "tools": {
            "calls": llm_stats["tool_calls"],
            "failed_calls": llm_stats["failed_tool_calls"],
            "rounds": llm_stats["tool_rounds"],
            "by_name": [
                {"name": name, "calls": count, "failed": failed_tool_names.get(name, 0)}
                for name, count in tool_names.most_common()
            ],
        },
        "tokens": {
            "runs_with_usage": llm_stats["runs_with_usage"],
            "runs_without_usage": llm_stats["runs_without_usage"],
            "prompt": token_totals["prompt"] if llm_stats["runs_with_usage"] else None,
            "completion": token_totals["completion"] if llm_stats["runs_with_usage"] else None,
            "total": token_totals["total"] if llm_stats["runs_with_usage"] else None,
            "cached": token_totals["cached"] if llm_stats["runs_with_usage"] else None,
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
            "Window filters analysis jobs and LLM audit events by created_at; approvals and executions show current state.",
            "LLM history comes from audit events; result.llm is used only when an analysis has no LLM audit event in the window.",
            "model_calls counts logical provider calls (native tool rounds or one non-native call); provider_requests includes internal retries.",
            "Runs with no model call are counted separately and excluded from usage/cost denominators.",
            "Native runs are complete only when every round has usage and a versioned price; one unpriced round makes amount_usd null.",
            "Tool arguments and raw prompts are not returned; only names and counts.",
        ],
    }
