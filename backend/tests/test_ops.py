import json
from datetime import timedelta
from uuid import uuid4

from app.models import Analysis, AuditLog, Execution, Recommendation, Tenant, User, utcnow
from app.pricing import estimate_cost_usd
from app.security import hash_password

ACME_APPROVER_EMAIL = "approver@acme.test"
ACME_APPROVER_PASSWORD = "acme-approver-secret"
USAGE_A = {
    "prompt_tokens": 1000, "completion_tokens": 100,
    "total_tokens": 1100, "cached_tokens": 200,
}
USAGE_B = {
    "prompt_tokens": 2000, "completion_tokens": 200,
    "total_tokens": 2200, "cached_tokens": 0,
}


def add_acme_approver(client):
    with client.app.state.factory.begin() as session:
        session.add(Tenant(id="acme", name="Acme"))
        session.add(User(
            tenant_id="acme", email=ACME_APPROVER_EMAIL, display_name="Acme Approver",
            role="approver", password_hash=hash_password(ACME_APPROVER_PASSWORD),
        ))


def login(client, email, password, tenant_id=None):
    body = {"email": email, "password": password}
    if tenant_id:
        body["tenant_id"] = tenant_id
    response = client.post("/api/auth/login", json=body)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def add_analysis(
    client, analysis_id, *, tenant_id="demo", status="completed", result=None,
    error=None, attempts=1, max_attempts=3, created_at=None,
):
    with client.app.state.factory.begin() as session:
        session.add(Analysis(
            id=analysis_id, tenant_id=tenant_id, idempotency_key=analysis_id,
            request={}, actor="demo-analyst", request_id=f"req-{analysis_id}",
            status=status, result=result, error=error, attempts=attempts,
            max_attempts=max_attempts, created_at=created_at or utcnow(),
        ))


def llm_result(*, usage=None, model="deepseek-flash", status="llm_success"):
    return {
        "engine": "deterministic-rules-v1",
        "llm": {
            "status": status,
            "configured": True,
            "provider": "deepseek",
            "model": model,
            "native_tools": True,
            "termination_reason": "final_answer",
            "rounds": [
                {"round": 1, "finish_reason": "tool_calls", "tool_call_count": 2, "latency_ms": 10.0, "usage": usage},
                {"round": 2, "finish_reason": "stop", "tool_call_count": 0, "latency_ms": 5.0, "usage": usage},
            ],
            "tool_calls": [
                {"round": 1, "tool_call_id": "call-1", "name": "get_profit_analysis", "arguments": {"secret": "not-returned"}, "ok": True, "result_summary": "ok", "error_code": None, "latency_ms": 1.0},
                {"round": 1, "tool_call_id": "call-2", "name": "get_sku_breakdown", "arguments": {}, "ok": False, "result_summary": "bad", "error_code": "invalid_arguments", "latency_ms": 1.0},
            ],
            "usage": usage,
        },
    }


def llm_round(round_no, usage, *, finish_reason="tool_calls", tool_call_count=1):
    return {
        "round": round_no, "finish_reason": finish_reason,
        "tool_call_count": tool_call_count, "latency_ms": 10.0, "usage": usage,
    }


def llm_details(
    *, usage=None, model="deepseek-flash", status="llm_success", configured=True,
    reason=None, latency_ms=15.0, native_tools=True, attempts=1, rounds=None,
):
    return {
        "status": status,
        "configured": configured,
        "provider": "deepseek",
        "model": model,
        "reason": reason,
        "attempts": attempts,
        "latency_ms": latency_ms,
        "usage": usage,
        "error": None,
        "evidence_references": None,
        "knowledge_references": None,
        "confidence": None,
        "native_tools": native_tools,
        "termination_reason": "final_answer" if native_tools else None,
        "rounds": rounds if rounds is not None else (
            [llm_round(1, usage)] if native_tools else []
        ),
        "tool_calls": [
            {
                "round": 1, "tool_call_id": "call-1", "name": "get_profit_analysis",
                "arguments": {}, "ok": True, "result_summary": "ok",
                "error_code": None, "latency_ms": 1.0,
            },
        ] if native_tools else [],
    }


