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


def snapshot_view(report):
    body = deepcopy(report.body)
    evidence = body.get("evidence", [])
    for row in body.get("stores", []):
        evidence.append({"id": f"{row['store_id']}:store", "source": "snapshot.stores", "value": row})
    for row in body.get("sku_diagnostics", []):
        evidence.append({"id": f"{row['store_id']}:sku:{row['sku']}", "source": "snapshot.sku_diagnostics", "value": row})
    return SavedWeeklyReportOutput(
        report_id=report.id, period=body["period"], limitations=body.get("limitations", []),
        metrics={k: v for k, v in body["metrics"].items() if k in
                 {"data_quality", "bridge_verified", "current", "previous", "changes", "store_id"}},
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
