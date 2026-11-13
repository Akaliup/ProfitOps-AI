from datetime import date, timedelta

from sqlalchemy import select

from app.models import (
    Analysis, AuditLog, DailyOperation, Execution, Order, Product,
    Recommendation, Store, Tenant, User,
)
from app.security import hash_password

ACME_TENANT = "acme"
ACME_EMAIL = "analyst@acme.test"
ACME_PASSWORD = "acme-secret"
ACME_APPROVER_EMAIL = "approver@acme.test"
ACME_APPROVER_PASSWORD = "acme-approver-secret"
PERIOD_START = date(2026, 10, 1)
PERIOD_END = date(2026, 10, 2)


def add_acme_tenant(client):
    with client.app.state.factory.begin() as session:
        session.add(Tenant(id=ACME_TENANT, name="Acme"))
        session.add(User(
            tenant_id=ACME_TENANT, email=ACME_EMAIL, display_name="Acme Analyst",
            role="analyst", password_hash=hash_password(ACME_PASSWORD),
        ))
        session.add(User(
            tenant_id=ACME_TENANT, email=ACME_APPROVER_EMAIL, display_name="Acme Approver",
            role="approver", password_hash=hash_password(ACME_APPROVER_PASSWORD),
        ))
        session.add(Store(id="acme-store", tenant_id=ACME_TENANT, name="Acme Store", marketplace="Acme"))
        session.add(Product(id="acme-product", tenant_id=ACME_TENANT, sku="ACME-SKU-01", name="Acme Product"))
        session.flush()
        for day in (
            PERIOD_START - timedelta(days=2),
            PERIOD_START - timedelta(days=1),
            PERIOD_START,
            PERIOD_END,
        ):
            session.add(Order(
                id=f"acme-order-{day}", tenant_id=ACME_TENANT, store_id="acme-store",
                product_id="acme-product", date=day, quantity=1, sales=1000,
                discounts=0, refunds=0, cogs=400, fulfillment=100, platform_fees=50, other_costs=10,
            ))
            session.add(DailyOperation(
                id=f"acme-op-{day}", tenant_id=ACME_TENANT, store_id="acme-store",
                product_id="acme-product", date=day, ad_spend=100,
                inventory=10, inbound_inventory=0, complete=True,
            ))


def add_acme_approved_recommendation(client):
    """Insert a complete-ledger recommendation and approve it through the API."""
    action = {
        "type": "create_review_task", "simulation": True,
        "product_id": "acme-product", "sku": "ACME-SKU-01",
        "baseline_daily_budget": 0, "proposed_daily_budget": 0,
        "source_period": {"start_date": str(PERIOD_START), "end_date": str(PERIOD_END)},
    }
    with client.app.state.factory.begin() as session:
        session.add(Analysis(
            id="acme-execution-analysis", tenant_id=ACME_TENANT,
            idempotency_key="acme-execution-analysis", request={},
            actor="acme-analyst", request_id="acme-execution-test",
            status="waiting_approval", max_attempts=1,
        ))
        session.add(Recommendation(
            id="acme-execution-recommendation", tenant_id=ACME_TENANT,
            analysis_id="acme-execution-analysis", status="pending_approval",
            title="Acme 模拟执行", body={
                "period": {"start_date": str(PERIOD_START), "end_date": str(PERIOD_END)},
                "store_id": "acme-store",
            }, action=action,
        ))
    token = login(client, ACME_APPROVER_EMAIL, ACME_APPROVER_PASSWORD, ACME_TENANT)
    approved = client.post(
        "/api/recommendations/acme-execution-recommendation/approve",
        json={"reason": "Acme 集成审批"}, headers=bearer(token),
    )
    assert approved.status_code == 200, approved.text
    return "acme-execution-recommendation"


