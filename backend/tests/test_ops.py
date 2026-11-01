import json
from datetime import timedelta

from app.models import Analysis, Execution, Recommendation, Tenant, User, utcnow
from app.security import hash_password

ACME_APPROVER_EMAIL = "approver@acme.test"
ACME_APPROVER_PASSWORD = "acme-approver-secret"


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
    }
    assert jobs["retry_count"] == 3
    assert jobs["dead_letter"] == 1
    assert jobs["error_codes"] == [{"code": "llm_network_error", "count": 1}]

    assert body["llm"]["runs"] == 1
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
    assert body["cost"]["priced_runs"] == 1
    assert body["cost"]["unpriced_runs"] == 0
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


def test_ops_missing_usage_or_price_is_unknown_not_zero(client, approver):
    add_analysis(client, "job-no-usage", result=llm_result(usage=None))
    body = client.get("/api/ops/summary", headers=approver).json()
    assert body["cost"]["status"] == "partial"
    assert body["cost"]["amount_usd"] is None
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