def add_llm_audit(
    client, object_id, *, tenant_id="demo", action="analysis.llm_succeeded",
    outcome="success", details=None, created_at=None,
):
    with client.app.state.factory.begin() as session:
        session.add(AuditLog(
            id=str(uuid4()), tenant_id=tenant_id, actor="demo-analyst",
            action=action, object_type="analysis", object_id=object_id,
            request_id=f"req-{object_id}", outcome=outcome,
            details=details or {}, created_at=created_at or utcnow(),
        ))


def test_ops_requires_approver_and_is_tenant_scoped(client, analyst, approver):
    assert client.get("/api/ops/summary").status_code == 401
    denied = client.get("/api/ops/summary", headers=analyst)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"

    empty = client.get("/api/ops/summary", headers=approver)
    assert empty.status_code == 200
    assert empty.json()["jobs"]["total"] == 0
    assert empty.json()["cost"]["status"] == "no_data"
    assert empty.json()["cost"]["amount_usd"] is None

    add_acme_approver(client)
    add_analysis(
        client, "acme-failed", tenant_id="acme", status="failed",
        error={"code": "llm_network_error", "message": "network", "dead_letter": True},
    )
    acme_token = login(client, ACME_APPROVER_EMAIL, ACME_APPROVER_PASSWORD, "acme")
    acme = client.get("/api/ops/summary", headers=bearer(acme_token)).json()
    assert acme["tenant_id"] == "acme"
    assert acme["jobs"]["total"] == 1
    assert [item["id"] for item in acme["recent_failures"]] == ["acme-failed"]

    demo = client.get("/api/ops/summary", headers=approver).json()
    assert demo["jobs"]["total"] == 0
    assert demo["recent_failures"] == []


def test_ops_aggregates_persisted_jobs_tools_tokens_and_cost(client, approver):
    usage = {
        "prompt_tokens": 1000, "completion_tokens": 100,
        "total_tokens": 1100, "cached_tokens": 200,
    }
    add_analysis(client, "job-success", result=llm_result(usage=usage))
    add_analysis(
        client, "job-dead", status="failed", attempts=3, max_attempts=3,
        error={"code": "llm_network_error", "message": "upstream reset", "dead_letter": True},
    )
    add_analysis(
        client, "job-retry", status="queued", attempts=2, max_attempts=3,
        error={"code": "llm_timeout", "message": "retry scheduled", "retry_scheduled": True},
    )
    with client.app.state.factory.begin() as session:
        session.add(Recommendation(
            id="rec-pending", tenant_id="demo", analysis_id="job-success",
            status="pending_approval", title="pending", body={}, action={},
        ))
        session.add(Recommendation(
            id="rec-approved", tenant_id="demo", analysis_id="job-dead",
            status="approved", title="approved", body={}, action={}, approved_by="demo-approver",
        ))
        session.flush()
        session.add(Execution(
            id="exec-1", tenant_id="demo", recommendation_id="rec-approved",
            idempotency_key="exec-1", status="executed", simulation=True,
            actor="demo-approver", request_id="req-exec",
        ))

    response = client.get("/api/ops/summary", headers=approver)
    assert response.status_code == 200, response.text
    body = response.json()
    jobs = body["jobs"]
    assert jobs["total"] == 3
    assert jobs["by_status"] == {
        "queued": 1, "running": 0, "waiting_approval": 0,
        "completed": 1, "insufficient_data": 0, "failed": 1,
        "approved": 0, "rejected": 0, "executed": 0, "execution_failed": 0,
    }
    assert sum(jobs["by_status"].values()) == jobs["total"]
    assert jobs["retry_count"] == 3
    assert jobs["dead_letter"] == 1
    assert jobs["error_codes"] == [{"code": "llm_network_error", "count": 1}]

    assert body["llm"]["runs"] == 1
    assert body["llm"]["model_calls"] == 2
    assert body["llm"]["provider_requests"] == 2
    assert body["llm"]["provider_retries"] == 0
    assert body["llm"]["by_status"]["llm_success"] == 1
    assert body["llm"]["native_tool_runs"] == 1
    assert body["llm"]["termination_reasons"] == [{"reason": "final_answer", "count": 1}]
    assert body["tools"]["calls"] == 2
    assert body["tools"]["failed_calls"] == 1
    assert body["tools"]["rounds"] == 2
    assert body["tools"]["by_name"] == [
        {"name": "get_profit_analysis", "calls": 1, "failed": 0},
        {"name": "get_sku_breakdown", "calls": 1, "failed": 1},
    ]
    assert body["tokens"] == {
        "runs_with_usage": 1, "runs_without_usage": 0,
        "prompt": 1000, "completion": 100, "total": 1100, "cached": 200,
    }
    assert body["cost"]["status"] == "complete"
    assert body["cost"]["amount_usd"] > 0
    assert body["cost"]["known_subtotal_usd"] == body["cost"]["amount_usd"]
    assert body["cost"]["priced_runs"] == 1
    assert body["cost"]["unpriced_runs"] == 0
    assert body["cost"]["priced_calls"] == 2
    assert body["cost"]["unpriced_calls"] == 0
    assert body["cost"]["pricing_version"] == "2026-11-02"
    assert body["approvals"]["by_status"]["pending_approval"] == 1
    assert body["approvals"]["by_status"]["approved"] == 1
    assert body["executions"]["total"] == 1
    assert body["recent_failures"][0]["id"] == "job-dead"
    assert body["recent_failures"][0]["dead_letter"] is True
    # Raw prompts and tool arguments must never be copied into ops telemetry.
    payload = json.dumps(body)
    assert "not-returned" not in payload
    assert "system_prompt" not in payload


