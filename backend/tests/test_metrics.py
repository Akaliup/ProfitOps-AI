from datetime import date

import pytest
from sqlalchemy import delete, func, select, update

from app.errors import DomainError
from app.metrics import analyze_drivers, build_report, calculate_totals, fetch_metrics, verify_evidence
from app.models import DailyOperation, Order
from app.schemas import MetricsInput, Period, Totals
from app.seed import seed_demo


@pytest.mark.parametrize("values,net_sales,profit,margin", [
    ({"sales": 10000, "discounts": 1000, "refunds": 500, "cogs": 3000, "ad_spend": 1000, "fulfillment": 500, "platform_fees": 300, "other_costs": 200}, 8500, 3500, 41.18),
    ({}, 0, 0, None),
    ({"sales": 100, "refunds": 200, "ad_spend": 50}, -100, -150, 150.0),
    ({"sales": 1_000_000_000_001, "cogs": 1}, 1_000_000_000_001, 1_000_000_000_000, 100.0),
])
def test_profit_exact(values, net_sales, profit, margin):
    value = calculate_totals(Totals(**values))
    assert (value.net_sales, value.net_profit, value.margin_pct) == (net_sales, profit, margin)


def test_seed_repeat_and_real_anomaly(client, analyst):
    factory = client.app.state.factory
    with factory() as session:
        order_count = session.scalar(select(func.count()).select_from(Order))
    assert order_count > 1000
    assert seed_demo(factory)["seeded"] is False
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Order)) == order_count
    report = client.get("/api/metrics", headers=analyst).json()
    assert report["current"]["sales"] > report["previous"]["sales"]
    assert report["current"]["net_profit"] < report["previous"]["net_profit"]
    assert report["data_quality"]["expected_records"] == 360
    assert report["data_quality"]["complete"] is True
    assert report["drivers"][0]["metric"] == "ad_spend"
    assert sum(item["contribution"] for item in report["drivers"]) == report["changes"]["net_profit"]["absolute"]
    assert report["period"] == {"start_date": "2026-10-01", "end_date": "2026-10-30"}
    assert report["previous_period"] == {"start_date": "2026-09-01", "end_date": "2026-09-30"}
    assert sum(row["sales"] for row in report["trend"][30:]) == report["current"]["sales"]
    assert sum(row["current"]["net_profit"] for row in report["sku_breakdown"]) == report["current"]["net_profit"]


def test_equal_length_scope_and_store(client, analyst):
    result = client.get("/api/metrics?start_date=2026-10-24&end_date=2026-10-30&store_id=us-store", headers=analyst).json()
    assert result["previous_period"] == {"start_date": "2026-10-17", "end_date": "2026-10-23"}
    assert result["data_quality"]["expected_records"] == 42
    assert len(result["trend"]) == 14


@pytest.mark.parametrize("query", ["start_date=2026-10-30", "start_date=2026-10-30&end_date=2026-10-01", "start_date=2026-01-01&end_date=2026-10-30"])
def test_invalid_ranges(client, analyst, query):
    response = client.get(f"/api/metrics?{query}", headers=analyst)
    assert response.status_code == 422
    assert "request_id" in response.json()


def test_missing_and_incomplete_records_block_evidence(client):
    factory = client.app.state.factory
    with factory.begin() as session:
        row = session.scalar(select(DailyOperation))
        session.delete(row)
        session.execute(update(DailyOperation).where(DailyOperation.date == date(2026, 10, 30)).values(complete=False))
    with factory() as session:
        raw = fetch_metrics(session, MetricsInput(period=Period(start_date="2026-10-01", end_date="2026-10-30")))
    report = build_report(raw)
    assert report.data_quality.missing_records == 1
    assert report.data_quality.incomplete_records == 6
    with pytest.raises(DomainError, match="不完整"):
        verify_evidence(report, analyze_drivers(report))


def test_tampered_evidence_rejected(client):
    with client.app.state.factory() as session:
        raw = fetch_metrics(session, MetricsInput(period=Period(start_date="2026-10-01", end_date="2026-10-30")))
    report = build_report(raw)
    findings = analyze_drivers(report)
    verify_evidence(report, findings)
    findings.evidence[0]["current"] += 1
    with pytest.raises(DomainError, match="不一致"):
        verify_evidence(report, findings)
