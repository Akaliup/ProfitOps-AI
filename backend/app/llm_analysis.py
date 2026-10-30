"""Prompt construction and evidence-reference validation for the LLM node.

The model receives only deterministic metrics and evidence rows. It never computes
profit; its output is validated before any recommendation is created.
"""

from __future__ import annotations

import json
from typing import Any

from .schemas import DriverReport, LLMAnalysisOutput, MetricsReport

SYSTEM_PROMPT = """你是 ProfitOps AI 的跨境电商利润分析助手。你只负责解释与策略建议，不负责计算利润。

必须遵守：
1. 只能使用输入 JSON 中的数字与证据，不得编造、外推或改写销售额、成本、利润、百分比、日期或证据 ID。金额字段均为整数美分（USD cents）；如需用美元表述，必须除以 100。summary 不要逐项罗列所有指标。
2. root_causes 和 recommendations 的每一项都必须至少引用一个 evidence_ids，且只能引用 allowed_evidence_ids 中的 ID。
3. 建议必须与 planned_simulation_action 的目标 SKU 和动作类型一致。所有动作都是 simulation，不得建议调用真实广告、采购或支付系统。
4. 如果信息不足，写入 missing_information，不要虚构。
5. 只输出一个 json 对象，不要 markdown、代码块或额外文字。字段必须完整：
   summary: 字符串
   root_causes: [{cause, reasoning, evidence_ids}]
   evidence_references: [evidence_id]
   recommendations: [{action, rationale, evidence_ids, priority: high|medium|low}]
   expected_impact: {direction: improve|neutral|uncertain, horizon_days: 1-90, explanation}
   risks: [字符串]
   missing_information: [字符串]
   confidence: 0-1
6. 输出必须紧凑：summary 120–300 个中文字符；root_causes 1–3 条，每条 reasoning ≤ 120 字；recommendations 1–3 条，每条 rationale ≤ 120 字；risks 与 missing_information 各 ≤ 3 条、每条 ≤ 60 字；expected_impact.explanation ≤ 120 字。不要重复输入中的整段数据。
7. 用中文回答。"""

TOTAL_FIELDS = (
    "sales", "discounts", "refunds", "cogs", "ad_spend", "fulfillment",
    "platform_fees", "other_costs", "net_sales", "net_profit", "margin_pct", "orders", "units",
)


def build_llm_payload(report: MetricsReport, findings: DriverReport, action: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "task": "解释利润变化的会计驱动因素，并给出可人工审批的模拟动作建议",
        "period": report.period.model_dump(mode="json"),
        "previous_period": report.previous_period.model_dump(mode="json"),
        "store_id": report.store_id,
        "currency": "USD",
        "amount_unit": "integer USD cents; divide by 100 for USD",
        "current_totals": {field: getattr(report.current, field) for field in TOTAL_FIELDS},
        "previous_totals": {field: getattr(report.previous, field) for field in TOTAL_FIELDS},
        "changes": {field: report.changes[field].model_dump() for field in TOTAL_FIELDS if field in report.changes},
        "drivers": [driver.model_dump() for driver in report.drivers],
        "primary_driver": findings.primary_driver.model_dump() if findings.primary_driver else None,
        "evidence": findings.evidence,
        "sku_breakdown": [
            {
                "sku": row["sku"], "profit_delta": row["profit_delta"],
                "ad_spend_delta": row["ad_spend_delta"],
                "inventory": row["inventory"], "inbound_inventory": row["inbound_inventory"],
            }
            for row in report.sku_breakdown
        ],
        "planned_simulation_action": action,
        "allowed_evidence_ids": [item["id"] for item in findings.evidence],
        "output_requirements": {
            "all_claims_reference_evidence": True,
            "numbers_must_come_from_payload": True,
            "simulation_only": True,
        },
    }


def build_user_prompt(payload: dict[str, Any]) -> str:
    return "请基于以下确定性经营数据输出 json：\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def validate_evidence_references(output: LLMAnalysisOutput, evidence: list[dict[str, Any]]) -> list[str]:
    """Return unknown evidence ids referenced anywhere in the model output."""
    known = {item.get("id") for item in evidence}
    referenced = set(output.evidence_references)
    for cause in output.root_causes:
        referenced.update(cause.evidence_ids)
    for recommendation in output.recommendations:
        referenced.update(recommendation.evidence_ids)
    return sorted(reference for reference in referenced if reference not in known)