def test_ops_by_status_includes_post_approval_states(client, approver):
    add_analysis(client, "job-approved", status="approved")
    add_analysis(client, "job-rejected", status="rejected")
    add_analysis(client, "job-executed", status="executed")
    add_analysis(client, "job-execution-failed", status="execution_failed")

    body = client.get("/api/ops/summary", headers=approver).json()
    jobs = body["jobs"]
    assert jobs["total"] == 4
    assert jobs["by_status"]["approved"] == 1
    assert jobs["by_status"]["rejected"] == 1
    assert jobs["by_status"]["executed"] == 1
    assert jobs["by_status"]["execution_failed"] == 1
    assert sum(jobs["by_status"].values()) == jobs["total"]


def test_ops_missing_usage_or_price_is_unknown_not_zero(client, approver):
    add_analysis(client, "job-no-usage", result=llm_result(usage=None))
    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["cost"]["status"] == "partial"
    assert body["cost"]["amount_usd"] is None
    assert body["cost"]["known_subtotal_usd"] is None
    assert body["cost"]["unpriced_runs"] == 1
    assert body["tokens"]["runs_without_usage"] == 1
    assert body["tokens"]["prompt"] is None

    with client.app.state.factory.begin() as session:
        session.query(Analysis).filter(Analysis.id == "job-no-usage").delete()
    add_analysis(
        client, "job-unknown-model",
        result=llm_result(
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            model="not-priced-model",
        ),
    )
    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["cost"]["status"] == "partial"
    assert body["cost"]["amount_usd"] is None
    assert body["cost"]["priced_runs"] == 0
    assert body["cost"]["unpriced_runs"] == 1


def test_ops_window_excludes_old_jobs(client, approver):
    add_analysis(
        client, "job-old", status="failed", created_at=utcnow() - timedelta(hours=48),
        error={"code": "old_failure", "message": "old", "dead_letter": True},
    )
    add_analysis(client, "job-new", status="completed")
    body = client.get("/api/ops/summary?window_hours=24", headers=approver).json()
    assert body["jobs"]["total"] == 1
    assert body["recent_failures"] == []
    assert body["window"]["hours"] == 24


