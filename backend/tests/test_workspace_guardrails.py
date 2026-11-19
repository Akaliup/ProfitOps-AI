"""Restart, tenancy and role checks for the nine-view workspace."""
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Execution, Order
from sqlalchemy import func, select

from test_auth_tenant import (
    ACME_APPROVER_EMAIL, ACME_APPROVER_PASSWORD, ACME_EMAIL, ACME_PASSWORD,
    ACME_TENANT, add_acme_tenant, bearer, login,
)


def _settings(url: str) -> Settings:
    return Settings(
        database_url=url, auto_seed=True, llm_api_key="",
        analysis_worker_enabled=False, analysis_max_attempts=1,
        knowledge_embedding_provider="hashing", knowledge_embedding_model="hashing-v1",
        knowledge_min_score=0.15,
    )


def test_anonymous_workspace_routes_require_auth(client):
    for path in (
        "/api/workspace/overview",
        "/api/workspace/campaigns",
        "/api/workspace/access",
        "/api/workspace/demo-csv/orders",
        "/api/evals",
    ):
        assert client.get(path).status_code == 401
    assert client.post("/api/proposals/budget", json={}).status_code == 401
    assert client.post("/api/proposals/price", json={
        "store_id": "us-store", "sku": "TRAVEL-BAG-01", "proposed_price_cents": 100,
    }).status_code == 401
    assert client.post("/api/evals/offline").status_code == 401


def test_role_header_cannot_approve_or_execute(client, analyst):
    created = client.post("/api/proposals/budget", headers=analyst, json={})
    assert created.status_code == 200, created.text
    recommendation_id = created.json()["id"]
    forged = {**analyst, "X-Role": "approver"}
    approved = client.post(
        f"/api/recommendations/{recommendation_id}/approve",
        headers=forged, json={"reason": "伪造审批角色"},
    )
    assert approved.status_code == 403
    executed = client.post(
        f"/api/recommendations/{recommendation_id}/execute",
        headers={**forged, "Idempotency-Key": "forged-execute"},
    )
    assert executed.status_code == 403
    assert client.get(f"/api/recommendations/{recommendation_id}", headers=analyst).json()["status"] == "pending_approval"


def test_other_tenant_cannot_read_demo_workspace(client, analyst):
    add_acme_tenant(client)
    created = client.post("/api/proposals/budget", headers=analyst, json={})
    assert created.status_code == 200, created.text
    recommendation_id = created.json()["id"]
    client.post("/api/evals/offline", headers=analyst)
    acme = bearer(login(client, ACME_EMAIL, ACME_PASSWORD, ACME_TENANT))

    hidden = client.get(f"/api/recommendations/{recommendation_id}", headers=acme)
    assert hidden.status_code == 404
    assert client.get("/api/workspace/overview?store_id=us-store", headers=acme).status_code == 404
    assert client.get("/api/workspace/campaigns?store_id=us-store", headers=acme).status_code == 404
    price = client.post("/api/proposals/price", headers=acme, json={
        "store_id": "us-store", "sku": "TRAVEL-BAG-01", "proposed_price_cents": 100,
    })
    assert price.status_code == 404
    assert price.json()["error"]["code"] == "store_not_found"
    csv_response = client.get("/api/workspace/demo-csv/orders", headers=acme)
    assert csv_response.status_code == 200
    assert "us-store" not in csv_response.text
    assert "acme-store" in csv_response.text
    assert client.get("/api/evals", headers=acme).json()["items"] == []
    acme_approver = bearer(login(client, ACME_APPROVER_EMAIL, ACME_APPROVER_PASSWORD, ACME_TENANT))
    forged = client.post(
        f"/api/recommendations/{recommendation_id}/approve",
        headers={**acme_approver, "X-Role": "approver"}, json={"reason": "跨租户审批"},
    )
    assert forged.status_code == 404
    assert client.get(f"/api/recommendations/{recommendation_id}", headers=analyst).json()["status"] == "pending_approval"


def test_restart_keeps_approval_and_repeat_import_is_noop(tmp_path):
    database = tmp_path / "restart.db"
    url = f"sqlite:///{database}"
    analyst = {"Authorization": "Bearer demo-analyst"}
    approver = {"Authorization": "Bearer demo-approver", "Idempotency-Key": "restart-execute"}

    app = create_app(_settings(url), inline_jobs=True)
    with TestClient(app) as http:
        demo = http.get("/api/demo").json()
        assert demo["data_range"]["end_date"] == "2026-10-30"
        assert demo["llm"]["configured"] is False
        inventory = http.get("/api/workspace/demo-csv/inventory", headers=analyst).text
        committed = http.post(
            "/api/data/inventory/commit?filename=inventory.csv",
            content=inventory, headers={**approver, "Content-Type": "text/csv"},
        )
        assert committed.status_code == 200, committed.text
        assert committed.json()["status"] == "committed"
        created = http.post("/api/proposals/budget", headers=analyst, json={})
        assert created.status_code == 200, created.text
        recommendation_id = created.json()["id"]
        approved = http.post(
            f"/api/recommendations/{recommendation_id}/approve",
            headers={"Authorization": "Bearer demo-approver"}, json={"reason": "重启前批准"},
        )
        assert approved.status_code == 200, approved.text
        with http.app.state.factory() as session:
            orders_before = session.scalar(select(func.count()).select_from(Order).where(Order.tenant_id == "demo"))

    restarted = create_app(_settings(url), inline_jobs=True)
    with TestClient(restarted) as http:
        detail = http.get(f"/api/recommendations/{recommendation_id}", headers=analyst)
        assert detail.status_code == 200, detail.text
        assert detail.json()["status"] == "approved"
        with http.app.state.factory() as session:
            orders_after = session.scalar(select(func.count()).select_from(Order).where(Order.tenant_id == "demo"))
        assert orders_after == orders_before
        first = http.post(f"/api/recommendations/{recommendation_id}/execute", headers=approver)
        assert first.status_code == 200, first.text
        assert first.json()["replayed"] is False
        assert first.json()["result"]["external_calls"] == 0
        second = http.post(f"/api/recommendations/{recommendation_id}/execute", headers=approver)
        assert second.status_code == 200, second.text
        assert second.json()["replayed"] is True
        assert second.json()["execution_id"] == first.json()["execution_id"]
        again = http.post(
            "/api/data/inventory/commit?filename=inventory.csv",
            content=inventory, headers={**approver, "Content-Type": "text/csv"},
        )
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "noop"
        with http.app.state.factory() as session:
            executions = session.scalars(select(Execution).where(Execution.recommendation_id == recommendation_id)).all()
        assert len(executions) == 1
