import csv
import io
from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, DailyOperation, ImportBatch, Order

ORDER_HEADER = (
    "order_id,store_id,sku,date,quantity,sales,discounts,refunds,cogs,"
    "fulfillment,platform_fees,other_costs"
)
ORDER_ROWS = [
    "IMP-1,us-store,TRAVEL-BAG-01,2026-11-10,1,49.00,3.92,0,16.00,6.50,7.35,0.90",
    "IMP-2,eu-store,THERMO-02,2026-11-10,2,64.00,5.12,0,20.00,8.60,9.60,1.80",
]
ORDER_CSV = "\n".join([ORDER_HEADER, *ORDER_ROWS]) + "\n"

OPERATION_HEADER = "store_id,sku,date,ad_spend,inventory,inbound_inventory,complete"
OPERATION_ROWS = [
    "us-store,TRAVEL-BAG-01,2026-11-10,125.00,210,120,true",
    "eu-store,THERMO-02,2026-11-10,65.50,180,60,false",
]
OPERATION_CSV = "\n".join([OPERATION_HEADER, *OPERATION_ROWS]) + "\n"


def csv_headers(token, **extra):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "text/csv",
        "X-Filename": "orders.csv",
        **extra,
    }


def post_csv(client, token, kind, text, suffix="", **extra):
    return client.post(
        f"/api/imports/{kind}{suffix}", headers=csv_headers(token, **extra),
        content=text.encode("utf-8"),
    )


def count(client, model):
    with client.app.state.factory() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_valid_orders_preview_does_not_write(client):
    before = count(client, Order)
    response = post_csv(client, "demo-approver", "orders", ORDER_CSV, "/preview")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["can_commit"] is True
    assert (body["total_rows"], body["accepted_rows"], body["rejected_rows"]) == (2, 2, 0)
    assert body["totals"] == {
        "rows": 2, "sales": 11300, "discounts": 904, "refunds": 0,
        "net_sales": 10396, "cogs": 3600, "ad_spend": 0, "fulfillment": 1510,
        "platform_fees": 1695, "other_costs": 270, "net_profit": 3321,
        "inventory": 0, "inbound_inventory": 0,
    }
    assert count(client, Order) == before
    assert {item["code"] for item in body["checks"]} >= {
        "parse_complete", "money_exact", "row_balance", "header_valid",
    }


def test_commit_requires_approver_and_writes_ledger(client, analyst, approver):
    before = count(client, Order)
    denied = post_csv(client, "demo-analyst", "orders", ORDER_CSV)
    assert denied.status_code == 403
    assert count(client, ImportBatch) == 0

    response = post_csv(client, "demo-approver", "orders", ORDER_CSV)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "committed"
    assert body["accepted_rows"] == 2
    assert body["id"]
    roundtrip = [item for item in body["checks"] if item["code"] == "db_roundtrip"]
    assert roundtrip and roundtrip[0]["ok"] is True

    assert count(client, Order) == before + 2
    with client.app.state.factory() as session:
        saved = session.scalar(select(Order).where(Order.id == "IMP-1"))
        assert saved.sales == 4900
        assert saved.discounts == 392
        assert saved.cogs == 1600
        batch = session.scalar(select(ImportBatch))
        assert batch.status == "committed" and batch.accepted_rows == 2
        actions = session.scalars(select(AuditLog.action)).all()
        assert "import.committed" in actions

    listed = client.get("/api/imports", headers=approver).json()["items"]
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]


def test_reupload_is_idempotent_noop(client):
    before = count(client, Order)
    assert post_csv(client, "demo-approver", "orders", ORDER_CSV).status_code == 200
    preview = post_csv(client, "demo-approver", "orders", ORDER_CSV, "/preview").json()
    assert preview["skipped_rows"] == 2
    assert preview["accepted_rows"] == 0
    assert preview["can_commit"] is True

    repeated = post_csv(client, "demo-approver", "orders", ORDER_CSV).json()
    assert repeated["status"] == "noop"
    assert repeated["replayed"] is True
    assert repeated["accepted_rows"] == 0
    assert count(client, Order) == before + 2