def test_ops_no_model_call_runs_do_not_affect_cost(client, approver):
    no_call = {
        "engine": "deterministic-rules-v1",
        "llm": {
            "status": "not_requested", "configured": True, "provider": "deepseek",
            "model": "deepseek-flash", "reason": "no_anomaly", "native_tools": False,
            "termination_reason": None, "rounds": [], "tool_calls": [], "usage": None,
        },
    }
    add_analysis(client, "job-no-anomaly", result=no_call)
    add_analysis(client, "job-insufficient", status="insufficient_data", result={
        **no_call,
        "llm": {**no_call["llm"], "reason": "insufficient_data"},
    })
    add_analysis(client, "job-not-configured", result={
        "engine": "deterministic-rules-v1",
        "llm": {
            "status": "deterministic_fallback", "configured": False, "provider": "deepseek",
            "model": "deepseek-flash", "reason": "not_configured", "native_tools": False,
            "termination_reason": None, "rounds": [], "tool_calls": [], "usage": None,
        },
    })
    add_llm_audit(
        client, "job-not-configured", action="analysis.llm_fallback", outcome="degraded",
        details=llm_details(
            status="deterministic_fallback", configured=False,
            reason="not_configured", usage=None, native_tools=False,
        ),
    )
    add_analysis(client, "job-queued", status="queued")

    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["jobs"]["total"] == 4
    assert body["jobs"]["by_status"] == {
        "queued": 1, "running": 0, "waiting_approval": 0,
        "completed": 2, "insufficient_data": 1, "failed": 0,
        "approved": 0, "rejected": 0, "executed": 0, "execution_failed": 0,
    }
    assert sum(body["jobs"]["by_status"].values()) == body["jobs"]["total"]
    assert body["llm"]["runs"] == 3
    assert body["llm"]["model_calls"] == 0
    assert body["llm"]["no_model_call_runs"] == 3
    assert body["llm"]["by_status"] == {
        "llm_success": 0, "deterministic_fallback": 1,
        "llm_failed": 0, "not_requested": 2,
    }
    assert body["tokens"] == {
        "runs_with_usage": 0, "runs_without_usage": 0,
        "prompt": None, "completion": None, "total": None, "cached": None,
    }
    assert body["cost"]["status"] == "no_data"
    assert body["cost"]["amount_usd"] is None
    assert body["cost"]["known_subtotal_usd"] is None
    assert body["cost"]["priced_runs"] == 0
    assert body["cost"]["unpriced_runs"] == 0


def test_ops_partial_cost_keeps_known_subtotal_separate(client, approver):
    add_analysis(client, "job-priced", result=llm_result(usage=USAGE_A))
    add_llm_audit(client, "job-priced", details=llm_details(usage=USAGE_A))
    add_analysis(
        client, "job-unpriced",
        result=llm_result(usage=USAGE_B, model="not-priced-model"),
    )

    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["llm"]["runs"] == 2
    assert body["llm"]["model_calls"] == 3
    assert body["llm"]["provider_requests"] == 3
    assert body["llm"]["provider_retries"] == 0
    assert body["tokens"] == {
        "runs_with_usage": 2, "runs_without_usage": 0,
        "prompt": 3000, "completion": 300, "total": 3300, "cached": 200,
    }
    assert body["cost"]["status"] == "partial"
    assert body["cost"]["amount_usd"] is None
    assert body["cost"]["known_subtotal_usd"] > 0
    assert body["cost"]["priced_runs"] == 1
    assert body["cost"]["unpriced_runs"] == 1
    assert body["cost"]["priced_calls"] == 1
    assert body["cost"]["unpriced_calls"] == 2


def test_ops_retry_history_counts_each_llm_call_once(client, approver):
    base = utcnow() - timedelta(seconds=10)
    add_analysis(client, "job-llm-retry", result=llm_result(usage=USAGE_A))
    failed = llm_details(
        status="deterministic_fallback", reason="llm_timeout",
        usage=None, native_tools=False,
    )
    add_llm_audit(
        client, "job-llm-retry", action="analysis.llm_fallback", outcome="degraded",
        details=failed, created_at=base,
    )
    add_llm_audit(
        client, "job-llm-retry", action="analysis.llm_fallback", outcome="degraded",
        details={**failed, "latency_ms": 21.0}, created_at=base + timedelta(seconds=1),
    )
    add_llm_audit(
        client, "job-llm-retry", action="analysis.llm_succeeded", outcome="success",
        details=llm_details(usage=USAGE_A), created_at=base + timedelta(seconds=2),
    )

    body = client.get("/api/ops/summary", headers=approver).json()
    # The latest result.llm must not be added on top of the three audit events.
    assert body["llm"]["runs"] == 3
    assert body["llm"]["model_calls"] == 3
    assert body["llm"]["by_status"]["deterministic_fallback"] == 2
    assert body["llm"]["by_status"]["llm_success"] == 1
    assert body["tokens"]["runs_with_usage"] == 1
    assert body["tokens"]["runs_without_usage"] == 2
    assert body["tokens"]["prompt"] == 1000
    assert body["cost"]["status"] == "partial"
    assert body["cost"]["amount_usd"] is None
    assert body["cost"]["known_subtotal_usd"] > 0
    assert body["cost"]["priced_runs"] == 1
    assert body["cost"]["unpriced_runs"] == 2


