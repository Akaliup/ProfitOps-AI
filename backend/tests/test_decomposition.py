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


def test_new_and_discontinued_skus_close_sales_change_without_imputed_asp():
    previous = Totals(sales=11000, units=8)
    current = Totals(sales=16001, units=10)
    rows = [
        {"sku": "TRAVEL-BAG-01", "current": {"sales": 16001, "units": 10}, "previous": {"sales": 6000, "units": 6}},
        {"sku": "THERMO-02", "current": {"sales": 0, "units": 0}, "previous": {"sales": 5000, "units": 2}},
        {"sku": "USB-HUB-03", "current": {"sales": 0, "units": 0}, "previous": {"sales": 0, "units": 0}},
    ]
    result = decompose_profit(raw(current, previous, rows))
    assert result["status"] == "OK"
    assert result["contributions"] == {"volume": 2750, "price": 6001, "mix": -3750, "residual": 0}
    assert sum(result["contributions"].values()) == result["sales_change"] == 5001
    assert result["sku_movement"]["new_sales"] == 0
    assert result["sku_movement"]["discontinued_sales"] == 5000
    assert "No ASP is imputed" in result["sku_movement"]["rule"]


def test_new_sku_sales_stay_inside_volume_and_mix():
    previous = Totals(sales=1000, units=10)
    current = Totals(sales=1500, units=12)
    rows = [
        {"sku": "KEEP", "current": {"sales": 1000, "units": 10}, "previous": {"sales": 1000, "units": 10}},
        {"sku": "NEW", "current": {"sales": 500, "units": 2}, "previous": {"sales": 0, "units": 0}},
    ]
    result = decompose_profit(raw(current, previous, rows))
    assert result["contributions"] == {"volume": 200, "price": 0, "mix": 300, "residual": 0}
    assert result["sku_movement"]["new_sales"] == 500
    assert result["sku_movement"]["discontinued_sales"] == 0
    assert sum(result["contributions"].values()) == result["sales_change"]


def test_unpriced_sales_with_zero_units_are_not_dropped():
    previous = Totals(sales=1000, units=10)
    current = Totals(sales=1100, units=10)
    rows = [
        {"sku": "KEEP", "current": {"sales": 900, "units": 10}, "previous": {"sales": 900, "units": 10}},
        {"sku": "UNPRICED", "current": {"sales": 200, "units": 0}, "previous": {"sales": 100, "units": 0}},
    ]
    result = decompose_profit(raw(current, previous, rows))
    assert result["status"] == "OK"
    assert result["contributions"]["mix"] == 100
    assert result["contributions"]["residual"] == 0
    assert sum(result["contributions"].values()) == result["sales_change"] == 100
