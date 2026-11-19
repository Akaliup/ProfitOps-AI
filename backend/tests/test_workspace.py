"""Nine-view workspace: ledger totals, proposals, margin floor, and offline evals."""
from sqlalchemy import select

from app.models import Execution, Order, Recommendation, Store


def _json(response):
    assert response.status_code < 500, response.text
    return response.json()


def test_overview_store_totals_match_ledger(client, analyst):
    overview = client.get("/api/workspace/overview", headers=analyst)
    body = _json(overview)
    assert overview.status_code == 200
    assert body["stores_reconciled"] is True
    assert body["currency"] == "USD"
    assert "虚构" not in body["summary"]["source"]
    sales = sum(row["current"]["sales"] for row in body["stores"])
    profit = sum(row["current"]["net_profit"] for row in body["stores"])
    assert sales == body["summary"]["current"]["sales"]
    assert profit == body["summary"]["current"]["net_profit"]
    assert body["note"]


def test_unknown_store_and_other_tenant_are_hidden(client, analyst):
    missing = client.get("/api/workspace/overview?store_id=missing-store", headers=analyst)
    assert missing.status_code == 404
    with client.app.state.factory.begin() as session:
        session.add(Store(id="secret-store", tenant_id="other", name="秘密店", marketplace="hidden"))
    hidden = client.get("/api/workspace/overview?store_id=secret-store", headers=analyst)
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "store_not_found"