def test_ops_success_then_invalid_evidence_fallback_counts_once(client, approver):
    success = llm_details(
        usage=USAGE_A, attempts=2,
        rounds=[
            llm_round(1, USAGE_A),
            llm_round(2, USAGE_A, finish_reason="stop", tool_call_count=0),
        ],
    )
    fallback = {
        **success,
        "status": "deterministic_fallback",
        "reason": "invalid_evidence_reference",
        "error": {
            "code": "invalid_evidence_reference",
            "message": "unknown evidence",
            "retryable": False,
        },
    }
    same_time = utcnow() - timedelta(seconds=5)
    add_analysis(client, "job-invalid-reference", result={"engine": "deterministic-rules-v1", "llm": fallback})
    add_llm_audit(client, "job-invalid-reference", details=success, created_at=same_time)
    add_llm_audit(
        client, "job-invalid-reference", action="analysis.llm_fallback",
        outcome="degraded", details=fallback, created_at=same_time,
    )

    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["llm"]["runs"] == 1
    assert body["llm"]["model_calls"] == 2
    assert body["llm"]["provider_requests"] == 2
    assert body["llm"]["provider_retries"] == 0
    assert body["llm"]["by_status"]["deterministic_fallback"] == 1
    assert body["llm"]["by_status"]["llm_success"] == 0
    assert body["tools"]["calls"] == 1
    assert body["tokens"]["runs_with_usage"] == 1
    assert body["tokens"]["prompt"] == 1000
    assert body["cost"]["status"] == "complete"
    assert body["cost"]["amount_usd"] > 0
    assert body["cost"]["priced_runs"] == 1
    assert body["cost"]["unpriced_runs"] == 0
    assert body["cost"]["priced_calls"] == 2
    assert body["cost"]["unpriced_calls"] == 0


def test_ops_failed_call_without_usage_is_partial(client, approver):
    add_analysis(
        client, "job-failed-no-usage",
        result=llm_result(usage=None, status="deterministic_fallback"),
    )
    add_llm_audit(
        client, "job-failed-no-usage", action="analysis.llm_fallback", outcome="degraded",
        details=llm_details(
            status="deterministic_fallback", reason="llm_timeout",
            usage=None, native_tools=False,
        ),
    )

    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["llm"]["runs"] == 1
    assert body["llm"]["model_calls"] == 1
    assert body["llm"]["no_model_call_runs"] == 0
    assert body["llm"]["by_status"]["deterministic_fallback"] == 1
    assert body["tokens"]["runs_with_usage"] == 0
    assert body["tokens"]["runs_without_usage"] == 1
    assert body["tokens"]["prompt"] is None
    assert body["cost"]["status"] == "partial"
    assert body["cost"]["amount_usd"] is None
    assert body["cost"]["known_subtotal_usd"] is None
    assert body["cost"]["priced_runs"] == 0
    assert body["cost"]["unpriced_runs"] == 1


def test_ops_llm_audit_is_tenant_scoped(client, approver):
    add_acme_approver(client)
    add_analysis(client, "job-demo-llm", result=llm_result(usage=USAGE_A))
    add_llm_audit(client, "job-demo-llm", details=llm_details(usage=USAGE_A))
    add_analysis(client, "job-acme-llm", tenant_id="acme", result=llm_result(usage=USAGE_B))
    add_llm_audit(
        client, "job-acme-llm", tenant_id="acme", details=llm_details(usage=USAGE_B),
    )

    demo = client.get("/api/ops/summary", headers=approver).json()
    assert demo["jobs"]["total"] == 1
    assert demo["llm"]["runs"] == 1
    assert demo["tokens"]["prompt"] == 1000
    assert demo["cost"]["priced_runs"] == 1

    acme_token = login(client, ACME_APPROVER_EMAIL, ACME_APPROVER_PASSWORD, "acme")
    acme = client.get("/api/ops/summary", headers=bearer(acme_token)).json()
    assert acme["tenant_id"] == "acme"
    assert acme["jobs"]["total"] == 1
    assert acme["llm"]["runs"] == 1
    assert acme["tokens"]["prompt"] == 2000
    assert acme["cost"]["priced_runs"] == 1