def test_conflicting_order_blocks_whole_file(client):
    assert post_csv(client, "demo-approver", "orders", ORDER_CSV).status_code == 200
    changed = ORDER_CSV.replace("1,49.00,3.92", "1,50.00,3.92")
    before = count(client, Order)
    response = post_csv(client, "demo-approver", "orders", changed)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "import_rejected"
    assert error["details"]["summary"]["rejected_rows"] == 1
    assert count(client, Order) == before


@pytest.mark.parametrize("mutate,code", [
    (lambda text: text.replace("49.00", "49.001"), "invalid_money"),
    (lambda text: text.replace("TRAVEL-BAG-01", "NOT-A-SKU"), "unknown_sku"),
    (lambda text: text.replace("us-store", "missing-store"), "unknown_store"),
    (lambda text: text.replace(",3.92,0,16.00", ",3.92,60.00,16.00"), "discount_exceeds_sales"),
    (lambda text: text.replace("2026-11-10", "2030-01-01"), "date_out_of_range"),
    (lambda text: text.replace("IMP-2", "IMP-1"), "duplicate_in_file"),
])
def test_row_validation_blocks_commit(client, mutate, code):
    response = post_csv(client, "demo-approver", "orders", mutate(ORDER_CSV), "/preview")
    assert response.status_code == 200
    body = response.json()
    assert body["can_commit"] is False
    assert code in body["issue_counts"]
    assert body["rejected_rows"] >= 1


def test_missing_header_and_encoding(client):
    bad_header = ORDER_CSV.replace(",other_costs", "")
    response = post_csv(client, "demo-approver", "orders", bad_header, "/preview")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "import_header_invalid"

    response = client.post(
        "/api/imports/orders/preview", headers=csv_headers("demo-approver"),
        content=b"\xff\xfe\x00bad",
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "import_encoding_invalid"


def test_operations_import_and_conflict(client):
    response = post_csv(client, "demo-approver", "operations", OPERATION_CSV)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "committed"
    assert body["totals"]["ad_spend"] == 19050
    assert body["totals"]["inventory"] == 390
    assert body["totals"]["inbound_inventory"] == 180
    with client.app.state.factory() as session:
        row = session.scalar(select(DailyOperation).where(
            DailyOperation.store_id == "eu-store",
            DailyOperation.product_id == "bottle",
            DailyOperation.date == date(2026, 11, 10),
        ))
        assert row is not None and row.complete is False

    again = post_csv(client, "demo-approver", "operations", OPERATION_CSV).json()
    assert again["status"] == "noop"

    changed = OPERATION_CSV.replace("125.00", "126.00")
    blocked = post_csv(client, "demo-approver", "operations", changed)
    assert blocked.status_code == 409
    assert "operation_conflict" in blocked.json()["error"]["details"]["summary"]["issue_counts"]


def test_invalid_kind_and_template(client):
    assert client.get("/api/imports/template/orders", headers={"Authorization": "Bearer demo-analyst"}).status_code == 200
    missing = client.get("/api/imports/template/unknown", headers={"Authorization": "Bearer demo-analyst"})
    assert missing.status_code == 404
    assert post_csv(client, "demo-approver", "unknown", ORDER_CSV, "/preview").status_code == 404


def test_imported_orders_are_visible_in_metrics(client, analyst):
    assert post_csv(client, "demo-approver", "orders", ORDER_CSV).status_code == 200
    metrics = client.get(
        "/api/metrics?start_date=2026-11-01&end_date=2026-11-10", headers=analyst,
    ).json()
    assert metrics["current"]["sales"] == 11300
    assert metrics["current"]["net_profit"] == 3321
    assert metrics["data_quality"]["complete"] is False


def test_csv_export_roundtrip_template(client):
    body = client.get(
        "/api/imports/template/orders", headers={"Authorization": "Bearer demo-analyst"},
    ).text
    rows = list(csv.reader(io.StringIO(body)))
    assert rows[0] == ORDER_HEADER.split(",")
    assert len(rows) == 2
