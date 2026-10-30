"""Build the synthetic LLM evaluation fixtures.

Every amount is an integer USD cent. The fixtures are synthetic, not real
company data. Expected labels come only from the deterministic business
functions in ``app.metrics`` and ``app.services``; no LLM is involved.

Run from the project root:
    .venv/Scripts/python.exe backend/evals/fixtures.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.errors import DomainError  # noqa: E402
from app.metrics import analyze_drivers, build_report, calculate_totals, verify_evidence  # noqa: E402
from app.schemas import DataQuality, Period, RawMetrics, Totals  # noqa: E402
from app.services import plan_action  # noqa: E402

DATASET_ID = "profitops-llm-eval-v1"
CASE_DIR = Path(__file__).resolve().parent / "llm_cases"
PERIOD = Period(start_date=date(2026, 10, 1), end_date=date(2026, 10, 30))
PREVIOUS_PERIOD = Period(start_date=date(2026, 9, 1), end_date=date(2026, 9, 30))
DAYS = 30
STORES = 2
MONEY_FIELDS = ("sales", "discounts", "refunds", "cogs", "ad_spend", "fulfillment", "platform_fees", "other_costs")
COUNT_FIELDS = ("orders", "units")

PRODUCTS = (
    {"product_id": "bag", "sku": "TRAVEL-BAG-01", "name": "轻量旅行背包", "inventory": 210, "inbound_inventory": 120},
    {"product_id": "bottle", "sku": "THERMO-02", "name": "不锈钢保温杯", "inventory": 270, "inbound_inventory": 120},
    {"product_id": "hub", "sku": "USB-HUB-03", "name": "多口扩展坞", "inventory": 330, "inbound_inventory": 120},
)


def amounts(
    *,
    sales: int = 0,
    discounts: int = 0,
    refunds: int = 0,
    cogs: int = 0,
    ad_spend: int = 0,
    fulfillment: int = 0,
    platform_fees: int = 0,
    other_costs: int = 0,
    orders: int = 0,
    units: int = 0,
) -> dict[str, int]:
    return {
        "sales": sales, "discounts": discounts, "refunds": refunds, "cogs": cogs,
        "ad_spend": ad_spend, "fulfillment": fulfillment,
        "platform_fees": platform_fees, "other_costs": other_costs,
        "orders": orders, "units": units,
    }


# Three SKUs per case. The sums are deliberately reconciled with the totals in
# the review report; the runner checks them again independently.
AD_CURRENT = {
    "bag": amounts(sales=60000, cogs=18000, ad_spend=25550, fulfillment=7000, platform_fees=3000, orders=60, units=60),
    "bottle": amounts(sales=36000, cogs=10800, ad_spend=12000, fulfillment=4200, platform_fees=1800, orders=45, units=45),
    "hub": amounts(sales=24000, cogs=7200, ad_spend=7450, fulfillment=2800, platform_fees=1200, orders=35, units=35),
}
AD_PREVIOUS = {
    "bag": amounts(sales=50000, cogs=15000, ad_spend=5000, fulfillment=5000, platform_fees=2500, orders=50, units=50),
    "bottle": amounts(sales=30000, cogs=9000, ad_spend=3000, fulfillment=3000, platform_fees=1500, orders=40, units=40),
    "hub": amounts(sales=20000, cogs=6000, ad_spend=2000, fulfillment=2000, platform_fees=1000, orders=30, units=30),
}

FULFILLMENT_CURRENT = {
    "bag": amounts(sales=55000, cogs=16500, ad_spend=5500, fulfillment=15000, platform_fees=2750, orders=55, units=55),
    "bottle": amounts(sales=33000, cogs=9900, ad_spend=3300, fulfillment=9000, platform_fees=1650, orders=41, units=41),
    "hub": amounts(sales=22000, cogs=6600, ad_spend=2200, fulfillment=6000, platform_fees=1100, orders=34, units=34),
}
FULFILLMENT_PREVIOUS = {
    "bag": amounts(sales=50000, cogs=15000, ad_spend=5000, fulfillment=5000, platform_fees=2500, orders=50, units=50),
    "bottle": amounts(sales=30000, cogs=9000, ad_spend=3000, fulfillment=3000, platform_fees=1500, orders=40, units=40),
    "hub": amounts(sales=20000, cogs=6000, ad_spend=2000, fulfillment=2000, platform_fees=1000, orders=30, units=30),
}

REFUND_CURRENT = {
    "bag": amounts(sales=55000, refunds=12500, cogs=16500, ad_spend=5000, fulfillment=5000, platform_fees=2500, orders=55, units=55),
    "bottle": amounts(sales=33000, refunds=7500, cogs=9900, ad_spend=3000, fulfillment=3000, platform_fees=1500, orders=41, units=41),
    "hub": amounts(sales=22000, refunds=5000, cogs=6600, ad_spend=2000, fulfillment=2000, platform_fees=1000, orders=34, units=34),
}
REFUND_PREVIOUS = {
    "bag": amounts(sales=50000, refunds=500, cogs=15000, ad_spend=5000, fulfillment=5000, platform_fees=2500, orders=50, units=50),
    "bottle": amounts(sales=30000, refunds=300, cogs=9000, ad_spend=3000, fulfillment=3000, platform_fees=1500, orders=40, units=40),
    "hub": amounts(sales=20000, refunds=200, cogs=6000, ad_spend=2000, fulfillment=2000, platform_fees=1000, orders=30, units=30),
}


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    title: str
    tags: tuple[str, ...]
    risk: str
    current: dict[str, dict[str, int]]
    previous: dict[str, dict[str, int]]
    complete: bool = True


CASES = (
    CaseSpec(
        "ad_spend_pressure", "广告费用上涨导致利润下降",
        ("anomaly", "ad_spend", "budget_action"), "high", AD_CURRENT, AD_PREVIOUS,
    ),
    CaseSpec(
        "fulfillment_pressure", "物流履约费用上涨导致利润下降",
        ("anomaly", "fulfillment", "review_action"), "high", FULFILLMENT_CURRENT, FULFILLMENT_PREVIOUS,
    ),
    CaseSpec(
        "refund_pressure", "退款上涨导致利润下降",
        ("anomaly", "refunds", "review_action"), "high", REFUND_CURRENT, REFUND_PREVIOUS,
    ),
    CaseSpec(
        "insufficient_data", "经营数据不完整，禁止生成无依据建议",
        ("guardrail", "insufficient_data", "no_llm"), "high", AD_CURRENT, AD_PREVIOUS, complete=False,
    ),
)


def data_quality_for(spec: CaseSpec) -> DataQuality:
    expected = STORES * len(PRODUCTS) * DAYS * 2
    if spec.complete:
        return DataQuality(
            complete=True, expected_records=expected, observed_records=expected,
            missing_records=0, incomplete_records=0, missing_examples=[], issues=[],
        )
    missing = 60
    examples = [f"2026-09-{day:02d}/us-store/TRAVEL-BAG-01" for day in range(1, 11)]
    return DataQuality(
        complete=False, expected_records=expected, observed_records=expected - missing,
        missing_records=missing, incomplete_records=0, missing_examples=examples,
        issues=[f"当前与上一周期缺少 {missing} 条每日经营记录；不将缺失值当成经营零值"],
    )


def allocate(total: int, days: int) -> list[int]:
    base, remainder = divmod(total, days)
    return [base + (1 if index < remainder else 0) for index in range(days)]


def build_trend(spec: CaseSpec) -> list[dict[str, Any]]:
    """Allocate each SKU's totals across 30 days, then aggregate by day."""
    entries: list[dict[str, Any]] = []
    for start, key in ((PREVIOUS_PERIOD.start_date, "previous"), (PERIOD.start_date, "current")):
        per_product = {
            product["product_id"]: {
                field: allocate((spec.previous if key == "previous" else spec.current)[product["product_id"]][field], DAYS)
                for field in (*MONEY_FIELDS, *COUNT_FIELDS)
            }
            for product in PRODUCTS
        }
        for index in range(DAYS):
            daily = {
                field: sum(per_product[product["product_id"]][field][index] for product in PRODUCTS)
                for field in (*MONEY_FIELDS, *COUNT_FIELDS)
            }
            entries.append({"date": str(start + timedelta(days=index)), **calculate_totals(Totals(**daily)).model_dump()})
    return entries


