from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import func, select

from app.models import AuditLog, Execution, Order, Recommendation


def test_auth_and_unapproved_execution(client, analyst, approver, recommendation):
    assert client.get("/health").status_code == 200
    assert client.get("/api/metrics").status_code == 401
    headers = {**analyst, "X-Role": "approver", "X-Actor": "admin"}
    assert client.post(f"/api/recommendations/{recommendation}/approve", json={"reason": "伪造角色测试"}, headers=headers).status_code == 403
    response = client.post(f"/api/recommendations/{recommendation}/execute", headers={**approver, "Idempotency-Key": "before-approval"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "not_approved"
    assert client.post(f"/api/recommendations/{recommendation}/execute", headers=approver).status_code == 422


def test_approve_execute_replay_and_audit(client, analyst, approver, recommendation):
    path = f"/api/recommendations/{recommendation}"
    approved = client.post(f"{path}/approve", json={"reason": "已复核证据，仅批准模拟"}, headers=approver)
    assert approved.status_code == 200
    snapshot = approved.json()["body"]["approval"]
    assert snapshot["version"] == 1
    assert snapshot["snapshot_type"] == "ledger_and_action"
    assert snapshot["action"] == approved.json()["action"]
    assert len(snapshot["ledger_hash"]) == len(snapshot["action_hash"]) == 64
    assert snapshot["scope"]["period"] == approved.json()["body"]["period"]
    assert client.post(f"{path}/approve", json={"reason": "重复审批"}, headers=approver).status_code == 409
    assert client.post(f"{path}/reject", json={"reason": "不可逆转审批"}, headers=approver).status_code == 409
    execution_headers = {**approver, "Idempotency-Key": "execution-test", "X-Request-ID": "test-exec-request"}
    first = client.post(f"{path}/execute", headers=execution_headers)
    second = client.post(f"{path}/execute", headers=execution_headers)
    third = client.post(f"{path}/execute", headers={**approver, "Idempotency-Key": "different-key"})
    assert first.status_code == second.status_code == third.status_code == 200
    assert first.json()["status"] == "executed"
    assert first.json()["execution_id"] == second.json()["execution_id"] == third.json()["execution_id"]
    assert not first.json()["replayed"] and second.json()["replayed"] and third.json()["replayed"]
    assert first.json()["result"]["external_calls"] == 0
    assert first.json()["result"]["side_effect_count"] == 1
    logs = client.get(f"/api/audit-logs?object_id={recommendation}", headers=analyst).json()["items"]
    actions = [row["action"] for row in logs]
    for action in ("recommendation.proposed", "recommendation.pending_approval", "recommendation.approve", "execution.started", "execution.executed", "execution.replayed", "request.approve.denied"):
        assert action in actions
    assert any(row["request_id"] == "test-exec-request" and row["action"] == "execution.executed" for row in logs)
    with client.app.state.factory() as session:
        assert session.scalar(select(func.count()).select_from(Execution)) == 1


def test_execution_rejects_changed_ledger_after_approval(client, approver, analyst, recommendation):
    path = f"/api/recommendations/{recommendation}"
    approved = client.post(f"{path}/approve", json={"reason": "批准后修改账本"}, headers=approver)
    assert approved.status_code == 200
    with client.app.state.factory.begin() as session:
        row = session.scalar(select(Order).where(Order.tenant_id == "demo").order_by(Order.date.desc()))
        row.sales += 100
    response = client.post(f"{path}/execute", headers={**approver, "Idempotency-Key": "stale-ledger"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_stale"
    detail = client.get(path, headers=analyst).json()
    assert detail["status"] == "approved"
    with client.app.state.factory() as session:
        assert session.scalar(select(func.count()).select_from(Execution)) == 0
    logs = client.get(f"/api/audit-logs?object_id={recommendation}", headers=analyst).json()["items"]
    stale = next(row for row in logs if row["action"] == "execution.stale")
    assert stale["outcome"] == "denied"
    assert stale["details"]["code"] == "approval_stale"


def test_execution_rejects_changed_action_after_approval(client, approver, analyst, recommendation):
    path = f"/api/recommendations/{recommendation}"
    assert client.post(f"{path}/approve", json={"reason": "批准后篡改动作"}, headers=approver).status_code == 200
    with client.app.state.factory.begin() as session:
        row = session.get(Recommendation, recommendation)
        row.action = {**row.action, "baseline_daily_budget": row.action["baseline_daily_budget"] + 1}
    response = client.post(f"{path}/execute", headers={**approver, "Idempotency-Key": "changed-action"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "action_changed"
    assert client.get(path, headers=analyst).json()["status"] == "approved"
    with client.app.state.factory() as session:
        assert session.scalar(select(func.count()).select_from(Execution)) == 0
    logs = client.get(f"/api/audit-logs?object_id={recommendation}", headers=analyst).json()["items"]
    assert any(row["action"] == "execution.action_changed" for row in logs)


def test_approval_rejects_evidence_changed_after_analysis(client, approver, analyst, recommendation):
    path = f"/api/recommendations/{recommendation}"
    with client.app.state.factory.begin() as session:
        row = session.scalar(select(Order).where(Order.tenant_id == "demo").order_by(Order.date.desc()))
        row.sales += 100
    response = client.post(f"{path}/approve", json={"reason": "账本已变化"}, headers=approver)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "recommendation_stale"
    assert client.get(path, headers=analyst).json()["status"] == "pending_approval"
    logs = client.get(f"/api/audit-logs?object_id={recommendation}", headers=analyst).json()["items"]
    assert any(row["action"] == "recommendation.stale" for row in logs)


def test_execution_requires_approval_snapshot(client, approver, recommendation):
    with client.app.state.factory.begin() as session:
        row = session.get(Recommendation, recommendation)
        row.status = "approved"
        row.approved_by = "demo-approver"
    response = client.post(
        f"/api/recommendations/{recommendation}/execute",
        headers={**approver, "Idempotency-Key": "missing-snapshot"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_snapshot_missing"
    with client.app.state.factory() as session:
        assert session.scalar(select(func.count()).select_from(Execution)) == 0
        assert session.get(Recommendation, recommendation).status == "approved"


def test_rejected_terminal(client, approver, analyst, recommendation):
    path = f"/api/recommendations/{recommendation}"
    result = client.post(f"{path}/reject", json={"reason": "缺少活动级转化证据"}, headers=approver)
    assert result.status_code == 200 and result.json()["status"] == "rejected"
    assert client.post(f"{path}/approve", json={"reason": "再次批准"}, headers=approver).status_code == 409
    assert client.post(f"{path}/execute", headers={**approver, "Idempotency-Key": "reject-test"}).status_code == 409
    logs = client.get(f"/api/audit-logs?object_id={recommendation}", headers=analyst).json()["items"]
    assert any(row["action"] == "recommendation.reject" for row in logs)


def test_concurrent_execution_is_exactly_once(client, approver, recommendation):
    path = f"/api/recommendations/{recommendation}"
    assert client.post(f"{path}/approve", json={"reason": "并发模拟验证"}, headers=approver).status_code == 200
    barrier = Barrier(6)
    def perform(number):
        barrier.wait(timeout=10)
        return client.post(f"{path}/execute", headers={**approver, "Idempotency-Key": f"parallel-{number}"})
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(perform, range(6)))
    assert [result.status_code for result in responses] == [200] * 6
    assert len({result.json()["execution_id"] for result in responses}) == 1
    assert sum(not result.json()["replayed"] for result in responses) == 1
    with client.app.state.factory() as session:
        assert session.scalar(select(func.count()).select_from(Execution)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "execution.executed")) == 1


def test_simulation_failure_is_diagnostic_terminal_and_audited(client, approver, analyst, recommendation, monkeypatch):
    from app import services
    path = f"/api/recommendations/{recommendation}"
    client.post(f"{path}/approve", json={"reason": "注入模拟失败"}, headers=approver)
    def fail(action):
        raise RuntimeError("injected simulator unavailable")
    monkeypatch.setattr(services, "simulate_action", fail)
    response = client.post(f"{path}/execute", headers={**approver, "Idempotency-Key": "failed-sim"})
    assert response.status_code == 500
    assert response.json()["status"] == "execution_failed"
    assert response.json()["error"]["message"] == "injected simulator unavailable"
    replay = client.post(f"{path}/execute", headers={**approver, "Idempotency-Key": "failed-sim"})
    assert replay.status_code == 500 and replay.json()["replayed"]
    detail = client.get(path, headers=analyst).json()
    assert detail["status"] == "execution_failed" and detail["execution"]["error"]
    logs = client.get(f"/api/audit-logs?object_id={recommendation}", headers=analyst).json()["items"]
    assert any(log["action"] == "execution.failed" and log["outcome"] == "failure" for log in logs)


def test_cross_recommendation_key_conflict(client, approver, analyst, recommendation):
    other = client.post("/api/analyses", json={}, headers=analyst).json()
    second = client.get(f"/api/analyses/{other['id']}", headers=analyst).json()["recommendation_id"]
    for target in [recommendation, second]:
        client.post(f"/api/recommendations/{target}/approve", json={"reason": "核对后批准"}, headers=approver)
    assert client.post(f"/api/recommendations/{recommendation}/execute", headers={**approver, "Idempotency-Key": "shared-key"}).status_code == 200
    conflict = client.post(f"/api/recommendations/{second}/execute", headers={**approver, "Idempotency-Key": "shared-key"})
    assert conflict.status_code == 409
    assert client.get(f"/api/recommendations/{second}", headers=analyst).json()["status"] == "approved"


def test_audit_filter_by_request_id(client, analyst, approver, recommendation):
    path = f"/api/recommendations/{recommendation}"
    assert client.post(f"{path}/approve", json={"reason": "审计过滤前置审批"}, headers=approver).status_code == 200
    executed = client.post(f"{path}/execute", headers={**approver, "Idempotency-Key": "audit-filter", "X-Request-ID": "filter-request"})
    assert executed.status_code == 200
    items = client.get("/api/audit-logs?request_id=filter-request", headers=analyst).json()["items"]
    assert items and {row["request_id"] for row in items} == {"filter-request"}
    assert {"execution.started", "execution.executed"}.issubset({row["action"] for row in items})
    assert client.get("/api/audit-logs?request_id=missing-request", headers=analyst).json()["items"] == []


def test_seed_preserves_approval_execution_and_audit(client, analyst, approver, recommendation):
    path = f"/api/recommendations/{recommendation}"
    assert client.post(f"{path}/approve", json={"reason": "种子重放前已批准"}, headers=approver).status_code == 200
    assert client.post(f"{path}/execute", headers={**approver, "Idempotency-Key": "seed-preserve"}).status_code == 200
    before = client.get(path, headers=analyst).json()
    logs_before = client.get(f"/api/audit-logs?object_id={recommendation}", headers=analyst).json()["items"]
    seeded = client.post("/api/demo/seed", headers=analyst)
    assert seeded.status_code == 200 and seeded.json()["seeded"] is False
    after = client.get(path, headers=analyst).json()
    assert after["status"] == "executed"
    assert after["execution"]["execution_id"] == before["execution"]["execution_id"]
    logs_after = client.get(f"/api/audit-logs?object_id={recommendation}", headers=analyst).json()["items"]
    assert len(logs_after) == len(logs_before)
