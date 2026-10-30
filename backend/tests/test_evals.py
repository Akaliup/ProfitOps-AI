import json
from pathlib import Path

import pytest

from app.metrics import analyze_drivers, build_report, verify_evidence
from app.schemas import DataQuality, RawMetrics, Totals

CASES = json.loads((Path(__file__).parents[1] / "evals" / "cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[item["id"] for item in CASES])
def test_business_eval(case):
    raw = RawMetrics(
        period={"start_date": "2026-10-01", "end_date": "2026-10-30"},
        previous_period={"start_date": "2026-09-01", "end_date": "2026-09-30"},
        store_id=None, current=Totals(**case["current"]), previous=Totals(**case["previous"]),
        trend=[], sku_breakdown=[],
        data_quality=DataQuality(complete=True, expected_records=60, observed_records=60, missing_records=0, incomplete_records=0, missing_examples=[], issues=[]),
    )
    report = build_report(raw)
    findings = analyze_drivers(report)
    verify_evidence(report, findings)
    assert findings.anomaly is case["expected_anomaly"]
    assert findings.primary_driver.metric == case["expected_primary"]
    assert findings.bridge_total == case["expected_profit_delta"]
    assert all(evidence["period"] == {"start_date": "2026-10-01", "end_date": "2026-10-30"} and evidence["source"] for evidence in findings.evidence)
    if not case["previous"]:
        assert report.changes["sales"].pct is None