def build_raw(spec: CaseSpec) -> RawMetrics:
    def summed(period: dict[str, dict[str, int]]) -> dict[str, int]:
        return {
            field: sum(period[product["product_id"]][field] for product in PRODUCTS)
            for field in (*MONEY_FIELDS, *COUNT_FIELDS)
        }

    current = calculate_totals(Totals(**summed(spec.current)))
    previous = calculate_totals(Totals(**summed(spec.previous)))
    sku_breakdown = []
    for product in PRODUCTS:
        now = calculate_totals(Totals(**spec.current[product["product_id"]]))
        before = calculate_totals(Totals(**spec.previous[product["product_id"]]))
        sku_breakdown.append({
            "product_id": product["product_id"], "sku": product["sku"], "name": product["name"],
            "current": now.model_dump(), "previous": before.model_dump(),
            "profit_delta": now.net_profit - before.net_profit,
            "ad_spend_delta": now.ad_spend - before.ad_spend,
            "inventory": product["inventory"], "inbound_inventory": product["inbound_inventory"],
        })
    return RawMetrics(
        period=PERIOD, previous_period=PREVIOUS_PERIOD, store_id=None,
        current=current, previous=previous, trend=build_trend(spec), sku_breakdown=sku_breakdown,
        data_quality=data_quality_for(spec),
        source="synthetic://profitops-llm-eval-v1; 2 stores x 3 SKUs x 60 days; generated by backend/evals/fixtures.py",
    )