def test_ops_many_analyses_aggregate_with_bounded_recent_details(client, approver):
    base = utcnow() - timedelta(minutes=5)
    with client.app.state.factory.begin() as session:
        for index in range(250):
            analysis_id = f"bulk-{index:03d}"
            failed = index < 100
            session.add(Analysis(
                id=analysis_id, tenant_id="demo", idempotency_key=analysis_id,
                request={}, actor="demo-analyst", request_id=f"req-{analysis_id}",
                status="failed" if failed else "completed",
                result=None if failed else llm_result(usage=USAGE_A),
                error={
                    "code": "llm_network_error", "message": "upstream reset",
                    "dead_letter": index < 20,
                } if failed else None,
                attempts=1, max_attempts=3, created_at=base,
            ))
            if not failed:
                session.add(AuditLog(
                    id=str(uuid4()), tenant_id="demo", actor="demo-analyst",
                    action="analysis.llm_succeeded", object_type="analysis",
                    object_id=analysis_id, request_id=f"req-{analysis_id}",
                    outcome="success", details=llm_details(usage=USAGE_A),
                    created_at=base,
                ))

    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["jobs"]["total"] == 250
    assert body["jobs"]["by_status"]["failed"] == 100
    assert body["jobs"]["by_status"]["completed"] == 150
    assert body["jobs"]["dead_letter"] == 20
    assert body["jobs"]["error_codes"] == [{"code": "llm_network_error", "count": 100}]
    assert len(body["recent_failures"]) == 10
    assert body["llm"]["runs"] == 150
    assert body["llm"]["model_calls"] == 150
    assert body["llm"]["provider_requests"] == 150
    assert body["llm"]["provider_retries"] == 0
    assert body["tokens"]["prompt"] == 150_000
    assert body["tokens"]["completion"] == 15_000
    assert body["tokens"]["total"] == 165_000
    assert body["cost"]["status"] == "complete"
    assert body["cost"]["amount_usd"] > 0
    assert body["cost"]["priced_calls"] == 150
    assert body["cost"]["unpriced_calls"] == 0


def test_ops_native_two_priced_rounds_are_complete(client, approver):
    details = llm_details(
        usage=USAGE_A, attempts=2,
        rounds=[
            llm_round(1, USAGE_A),
            llm_round(2, USAGE_B, finish_reason="stop", tool_call_count=0),
        ],
    )
    add_analysis(
        client, "job-native-two-priced",
        result={"engine": "deterministic-rules-v1", "llm": details},
    )
    add_llm_audit(client, "job-native-two-priced", details=details)

    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["llm"]["runs"] == 1
    assert body["llm"]["model_calls"] == 2
    assert body["llm"]["provider_requests"] == 2
    assert body["llm"]["provider_retries"] == 0
    assert body["cost"]["status"] == "complete"
    expected = round(
        estimate_cost_usd("deepseek-flash", USAGE_A)["amount_usd"]
        + estimate_cost_usd("deepseek-flash", USAGE_B)["amount_usd"],
        6,
    )
    assert abs(body["cost"]["amount_usd"] - expected) < 1e-9
    assert body["cost"]["known_subtotal_usd"] == body["cost"]["amount_usd"]
    assert body["cost"]["priced_calls"] == 2
    assert body["cost"]["unpriced_calls"] == 0
    assert body["cost"]["priced_runs"] == 1
    assert body["cost"]["unpriced_runs"] == 0


