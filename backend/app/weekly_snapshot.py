"""Read and derive citations from a saved snapshot, never from the live ledger."""
from copy import deepcopy

from pydantic import Field
from sqlalchemy import select

from .errors import DomainError
from .models import WeeklyReport
from .schemas import StrictModel


class SavedWeeklyReportInput(StrictModel):
    report_id: str = Field(min_length=1, max_length=36)


class SavedWeeklyReportOutput(StrictModel):
    report_id: str
    period: dict
    limitations: list[str]
    metrics: dict
    findings: dict
    stores: list[dict]
    sku_diagnostics: list[dict]
    inventory: dict
    analysis: dict
    citable_evidence: list[dict]


def require_report(session, tenant_id, report_id):
    report = session.scalar(select(WeeklyReport).where(
        WeeklyReport.tenant_id == tenant_id, WeeklyReport.id == report_id))
    if report is None:
        raise DomainError("report_not_found", "周报不存在", 404)
    return report


def _evidence_value(value):
    """Keep citable evidence compact while retaining verification facts."""
    if not isinstance(value, dict):
        return value
    allowed = {
        "id", "source", "metric", "current", "previous", "contribution",
        "period", "store_id", "sku", "currency",
        "profit_delta", "ad_spend_delta", "refund_delta", "ad_growth_alert",
        "risk_skus", "name", "evidence_id", "risk", "reorder_quantity", "on_hand",
        "reserved", "available", "inbound_quantity", "timely_inbound", "daily_sales",
        "coverage_days", "lead_time_days", "safety_stock_days", "safety_stock",
        "reorder_point", "snapshot_date", "observed_days", "status", "as_of",
        "start_date", "end_date", "inbound_eta", "moq", "case_pack", "source_id",
        "import_batch_id", "product_id", "bridge", "data_quality", "complete",
        "sales", "discounts", "refunds", "cogs", "ad_spend", "fulfillment",
        "platform_fees", "other_costs", "net_sales", "net_profit", "margin_pct",
        "orders", "units",
    }
    return {key: _evidence_value(item) for key, item in value.items()
            if key in allowed and not isinstance(item, list)}


def snapshot_view(report):
    body = deepcopy(report.body)
    evidence = []
    for item in body.get("evidence", []):
        if "value" in item:
            evidence.append({key: item[key] for key in ("id", "source") if key in item}
                            | {"value": _evidence_value(item["value"])})
        else:
            evidence.append(_evidence_value(item))
    for row in body.get("stores", []):
        value = _evidence_value(row)
        for period in ("current", "previous"):
            value[period] = {key: amount for key, amount in value[period].items()
                             if key in {"sales", "net_sales", "net_profit", "margin_pct", "ad_spend", "refunds", "orders", "units"}}
        evidence.append({"id": f"{row['store_id']}:store", "source": "snapshot.stores", "value": value})
    for row in body.get("sku_diagnostics", []):
        evidence.append({"id": f"{row['store_id']}:sku:{row['sku']}", "source": "snapshot.sku_diagnostics", "value": _evidence_value(row)})
    return SavedWeeklyReportOutput(
        report_id=report.id, period=body["period"], limitations=body.get("limitations", []),
        metrics={k: v for k, v in body["metrics"].items() if k in
                 {"data_quality", "bridge_verified", "current", "previous", "previous_period", "store_id"}},
        findings={"conclusion": body["findings"]["conclusion"]},
        stores=[{k: r[k] for k in ("store_id", "name", "profit_delta", "risk_skus") if k in r}
                for r in body.get("stores", [])],
        sku_diagnostics=[{k: r[k] for k in ("store_id", "sku", "profit_delta", "ad_spend_delta", "refund_delta", "ad_growth_alert") if k in r}
                         for r in body.get("sku_diagnostics", [])],
        inventory={"items": [{k: r[k] for k in ("evidence_id", "sku", "store_id", "risk", "reorder_quantity") if k in r}
                              for r in body.get("inventory", {}).get("items", [])]},
        analysis={"status": body.get("analysis", {}).get("status")}, citable_evidence=evidence)


def get_saved_weekly_report(payload, context):
    if not context.report_id or payload.report_id != context.report_id:
        raise DomainError("llm_tool_scope_mismatch", "工具参数超出本周报范围", 422)
    with context.factory() as session:
        return snapshot_view(require_report(session, context.actor.tenant_id, payload.report_id))