def build_expected(raw: RawMetrics) -> dict[str, Any]:
    report = build_report(raw)
    findings = analyze_drivers(report)
    error_code = None
    try:
        verify_evidence(report, findings)
    except DomainError as exc:
        error_code = exc.code
    action = None
    if error_code is None and findings.anomaly:
        plan = plan_action(report, findings)
        action = {
            "type": plan.action.type,
            "target_product_id": plan.action.product_id,
            "target_sku": plan.action.sku,
            "baseline_daily_budget": plan.action.baseline_daily_budget,
            "proposed_daily_budget": plan.action.proposed_daily_budget,
            "adjustment_pct": plan.action.adjustment_pct,
            "days": plan.days,
            "potential_cents": plan.potential,
            "is_budget": plan.is_budget,
        }
    llm_status = "llm_success" if error_code is None and findings.anomaly else "not_requested"
    llm_reason = None if llm_status == "llm_success" else ("insufficient_data" if error_code == "insufficient_data" else "no_anomaly")
    return {
        "anomaly": findings.anomaly,
        "primary_driver": findings.primary_driver.metric if findings.primary_driver else None,
        "primary_label": findings.primary_driver.label if findings.primary_driver else None,
        "profit_delta": findings.bridge_total,
        "bridge_verified": report.bridge_verified,
        "required_evidence_ids": [f"ev-{findings.primary_driver.metric}"] if findings.primary_driver else [],
        "llm_status": llm_status,
        "llm_reason": llm_reason,
        "error_code": error_code,
        "action": action,
    }


def build_case(spec: CaseSpec) -> dict[str, Any]:
    raw = build_raw(spec)
    expected = build_expected(raw)
    return {
        "case_id": spec.case_id,
        "title": spec.title,
        "source_type": "synthetic",
        "generation_method": (
            "backend/evals/fixtures.py v1: deterministic SKU amounts allocated over 30 days; "
            "expected labels from build_report/analyze_drivers/plan_action; no LLM"
        ),
        "synthetic_notes": (
            "Synthetic cross-border e-commerce ledger: 2 stores x 3 SKUs x 60 days aggregated. "
            "Not real company data; no PII."
        ),
        "tags": list(spec.tags),
        "risk": spec.risk,
        "input": raw.model_dump(mode="json"),
        "expected": expected,
        "review": {
            "sku_totals_reconciled": True,
            "bridge_reconciled": expected["bridge_verified"],
            "data_quality_complete": raw.data_quality.complete,
        },
    }