def login(client, email, password, tenant_id=None):
    body = {"email": email, "password": password}
    if tenant_id:
        body["tenant_id"] = tenant_id
    response = client.post("/api/auth/login", json=body)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_login_me_and_invalid_credentials(client):
    token = login(client, "analyst@demo.local", "demo-analyst")
    me = client.get("/api/auth/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["role"] == "analyst"
    assert me.json()["tenant_id"] == "demo"
    assert me.json()["email"] == "analyst@demo.local"

    wrong = client.post("/api/auth/login", json={"email": "analyst@demo.local", "password": "wrong"})
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_credentials"

    assert client.get("/api/auth/me", headers=bearer(token + "x")).status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_jwt_role_is_enforced_server_side(client, recommendation):
    analyst_token = login(client, "analyst@demo.local", "demo-analyst")
    approver_token = login(client, "approver@demo.local", "demo-approver")
    denied = client.post(
        f"/api/recommendations/{recommendation}/approve",
        json={"reason": "jwt analyst should not approve"}, headers=bearer(analyst_token),
    )
    assert denied.status_code == 403
    approved = client.post(
        f"/api/recommendations/{recommendation}/approve",
        json={"reason": "jwt approver review"}, headers=bearer(approver_token),
    )
    assert approved.status_code == 200


def test_tenant_metrics_and_store_isolation(client):
    add_acme_tenant(client)
    token = login(client, ACME_EMAIL, ACME_PASSWORD, ACME_TENANT)
    report = client.get(
        f"/api/metrics?start_date={PERIOD_START}&end_date={PERIOD_END}", headers=bearer(token),
    )
    assert report.status_code == 200, report.text
    body = report.json()
    assert [row["sku"] for row in body["sku_breakdown"]] == ["ACME-SKU-01"]
    assert body["data_quality"]["observed_records"] == 4
    assert body["current"]["sales"] == 2000

    demo_token = login(client, "analyst@demo.local", "demo-analyst")
    hidden_store = client.get(
        f"/api/metrics?start_date={PERIOD_START}&end_date={PERIOD_END}&store_id=acme-store",
        headers=bearer(demo_token),
    )
    assert hidden_store.status_code == 404
    assert hidden_store.json()["error"]["code"] == "store_not_found"


def test_cross_tenant_analysis_idempotency_keys_do_not_collide(client):
    add_acme_tenant(client)
    acme_token = login(client, ACME_EMAIL, ACME_PASSWORD, ACME_TENANT)
    demo_token = login(client, "analyst@demo.local", "demo-analyst")
    body = {"start_date": str(PERIOD_START), "end_date": str(PERIOD_END)}

    # demo stores the raw key; acme stores the same string with its prefix.
    demo = client.post(
        "/api/analyses", json=body,
        headers={**bearer(demo_token), "Idempotency-Key": "acme:shared"},
    )
    acme = client.post(
        "/api/analyses", json=body,
        headers={**bearer(acme_token), "Idempotency-Key": "shared"},
    )
    assert demo.status_code == 202, demo.text
    assert acme.status_code == 202, acme.text
    assert demo.json()["id"] != acme.json()["id"]

    with client.app.state.factory() as session:
        rows = session.scalars(select(Analysis).where(
            Analysis.id.in_([demo.json()["id"], acme.json()["id"]]),
        )).all()
    assert {row.idempotency_key for row in rows} == {"acme:shared"}
    assert {(row.tenant_id, row.id) for row in rows} == {
        ("demo", demo.json()["id"]), (ACME_TENANT, acme.json()["id"]),
    }

    demo_replay = client.post(
        "/api/analyses", json=body,
        headers={**bearer(demo_token), "Idempotency-Key": "acme:shared"},
    )
    acme_replay = client.post(
        "/api/analyses", json=body,
        headers={**bearer(acme_token), "Idempotency-Key": "shared"},
    )
    assert demo_replay.json()["id"] == demo.json()["id"] and demo_replay.json()["replayed"]
    assert acme_replay.json()["id"] == acme.json()["id"] and acme_replay.json()["replayed"]


def test_long_scoped_analysis_key_fits_storage(client):
    add_acme_tenant(client)
    acme_token = login(client, ACME_EMAIL, ACME_PASSWORD, ACME_TENANT)
    key = "k" * 100
    created = client.post(
        "/api/analyses", json={},
        headers={**bearer(acme_token), "Idempotency-Key": key},
    )
    assert created.status_code == 202, created.text
    with client.app.state.factory() as session:
        row = session.get(Analysis, created.json()["id"])
        assert row.idempotency_key == f"{ACME_TENANT}:{key}"
        assert len(row.idempotency_key) == 105
    replay = client.post(
        "/api/analyses", json={},
        headers={**bearer(acme_token), "Idempotency-Key": key},
    )
    assert replay.json()["id"] == created.json()["id"] and replay.json()["replayed"]


def test_cross_tenant_execution_idempotency_keys_do_not_collide(client, recommendation):
    add_acme_tenant(client)
    acme_approver = login(client, ACME_APPROVER_EMAIL, ACME_APPROVER_PASSWORD, ACME_TENANT)
    demo_approver = login(client, "approver@demo.local", "demo-approver")

    acme_recommendation = add_acme_approved_recommendation(client)
    approved = client.post(
        f"/api/recommendations/{recommendation}/approve",
        json={"reason": "跨租户幂等键隔离验证"}, headers=bearer(demo_approver),
    )
    assert approved.status_code == 200, approved.text

    demo_execution = client.post(
        f"/api/recommendations/{recommendation}/execute",
        headers={**bearer(demo_approver), "Idempotency-Key": "acme:exec"},
    )
    acme_execution = client.post(
        f"/api/recommendations/{acme_recommendation}/execute",
        headers={**bearer(acme_approver), "Idempotency-Key": "exec"},
    )
    assert demo_execution.status_code == 200, demo_execution.text
    assert acme_execution.status_code == 200, acme_execution.text
    assert demo_execution.json()["execution_id"] != acme_execution.json()["execution_id"]

    with client.app.state.factory() as session:
        rows = session.scalars(select(Execution).where(
            Execution.id.in_([
                demo_execution.json()["execution_id"], acme_execution.json()["execution_id"],
            ]),
        )).all()
    assert {row.idempotency_key for row in rows} == {"acme:exec"}
    assert {row.tenant_id for row in rows} == {"demo", ACME_TENANT}


def test_long_scoped_execution_key_fits_storage(client):
    add_acme_tenant(client)
    acme_approver = login(client, ACME_APPROVER_EMAIL, ACME_APPROVER_PASSWORD, ACME_TENANT)
    target = add_acme_approved_recommendation(client)
    key = "e" * 100
    executed = client.post(
        f"/api/recommendations/{target}/execute",
        headers={**bearer(acme_approver), "Idempotency-Key": key},
    )
    assert executed.status_code == 200, executed.text
    with client.app.state.factory() as session:
        row = session.get(Execution, executed.json()["execution_id"])
        assert row.idempotency_key == f"{ACME_TENANT}:{key}"
        assert len(row.idempotency_key) == 105


def test_cross_tenant_analysis_and_retry_are_hidden(client):
    add_acme_tenant(client)
    acme_token = login(client, ACME_EMAIL, ACME_PASSWORD, ACME_TENANT)
    created = client.post(
        "/api/analyses",
        json={"start_date": str(PERIOD_START), "end_date": str(PERIOD_END)},
        headers={**bearer(acme_token), "Idempotency-Key": "acme-analysis-1"},
    )
    assert created.status_code == 202, created.text
    analysis_id = created.json()["id"]
    detail = client.get(f"/api/analyses/{analysis_id}", headers=bearer(acme_token))
    assert detail.status_code == 200

    demo_token = login(client, "analyst@demo.local", "demo-analyst")
    assert client.get(f"/api/analyses/{analysis_id}", headers=bearer(demo_token)).status_code == 404
    listed = client.get("/api/analyses", headers=bearer(demo_token)).json()["items"]
    assert analysis_id not in {item["id"] for item in listed}
    retried = client.post(f"/api/analyses/{analysis_id}/retry", headers=bearer(demo_token))
    assert retried.status_code == 404


def test_cross_tenant_recommendation_is_hidden(client, recommendation):
    add_acme_tenant(client)
    acme_token = login(client, ACME_EMAIL, ACME_PASSWORD, ACME_TENANT)
    acme_approver = login(client, ACME_APPROVER_EMAIL, ACME_APPROVER_PASSWORD, ACME_TENANT)
    assert client.get(f"/api/recommendations/{recommendation}", headers=bearer(acme_token)).status_code == 404
    assert client.post(
        f"/api/recommendations/{recommendation}/approve",
        json={"reason": "cross tenant"}, headers=bearer(acme_approver),
    ).status_code == 404
    assert client.post(
        f"/api/recommendations/{recommendation}/execute",
        headers={**bearer(acme_approver), "Idempotency-Key": "acme-execute-1"},
    ).status_code == 404
    assert client.get("/api/recommendations", headers=bearer(acme_token)).json()["items"] == []


def test_cross_tenant_knowledge_isolation_and_same_content_allowed(client):
    add_acme_tenant(client)
    acme_token = login(client, ACME_EMAIL, ACME_PASSWORD, ACME_TENANT)
    acme_approver = login(client, ACME_APPROVER_EMAIL, ACME_APPROVER_PASSWORD, ACME_TENANT)
    content = "# Acme SOP\n\nAcme 专属退款流程。"
    created = client.post(
        "/api/knowledge/documents",
        json={"title": "Acme SOP", "source": "synthetic://acme/sop", "content": content},
        headers=bearer(acme_approver),
    )
    assert created.status_code == 201, created.text
    acme_doc_id = created.json()["id"]

    # Same content in another tenant must be a separate document, not a shared row.
    demo_approver = login(client, "approver@demo.local", "demo-approver")
    demo_created = client.post(
        "/api/knowledge/documents",
        json={"title": "Acme SOP", "source": "synthetic://demo/acme-sop", "content": content},
        headers=bearer(demo_approver),
    )
    assert demo_created.status_code == 201, demo_created.text
    assert demo_created.json()["id"] != acme_doc_id

    acme_docs = client.get("/api/knowledge/documents", headers=bearer(acme_token)).json()["items"]
    assert {item["id"] for item in acme_docs} == {acme_doc_id}
    demo_docs = client.get("/api/knowledge/documents", headers=bearer(demo_approver)).json()["items"]
    assert acme_doc_id not in {item["id"] for item in demo_docs}

    acme_search = client.post(
        "/api/knowledge/search", json={"query": "Acme 退款流程", "top_k": 3},
        headers=bearer(acme_token),
    ).json()
    assert not acme_search["no_answer"]
    assert all(hit["document_id"] == acme_doc_id for hit in acme_search["hits"])

    assert client.post(
        f"/api/knowledge/documents/{acme_doc_id}/reindex", headers=bearer(demo_approver),
    ).status_code == 404
    assert client.delete(
        f"/api/knowledge/documents/{acme_doc_id}", headers=bearer(demo_approver),
    ).status_code == 404


def test_cross_tenant_audit_logs_are_isolated(client):
    add_acme_tenant(client)
    acme_token = login(client, ACME_EMAIL, ACME_PASSWORD, ACME_TENANT)
    demo_token = login(client, "analyst@demo.local", "demo-analyst")
    created = client.post(
        "/api/analyses",
        json={"start_date": str(PERIOD_START), "end_date": str(PERIOD_END)},
        headers={**bearer(acme_token), "Idempotency-Key": "acme-audit-1"},
    ).json()
    acme_logs = client.get("/api/audit-logs", headers=bearer(acme_token)).json()["items"]
    demo_logs = client.get("/api/audit-logs", headers=bearer(demo_token)).json()["items"]
    assert created["id"] in {item["object_id"] for item in acme_logs}
    assert created["id"] not in {item["object_id"] for item in demo_logs}


def test_denied_request_audit_stays_in_the_actor_tenant(client):
    add_acme_tenant(client)
    token = login(client, ACME_EMAIL, ACME_PASSWORD, ACME_TENANT)
    denied = client.post(
        "/api/knowledge/documents",
        json={"title": "Denied", "source": "synthetic://acme/denied", "content": "Denied upload."},
        headers=bearer(token),
    )
    assert denied.status_code == 403, denied.text
    request_id = denied.headers["X-Request-ID"]
    with client.app.state.factory() as session:
        rows = session.scalars(
            select(AuditLog).where(AuditLog.request_id == request_id)
        ).all()
    assert len(rows) == 1
    assert rows[0].tenant_id == ACME_TENANT
    assert rows[0].action == "request.documents.denied"


def test_demo_endpoint_scopes_stores_for_authenticated_tenant(client):
    add_acme_tenant(client)
    acme_token = login(client, ACME_EMAIL, ACME_PASSWORD, ACME_TENANT)
    body = client.get("/api/demo", headers=bearer(acme_token)).json()
    assert [store["id"] for store in body["stores"]] == ["acme-store"]
    assert body["data_range"] == {
        "start_date": str(PERIOD_START - timedelta(days=2)),
        "end_date": str(PERIOD_END),
    }
    demo_body = client.get("/api/demo").json()
    assert {store["id"] for store in demo_body["stores"]} == {"us-store", "eu-store"}