def test_ops_native_missing_round_usage_is_partial(client, approver):
    details = llm_details(
        usage=USAGE_A, attempts=2,
        rounds=[
            llm_round(1, USAGE_A),
            llm_round(2, None, finish_reason="stop", tool_call_count=0),
        ],
    )
    add_analysis(
        client, "job-native-missing-usage",
        result={"engine": "deterministic-rules-v1", "llm": details},
    )
    add_llm_audit(client, "job-native-missing-usage", details=details)

    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["llm"]["model_calls"] == 2
    assert body["llm"]["provider_requests"] == 2
    assert body["llm"]["provider_retries"] == 0
    assert body["cost"]["status"] == "partial"
    assert body["cost"]["amount_usd"] is None
    expected = estimate_cost_usd("deepseek-flash", USAGE_A)["amount_usd"]
    assert abs(body["cost"]["known_subtotal_usd"] - expected) < 1e-9
    assert body["cost"]["priced_calls"] == 1
    assert body["cost"]["unpriced_calls"] == 1
    assert body["cost"]["priced_runs"] == 0
    assert body["cost"]["unpriced_runs"] == 1


def test_ops_native_failed_round_without_usage_is_partial(client, approver):
    details = llm_details(
        status="deterministic_fallback", reason="llm_timeout",
        usage=USAGE_A, attempts=2,
        rounds=[
            llm_round(1, USAGE_A),
            llm_round(2, None, finish_reason=None, tool_call_count=0),
        ],
    )
    add_analysis(
        client, "job-native-failed-round",
        result={"engine": "deterministic-rules-v1", "llm": details},
    )
    add_llm_audit(
        client, "job-native-failed-round", action="analysis.llm_fallback",
        outcome="degraded", details=details,
    )

    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["llm"]["by_status"]["deterministic_fallback"] == 1
    assert body["llm"]["model_calls"] == 2
    assert body["llm"]["provider_requests"] == 2
    assert body["cost"]["status"] == "partial"
    assert body["cost"]["amount_usd"] is None
    expected = estimate_cost_usd("deepseek-flash", USAGE_A)["amount_usd"]
    assert abs(body["cost"]["known_subtotal_usd"] - expected) < 1e-9
    assert body["cost"]["priced_calls"] == 1
    assert body["cost"]["unpriced_calls"] == 1
    assert body["cost"]["priced_runs"] == 0
    assert body["cost"]["unpriced_runs"] == 1


def test_ops_native_internal_retries_are_provider_requests(client, approver):
    details = llm_details(
        usage=USAGE_A, attempts=3,
        rounds=[
            llm_round(1, USAGE_A),
            llm_round(2, USAGE_A, finish_reason="stop", tool_call_count=0),
        ],
    )
    add_analysis(
        client, "job-native-retry",
        result={"engine": "deterministic-rules-v1", "llm": details},
    )
    add_llm_audit(client, "job-native-retry", details=details)

    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["llm"]["model_calls"] == 2
    assert body["llm"]["provider_requests"] == 3
    assert body["llm"]["provider_retries"] == 1
    assert body["cost"]["status"] == "complete"
    assert body["cost"]["priced_calls"] == 2


def test_ops_non_native_internal_retries_are_provider_requests(client, approver):
    details = llm_details(usage=USAGE_A, native_tools=False, attempts=3)
    add_analysis(
        client, "job-non-native-retry",
        result={"engine": "deepseek-llm-v1", "llm": details},
    )
    add_llm_audit(client, "job-non-native-retry", details=details)

    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["llm"]["model_calls"] == 1
    assert body["llm"]["provider_requests"] == 3
    assert body["llm"]["provider_retries"] == 2
    assert body["cost"]["status"] == "complete"
    assert body["cost"]["priced_calls"] == 1
    assert body["cost"]["unpriced_calls"] == 0


def test_ops_failed_before_provider_request_is_not_a_model_call(client, approver):
    details = llm_details(
        status="deterministic_fallback", reason="llm_invalid_scope",
        usage=None, native_tools=False, attempts=0,
    )
    add_analysis(
        client, "job-before-request",
        result={"engine": "deterministic-rules-v1", "llm": details},
    )
    add_llm_audit(
        client, "job-before-request", action="analysis.llm_fallback",
        outcome="degraded", details=details,
    )

    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["llm"]["runs"] == 1
    assert body["llm"]["model_calls"] == 0
    assert body["llm"]["provider_requests"] == 0
    assert body["llm"]["provider_retries"] == 0
    assert body["llm"]["no_model_call_runs"] == 1
    assert body["cost"]["status"] == "no_data"
    assert body["cost"]["amount_usd"] is None