def review_markdown(cases: list[dict[str, Any]]) -> str:
    lines = [
        "# ProfitOps AI LLM 评测样本人工复核材料",
        "",
        f"- 数据集：`{DATASET_ID}`",
        "- 来源：**synthetic**，由 `backend/evals/fixtures.py` 程序合成；不是真实企业数据。",
        "- 周期：2026-09-01 至 2026-09-30（上一周期）与 2026-10-01 至 2026-10-30（当前周期）。",
        "- 规模：2 店铺 × 3 SKU × 60 天，按 SKU 汇总；金额单位为整数 USD 美分。",
        "- 标签：由 `build_report` / `analyze_drivers` / `plan_action` 生成，未调用任何 LLM。",
        "- 说明：本文件是人工复核材料，不代表已经由第三方完成签字复核。",
        "",
    ]
    for case in cases:
        raw = RawMetrics.model_validate(case["input"])
        expected = case["expected"]
        lines.extend([
            f"## {case['case_id']} · {case['title']}",
            "",
            f"- source_type：`{case['source_type']}`",
            f"- tags：{', '.join(case['tags'])}",
            f"- 数据完整性：complete={raw.data_quality.complete}；missing={raw.data_quality.missing_records}",
            "",
            "### 汇总对账",
            "",
            "| 指标 | 上一周期 | 当前周期 | 变化 |",
            "|---|---:|---:|---:|",
        ])
        for field in (*MONEY_FIELDS, "net_sales", "net_profit"):
            previous = getattr(raw.previous, field)
            current = getattr(raw.current, field)
            lines.append(f"| {field} | {previous} | {current} | {current - previous} |")
        lines.extend([
            "",
            "### SKU 明细",
            "",
            "| SKU | 当前销售额 | 当前广告 | 当前履约 | 当前退款 | 利润变化 | 广告变化 | 期末库存 | 在途库存 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for row in raw.sku_breakdown:
            lines.append(
                f"| {row['sku']} | {row['current']['sales']} | {row['current']['ad_spend']} | "
                f"{row['current']['fulfillment']} | {row['current']['refunds']} | "
                f"{row['profit_delta']} | {row['ad_spend_delta']} | {row['inventory']} | {row['inbound_inventory']} |"
            )
        action = expected["action"]
        lines.extend([
            "",
            "### 确定性 Oracle",
            "",
            f"- anomaly：`{expected['anomaly']}`",
            f"- primary_driver：`{expected['primary_driver']}`（{expected['primary_label']}）",
            f"- profit_delta / bridge_total：`{expected['profit_delta']}`",
            f"- bridge_verified：`{expected['bridge_verified']}`",
            f"- llm_status：`{expected['llm_status']}`；llm_reason：`{expected['llm_reason']}`",
            f"- error_code：`{expected['error_code']}`",
        ])
        if action:
            lines.extend([
                f"- action：`{action['type']}`，target=`{action['target_sku']}`，"
                f"baseline_daily_budget={action['baseline_daily_budget']}，"
                f"proposed_daily_budget={action['proposed_daily_budget']}，"
                f"potential_cents={action['potential_cents']}",
            ])
        else:
            lines.append("- action：无；数据不完整案例必须在 `verify_evidence` 处停止，不生成建议。")
        lines.extend([
            "",
            "### 算术校验点",
            "",
            "- `current` / `previous` 的 `net_sales`、`net_profit`、`margin_pct` 可由原始金额独立重算。",
            "- 3 个 SKU 的每个金额字段之和等于汇总金额。",
            "- 8 项利润桥贡献之和等于净利润变化。",
            "- 60 天 trend 的当前/上一周期金额分别加总等于 current/previous totals。",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def write_cases() -> dict[str, Any]:
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    cases = [build_case(spec) for spec in CASES]
    manifest_cases = []
    for case in cases:
        path = CASE_DIR / f"{case['case_id']}.json"
        path.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest_cases.append({
            "case_id": case["case_id"],
            "file": path.name,
            "title": case["title"],
            "tags": case["tags"],
            "risk": case["risk"],
            "source_type": case["source_type"],
            "expected_primary": case["expected"]["primary_driver"],
            "expected_action": case["expected"]["action"]["type"] if case["expected"]["action"] else None,
            "expected_llm_status": case["expected"]["llm_status"],
            "expected_error_code": case["expected"]["error_code"],
        })
    manifest = {
        "dataset_id": DATASET_ID,
        "source_type": "synthetic",
        "generation_method": (
            "backend/evals/fixtures.py v1: deterministic SKU amounts allocated over 30 days; "
            "expected labels from build_report/analyze_drivers/plan_action; no LLM"
        ),
        "synthetic_notes": "Synthetic cross-border e-commerce ledger; not real company data; no PII.",
        "period": PERIOD.model_dump(mode="json"),
        "previous_period": PREVIOUS_PERIOD.model_dump(mode="json"),
        "stores": STORES,
        "products": list(PRODUCTS),
        "cases": manifest_cases,
        "validation": {
            "all_sku_totals_reconciled": True,
            "all_bridge_totals_reconciled": all(case["expected"]["bridge_verified"] for case in cases),
            "labels_generated_by_deterministic_functions": True,
            "llm_used_for_labels": False,
        },
    }
    (CASE_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (CASE_DIR / "review.md").write_text(review_markdown(cases), encoding="utf-8")
    return manifest


def main() -> int:
    manifest = write_cases()
    print(json.dumps({
        "dataset_id": manifest["dataset_id"],
        "source_type": manifest["source_type"],
        "cases": [
            {
                "case_id": item["case_id"],
                "expected_primary": item["expected_primary"],
                "expected_action": item["expected_action"],
                "expected_llm_status": item["expected_llm_status"],
                "expected_error_code": item["expected_error_code"],
            }
            for item in manifest["cases"]
        ],
        "output_dir": str(CASE_DIR),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
