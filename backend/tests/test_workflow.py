import time

from sqlalchemy import select, update

from app.models import Analysis, AuditLog, DailyOperation, Order, Recommendation
from app.schemas import ApprovalInput, ApprovalOutput, ToolFailure, ToolResult
from app.security import Actor
from app.tools import ToolContext, ToolSpec
from app.workflow import recover_interrupted


def test_langgraph_path_persisted(client, analyst, recommendation):
    row = client.get(f"/api/recommendations/{recommendation}", headers=analyst).json()
    analysis = client.get(f"/api/analyses/{row['analysis_id']}", headers=analyst).json()
    assert [step["name"] for step in analysis["steps"]] == [
        "receive_request", "get_sales_metrics", "calculate_profit", "analyze_profit_drivers",
        "verify_evidence", "retrieve_knowledge", "llm_root_cause_analysis", "verify_llm_evidence",
        "create_recommendation", "wait_for_approval",
    ]
    assert all(step["status"] == "completed" and step["duration_ms"] >= 0 for step in analysis["steps"])
    assert analysis["result"]["metrics"]["bridge_verified"]
    assert analysis["result"]["llm"]["status"] == "deterministic_fallback"
    assert analysis["result"]["llm"]["configured"] is False
    assert analysis["result"]["engine"] == "deterministic-rules-v1"
    assert row["action"]["simulation"] is True
    assert row["body"]["primary_driver"]["metric"] == "ad_spend"
    assert len(row["body"]["evidence"]) == 8
    with client.app.state.factory() as session:
        assert session.get(Analysis, row["analysis_id"]).snapshot["recommendation_id"] == recommendation


def test_insufficient_data_no_recommendation(client, analyst):
    with client.app.state.factory.begin() as session:
        session.execute(update(DailyOperation).where(DailyOperation.id == session.scalar(select(DailyOperation.id))).values(complete=False))
    created = client.post("/api/analyses", json={}, headers=analyst).json()
    result = client.get(f"/api/analyses/{created['id']}", headers=analyst).json()
    assert result["status"] == "insufficient_data"
    assert result["recommendation_id"] is None
    assert result["error"]["code"] == "insufficient_data"
    assert "create_recommendation" not in [step["name"] for step in result["steps"]]
    assert result["result"]["llm"]["status"] == "not_requested"
    assert result["result"]["llm"]["reason"] == "insufficient_data"


def test_no_anomaly_does_not_invent_action(client, analyst):
    # Create an explicit healthy scenario: profit equals sales in both periods.
    with client.app.state.factory.begin() as session:
        session.execute(update(DailyOperation).values(ad_spend=0))
        session.execute(update(Order).values(discounts=0, refunds=0, cogs=0, fulfillment=0, platform_fees=0, other_costs=0))
    created = client.post("/api/analyses", json={"start_date": "2026-10-24", "end_date": "2026-10-30"}, headers=analyst).json()
    result = client.get(f"/api/analyses/{created['id']}", headers=analyst).json()
    assert result["status"] == "completed"
    assert result["recommendation_id"] is None
    assert result["result"]["findings"]["anomaly"] is False
    assert result["result"]["llm"]["status"] == "not_requested"
    assert result["result"]["llm"]["reason"] == "no_anomaly"


def test_tool_validation_permission_unknown_and_failure(client):
    registry = client.app.state.registry
    context = ToolContext(client.app.state.factory, Actor("demo-analyst", "analyst"), "tool-test")
    assert registry.invoke("unknown", {}, context).error.code == "unknown_tool"
    assert registry.invoke("calculate_profit", {}, context).error.code == "invalid_tool_input"
    assert registry.invoke("simulate_execute_approved_action", {"recommendation_id": "x", "idempotency_key": "test"}, context).error.code == "forbidden"
    assert registry.invoke("get_sales_metrics", {"period": {"start_date": "2026-10-30", "end_date": "2026-10-01"}}, context).error.code == "invalid_tool_input"
    catalog = registry.catalog()
    assert len(catalog) == 9
    assert all(item["input_schema"] and item["output_schema"] for item in catalog)
    spec = registry.tools["get_sales_metrics"]
    spec.handler = lambda payload, ctx: (_ for _ in ()).throw(RuntimeError("data-source-offline"))
    result = registry.invoke("get_sales_metrics", {"period": {"start_date": "2026-10-01", "end_date": "2026-10-30"}}, context)
    assert not result.ok and result.error.code == "tool_execution_failed"
    assert "data-source-offline" in result.error.message