def test_demo_inventory_import_is_idempotent_and_analyst_cannot_commit(client, analyst, approver):
    csv_response = client.get("/api/workspace/demo-csv/inventory", headers=analyst)
    assert csv_response.status_code == 200
    text = csv_response.text
    assert "TRAVEL-BAG-01" in text or "THERMO-02" in text
    denied = client.post(
        "/api/data/inventory/commit?filename=inventory.csv",
        content=text,
        headers={**analyst, "Content-Type": "text/csv"},
    )
    assert denied.status_code == 403
    ads = client.get("/api/workspace/demo-csv/ads", headers=approver)
    assert ads.status_code == 409
    assert ads.json()["error"]["code"] == "demo_ads_conflicts_with_ledger"
    committed = client.post(
        "/api/data/inventory/commit?filename=inventory.csv",
        content=text,
        headers={**approver, "Content-Type": "text/csv"},
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["status"] == "committed"
    again = client.post(
        "/api/data/inventory/commit?filename=inventory.csv",
        content=text,
        headers={**approver, "Content-Type": "text/csv"},
    )
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "noop"
    overview = client.get("/api/workspace/overview", headers=analyst).json()
    assert overview["inventory"]["low"] >= 1
    assert overview["inventory"]["missing"] is False


def test_replenishment_excludes_overstock_and_execution_is_idempotent(client, analyst, approver):
    text = client.get("/api/workspace/demo-csv/inventory", headers=approver).text
    imported = client.post(
        "/api/data/inventory/commit?filename=inventory.csv",
        content=text, headers={**approver, "Content-Type": "text/csv"},
    )
    assert imported.status_code == 200, imported.text
    created = client.post("/api/proposals/replenishment", headers=analyst, json={})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["replayed"] is False
    assert body["action"]["type"] == "simulate_replenishment"
    assert body["action"]["simulation"] is True
    skus = {line["sku"] for line in body["body"]["proposal"]["lines"]}
    assert skus == {"THERMO-02"}
    assert all(line["reorder_quantity"] > 0 for line in body["body"]["proposal"]["lines"])
    replay = client.post("/api/proposals/replenishment", headers=analyst, json={})
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["id"] == body["id"]
    forbidden = client.post(
        f"/api/recommendations/{body['id']}/approve",
        headers={**analyst, "X-Role": "approver"},
        json={"reason": "分析师不能审批"},
    )
    assert forbidden.status_code == 403
    approved = client.post(
        f"/api/recommendations/{body['id']}/approve",
        headers=approver, json={"reason": "同意模拟补货"},
    )
    assert approved.status_code == 200, approved.text
    headers = {**approver, "Idempotency-Key": "replenish-once"}
    first = client.post(f"/api/recommendations/{body['id']}/execute", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["simulation"] is True
    assert first.json()["replayed"] is False
    assert first.json()["result"]["external_calls"] == 0
    second = client.post(f"/api/recommendations/{body['id']}/execute", headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["replayed"] is True
    assert second.json()["execution_id"] == first.json()["execution_id"]
    with client.app.state.factory() as session:
        executions = session.scalars(select(Execution).where(Execution.recommendation_id == body["id"])).all()
        assert len(executions) == 1


def test_margin_redline_blocks_approve_and_forced_execute(client, analyst, approver):
    invalid = client.post("/api/proposals/price", headers=analyst, json={
        "store_id": "us-store", "sku": "TRAVEL-BAG-01", "proposed_price_cents": 0,
    })
    assert invalid.status_code == 422
    missing = client.post("/api/proposals/price", headers=analyst, json={
        "store_id": "us-store", "sku": "NO-SUCH", "proposed_price_cents": 100,
    })
    assert missing.status_code == 404
    created = client.post("/api/proposals/price", headers=analyst, json={
        "store_id": "us-store", "sku": "TRAVEL-BAG-01", "proposed_price_cents": 100,
    })
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["margin_check"]["ok"] is False
    assert body["action"]["margin_ok"] is False
    assert body["action"]["margin_pct"] < 28
    blocked = client.post(
        f"/api/recommendations/{body['id']}/approve",
        headers=approver, json={"reason": "试图越过红线"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "margin_redline"
    with client.app.state.factory.begin() as session:
        row = session.get(Recommendation, body["id"])
        row.status = "approved"
    forced = client.post(
        f"/api/recommendations/{body['id']}/execute",
        headers={**approver, "Idempotency-Key": "price-forced"},
    )
    assert forced.status_code == 409
    assert forced.json()["error"]["code"] == "margin_redline"
    with client.app.state.factory() as session:
        assert session.scalar(select(Execution).where(Execution.recommendation_id == body["id"])) is None
        assert session.get(Recommendation, body["id"]).status == "approved"


def test_passing_price_review_does_not_change_price(client, analyst, approver):
    created = client.post("/api/proposals/price", headers=approver, json={
        "store_id": "us-store", "sku": "TRAVEL-BAG-01", "proposed_price_cents": 20000,
    })
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["margin_check"]["ok"] is True
    approved = client.post(
        f"/api/recommendations/{body['id']}/approve",
        headers=approver, json={"reason": "毛利通过，仅模拟"},
    )
    assert approved.status_code == 200, approved.text
    executed = client.post(
        f"/api/recommendations/{body['id']}/execute",
        headers={**approver, "Idempotency-Key": "price-ok"},
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["result"]["external_calls"] == 0
    assert "售价未改变" in executed.json()["result"]["message"]


def test_budget_proposal_then_stale_ledger_blocks_execution(client, analyst, approver):
    created = client.post("/api/proposals/budget", headers=analyst, json={})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["action"]["type"] == "adjust_ad_budget"
    assert body["action"]["simulation"] is True
    assert body["action"]["proposed_daily_budget"] <= body["action"]["baseline_daily_budget"]
    approved = client.post(
        f"/api/recommendations/{body['id']}/approve",
        headers=approver, json={"reason": "同意预算模拟"},
    )
    assert approved.status_code == 200, approved.text
    with client.app.state.factory.begin() as session:
        order = session.scalar(select(Order).where(Order.tenant_id == "demo").limit(1))
        order.sales += 1
    executed = client.post(
        f"/api/recommendations/{body['id']}/execute",
        headers={**approver, "Idempotency-Key": "budget-stale"},
    )
    assert executed.status_code == 409
    assert executed.json()["error"]["code"] == "approval_stale"


def test_offline_eval_is_computed_and_access_hides_secrets(client, analyst, approver):
    empty = client.get("/api/evals", headers=analyst)
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    ran = client.post("/api/evals/offline", headers=analyst)
    assert ran.status_code == 200, ran.text
    report = ran.json()
    assert report["llm_calls"] == 0
    assert report["external_calls"] == 0
    assert report["total"] == len(report["cases"])
    assert report["passed"] + report["failed"] == report["total"]
    assert any(case["id"] == "store_sum" and case["ok"] for case in report["cases"])
    listed = client.get("/api/evals", headers=analyst).json()
    assert len(listed["items"]) == 1
    access = client.get("/api/workspace/access", headers=analyst).json()
    assert access["actor"]["role"] == "analyst"
    assert "api_key" not in str(access).lower()
    planned = [row for row in access["rows"] if row["implemented"] is False]
    assert planned and all(not any(row["cells"]) for row in planned)
    analyst_row = next(row for row in access["rows"] if row["role"] == "analyst")
    assert analyst_row["cells"][4] is False
    approver_access = client.get("/api/workspace/access", headers=approver).json()
    assert approver_access["actor"]["role"] == "approver"
    assert next(row for row in approver_access["integrations"] if row["name"] == "Langfuse / 外部追踪")["state"] == "not_integrated"
