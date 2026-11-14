from app.metrics import build_report, decompose_profit
from app.schemas import DataQuality, Period, RawMetrics, Totals


def raw(current: Totals, previous: Totals, rows=None):
    return RawMetrics(
        period=Period(start_date="2026-11-08", end_date="2026-11-14"),
        previous_period=Period(start_date="2026-11-01", end_date="2026-11-07"),
        store_id=None, current=current, previous=previous, trend=[],
        sku_breakdown=rows or [], data_quality=DataQuality(
            complete=True, expected_records=1, observed_records=1,
            missing_records=0, incomplete_records=0, missing_examples=[], issues=[]),
    )


def test_volume_and_price_effect_close_to_sales_delta():
    previous = Totals(sales=1000, units=10)
    current = Totals(sales=1500, units=12)
    result = decompose_profit(raw(current, previous))
    assert result["status"] == "OK"
    assert result["contributions"]["volume"] == 200
    assert result["contributions"]["price"] == 300
    assert result["contributions"]["mix"] == 0
    assert result["contributions"]["residual"] == 0
    assert sum(result["contributions"].values()) == result["sales_change"]


def test_zero_units_abstains_from_asp():
    result = decompose_profit(raw(Totals(sales=100), Totals()))
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["current"]["asp"] is None
    assert result["contributions"]["price"] is None


def test_report_exposes_decomposition_evidence():
    report = build_report(raw(Totals(sales=1500, units=12), Totals(sales=1000, units=10)))
    assert report.decomposition["status"] == "OK"
    ids = {item["id"] for item in __import__("app.metrics", fromlist=["analyze_drivers"]).analyze_drivers(report).evidence}
    assert {"ev-volume", "ev-price", "ev-mix", "ev-residual"} <= ids