def test_failed_node_and_timeout_are_visible(client, analyst, monkeypatch):
    registry = client.app.state.registry
    original = registry.invoke
    def broken(name, payload, context):
        if name == "get_sales_metrics":
            return ToolResult(ok=False, error=ToolFailure(code="source_failed", message="Source unavailable"))
        return original(name, payload, context)
    monkeypatch.setattr(registry, "invoke", broken)
    created = client.post("/api/analyses", json={}, headers=analyst).json()
    result = client.get(f"/api/analyses/{created['id']}", headers=analyst).json()
    assert result["status"] == "failed" and result["error"]["code"] == "source_failed"
    assert result["steps"][-1]["status"] == "failed"
    monkeypatch.setattr(registry, "invoke", original)
    client.app.state.settings.step_timeout_seconds = 0.0000001
    created = client.post("/api/analyses", json={}, headers=analyst).json()
    result = client.get(f"/api/analyses/{created['id']}", headers=analyst).json()
    assert result["status"] == "failed" and result["error"]["code"] == "step_timeout"


def test_slow_knowledge_retrieval_degrades_without_failing_analysis(client, analyst, monkeypatch):
    registry = client.app.state.registry
    original = registry.invoke

    def slow(name, payload, context):
        if name == "search_knowledge":
            time.sleep(1.2)
        return original(name, payload, context)

    monkeypatch.setattr(registry, "invoke", slow)
    client.app.state.settings.step_timeout_seconds = 1.0
    created = client.post("/api/analyses", json={}, headers=analyst).json()
    result = client.get(f"/api/analyses/{created['id']}", headers=analyst).json()

    assert result["status"] == "waiting_approval", result
    retrieve = next(step for step in result["steps"] if step["name"] == "retrieve_knowledge")
    assert retrieve["status"] == "completed"
    assert retrieve["duration_ms"] >= 1000


def test_tool_policy_catalog_and_deny(client, analyst):
    registry = client.app.state.registry
    response = client.get("/api/tools", headers=analyst)
    assert response.status_code == 200 and len(response.json()["items"]) == 9
    catalog = {item["name"]: item for item in registry.catalog()}
    assert {item["policy"] for item in catalog.values()} == {"safe", "ask"}
    assert catalog["get_sales_metrics"]["policy"] == "safe"
    assert catalog["get_profit_analysis"]["effect"] == "read"
    assert catalog["get_sku_breakdown"]["effect"] == "read"
    assert catalog["simulate_execute_approved_action"]["policy"] == "ask"
    context = ToolContext(client.app.state.factory, Actor("demo-approver", "approver"), "policy-test")
    registry.register(ToolSpec(
        name="external_payment", description="must never run", input_model=ApprovalInput,
        output_model=ApprovalOutput, roles=("approver",), effect="external_write", policy="deny",
        handler=lambda payload, ctx: None,
    ))
    result = registry.invoke("external_payment", {"recommendation_id": "x"}, context)
    assert not result.ok and result.error.code == "policy_denied"


def test_api_timestamps_are_explicit_utc(client, analyst, recommendation):
    row = client.get(f"/api/recommendations/{recommendation}", headers=analyst).json()
    assert row["created_at"].endswith("Z") and row["updated_at"].endswith("Z")
    analysis = client.get(f"/api/analyses/{row['analysis_id']}", headers=analyst).json()
    assert analysis["created_at"].endswith("Z") and analysis["updated_at"].endswith("Z")
    logs = client.get("/api/audit-logs", headers=analyst).json()["items"]
    assert logs and all(item["created_at"].endswith("Z") for item in logs)


def test_analysis_request_idempotency(client, analyst):
    headers = {**analyst, "Idempotency-Key": "same-analysis"}
    first = client.post("/api/analyses", json={}, headers=headers)
    second = client.post("/api/analyses", json={}, headers=headers)
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["replayed"] is True
    changed = client.post("/api/analyses", json={"store_id": "us-store"}, headers=headers)
    assert changed.status_code == 409


def test_restart_marks_interrupted_analysis(client, analyst):
    factory = client.app.state.factory
    with factory.begin() as session:
        row = Analysis(id="interrupted", idempotency_key="interrupted", request={}, actor="demo-analyst", request_id="restart-test", status="running", steps=[{"name": "get_sales_metrics", "status": "running"}])
        session.add(row)
    recover_interrupted(factory)
    with factory() as session:
        row = session.get(Analysis, "interrupted")
        assert row.status == "failed" and row.error["code"] == "restart_interrupted"
        assert row.steps[0]["name"] == "get_sales_metrics"
        assert session.scalar(select(AuditLog).where(AuditLog.action == "analysis.interrupted")) is not None
