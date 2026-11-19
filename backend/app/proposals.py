"""Ledger-backed proposals on the existing recommendation state machine.

Inventory, budget and price checks become Recommendation rows. Approval and
simulation stay in ``decide`` / ``execute_once``. Nothing here calls a store,
an ad platform, a supplier or a paid model.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from pydantic import Field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import record
from .errors import DomainError
from .inventory import InventoryPolicy, inventory_report
from .metrics import analyze_drivers, build_report, fetch_metrics, ledger_fingerprint
from .models import Analysis, Order, Product, Recommendation, Store, new_id, utcnow
from .schemas import MetricsInput, StrictModel
from .security import Actor, require_role, scoped_idempotency_key
from .services import SimulationAction, plan_action


MARGIN_FLOOR = Decimal("28")
REORDER_RISKS = {"CRITICAL", "HIGH", "MEDIUM"}


class ProposalScope(StrictModel):
    store_id: str | None = Field(default=None, min_length=1, max_length=40)
    start_date: date | None = None
    end_date: date | None = None
    as_of: date | None = None


class PriceProposalInput(StrictModel):
    store_id: str = Field(min_length=1, max_length=40)
    sku: str = Field(min_length=1, max_length=50)
    proposed_price_cents: int = Field(ge=1, le=10_000_000_00)
    start_date: date | None = None
    end_date: date | None = None


def deal_margin_pct(price_cents: int, unit_cogs: Decimal, platform_fee: Decimal, promo: Decimal) -> Decimal:
    """Gross margin after unit cost, platform fee and promo allocation. Percent."""
    if price_cents <= 0:
        raise DomainError("invalid_price", "售价必须为正整数美分", 422)
    profit = Decimal(price_cents) - unit_cogs - platform_fee - promo
    return profit / Decimal(price_cents) * Decimal(100)


def _scope(session: Session, tenant_id: str, start: date | None, end: date | None, store_id: str | None) -> MetricsInput:
    from .metrics import resolve_scope
    from .schemas import AnalysisRequest

    try:
        request = AnalysisRequest(start_date=start, end_date=end, store_id=store_id)
    except ValueError as exc:
        raise DomainError("invalid_period", str(exc), 422) from exc
    return resolve_scope(session, request, tenant_id)


def _report(session, tenant_id, scope: MetricsInput):
    report = build_report(fetch_metrics(session, scope, tenant_id))
    if not report.data_quality.complete:
        raise DomainError(
            "insufficient_data",
            "账本覆盖不完整，不能生成可审批提案",
            409,
            report.data_quality.model_dump(mode="json"),
        )
    return report, analyze_drivers(report)


def _not_requested_llm() -> dict:
    return {
        "status": "not_requested", "configured": False, "provider": "disabled",
        "model": "deterministic-rules-v1", "reason": "proposal_rules",
        "attempts": 0, "latency_ms": None, "usage": None, "output": None, "error": None,
        "native_tools": False,
    }


def _impact(explanation: str, days: int, low: int = 0, high: int = 0) -> dict:
    return {
        "kind": "conditional_scenario", "horizon_days": days,
        "profit_improvement_min": low, "profit_improvement_max": high,
        "explanation": explanation,
    }


def _save(
    session: Session, actor: Actor, request_id: str, *,
    kind: str, report, findings, action: SimulationAction, title: str,
    recommendation: str, proposal: dict, risks: list[str], missing: list[str],
) -> tuple[Recommendation, bool]:
    fingerprint = ledger_fingerprint(session, MetricsInput(period=report.period, store_id=report.store_id), actor.tenant_id)
    digest = hashlib.sha256(
        f"{kind}|{report.store_id}|{report.period.end_date.isoformat()}|{fingerprint}".encode()
    ).hexdigest()[:24]
    raw_key = f"proposal-{digest}"
    stored_key = scoped_idempotency_key(actor.tenant_id, raw_key)
    existing = session.scalar(select(Analysis).where(
        Analysis.tenant_id == actor.tenant_id, Analysis.idempotency_key == stored_key,
    ))
    if existing is not None:
        row = session.scalar(select(Recommendation).where(
            Recommendation.analysis_id == existing.id, Recommendation.tenant_id == actor.tenant_id,
        ))
        if row is None:
            raise DomainError("proposal_incomplete", "提案分析已存在但建议缺失", 409)
        return row, True
    dumped = findings.model_dump(mode="json")
    now = utcnow().isoformat().replace("+00:00", "Z")
    analysis = Analysis(
        id=new_id(), tenant_id=actor.tenant_id, idempotency_key=stored_key,
        request={"period": report.period.model_dump(mode="json"), "store_id": report.store_id, "kind": kind},
        actor=actor.id, request_id=request_id, status="waiting_approval",
        steps=[{
            "name": "deterministic_proposal", "label": "按账本规则起草提案",
            "status": "completed", "started_at": now, "request_id": request_id,
            "phase": "proposal", "duration_ms": 0.0,
            "summary": "确定性规则生成，未调用模型，未连接外部系统",
        }],
        result={
            "metrics": report.model_dump(mode="json"),
            "findings": dumped,
            "engine": "deterministic-rules-v1",
            "llm": _not_requested_llm(),
        },
        attempts=1, max_attempts=1,
    )
    session.add(analysis)
    session.flush()
    body = {
        "problem": findings.conclusion,
        "period": report.period.model_dump(mode="json"),
        "previous_period": report.previous_period.model_dump(mode="json"),
        "store_id": report.store_id, "currency": "USD",
        "engine": "deterministic-rules-v1",
        "llm": _not_requested_llm(),
        "knowledge": None,
        "evidence": dumped["evidence"],
        "primary_driver": dumped["primary_driver"],
        "recommendation": recommendation,
        "expected_impact": proposal.get("expected_impact") or _impact("情景测算，不是已实现利润。", 7),
        "risks": risks,
        "missing_information": missing,
        "assumptions": [
            "金额为 USD 整数美分",
            "动作 simulation=true，不产生外部副作用",
            "证据来自当前租户账本，审批时会重新核对",
        ],
        "source": report.source,
        "proposal": proposal,
    }
    row = Recommendation(
        id=new_id(), tenant_id=actor.tenant_id, analysis_id=analysis.id,
        status="pending_approval", title=title[:200], body=body,
        action=action.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    analysis.recommendation_id = row.id
    record(session, actor, "recommendation.proposed", "recommendation", row.id, request_id, details={"kind": kind, "simulation": True})
    record(session, actor, "recommendation.pending_approval", "recommendation", row.id, request_id, details={"kind": kind})
    return row, False


def _lines_from_inventory(session, tenant_id, as_of: date, store_id: str | None) -> list[dict]:
    report = inventory_report(session, tenant_id, as_of, store_id, InventoryPolicy())
    lines = []
    for item in report["items"]:
        quantity = item.get("reorder_quantity") or 0
        if item.get("risk") not in REORDER_RISKS or not quantity:
            continue
        if item.get("status") in {"insufficient_data", "no_demand"}:
            continue
        lines.append({
            "store_id": item["store_id"], "sku": item["sku"], "product_id": item["product_id"],
            "on_hand": item.get("on_hand"), "available": item.get("available"),
            "reorder_quantity": quantity, "risk": item["risk"],
            "coverage_days": item.get("coverage_days"),
            "evidence_id": item.get("evidence_id"),
            "daily_sales": item.get("daily_sales"),
        })
    lines.sort(key=lambda row: (-row["reorder_quantity"], row["sku"]))
    return lines


def create_replenishment(session: Session, actor: Actor, request_id: str, payload: ProposalScope):
    require_role(actor, "analyst", "approver")
    scope = _scope(session, actor.tenant_id, payload.start_date, payload.end_date, payload.store_id)
    as_of = payload.as_of or scope.period.end_date
    if as_of > utcnow().date():
        raise DomainError("invalid_period", "库存时点不能晚于今天", 422)
    report, findings = _report(session, actor.tenant_id, scope)
    lines = _lines_from_inventory(session, actor.tenant_id, as_of, payload.store_id)
    if not lines:
        raise DomainError(
            "no_replenishment",
            "没有低于补货门槛的 SKU。超储和数据不足的 SKU 不会进入提案。请先导入库存快照。",
            409, {"as_of": str(as_of)},
        )
    primary = lines[0]
    action = SimulationAction(
        type="simulate_replenishment", product_id=primary["product_id"], sku=primary["sku"],
        source_period=report.period.model_dump(mode="json"),
        replenish_quantity=primary["reorder_quantity"], line_count=len(lines),
        margin_ok=True, margin_floor_pct=float(MARGIN_FLOOR),
    )
    text = "；".join(f"{row['sku']} × {row['reorder_quantity']}" for row in lines[:8])
    proposal = {
        "kind": "replenishment", "as_of": str(as_of), "lines": lines,
        "margin_check": {"ok": True, "text": "补货不改变售价，毛利红线不拦截。仍须人工审批后才写入模拟记录。"},
        "diffs": [
            {"item": f"{row['sku']} 建议补货", "before": "0", "after": str(row["reorder_quantity"])}
            for row in lines
        ],
        "evidence_notes": [f"{row['evidence_id']} · 风险 {row['risk']} · 可售天数 {row['coverage_days']}" for row in lines],
        "expected_impact": _impact("补货预览不产生采购单，也不承诺利润改善。", 14),
    }
    line_key = hashlib.sha256(json.dumps(lines, sort_keys=True, default=str).encode()).hexdigest()[:12]
    row, replayed = _save(
        session, actor, request_id, kind=f"replenish-{as_of.isoformat()}-{line_key}", report=report, findings=findings, action=action,
        title=f"{primary['sku']} 等 {len(lines)} 个 SKU · 模拟补货",
        recommendation=f"为低于补货门槛的 SKU 生成模拟补货：{text}。超储未纳入。",
        proposal=proposal,
        risks=["模拟数量来自覆盖天数与安全库存，不是供应商承诺。", "真实仓储系统未连接。"],
        missing=["在途到货准确性和采购单价需人工复核。"],
    )
    return row, replayed


def create_budget(session: Session, actor: Actor, request_id: str, payload: ProposalScope):
    require_role(actor, "analyst", "approver")
    scope = _scope(session, actor.tenant_id, payload.start_date, payload.end_date, payload.store_id)
    report, findings = _report(session, actor.tenant_id, scope)
    if not findings.anomaly:
        raise DomainError("no_anomaly", "当前周期没有销售涨、利润降的异常，不生成预算提案", 409)
    plan = plan_action(report, findings)
    if plan.action.type != "adjust_ad_budget":
        raise DomainError(
            "budget_not_indicated",
            f"最大负向贡献是{findings.primary_driver.label if findings.primary_driver else '未知'}，不是广告花费。请走经营调查，而不是预算收缩。",
            409,
        )
    action = plan.action.model_copy(update={"margin_ok": True, "margin_floor_pct": float(MARGIN_FLOOR), "line_count": 1})
    days = plan.days
    proposal = {
        "kind": "budget",
        "margin_check": {"ok": True, "text": "本提案不改售价。参考日预算来自账本广告花费均值，不是广告后台已配置预算。"},
        "diffs": [
            {"item": f"{action.sku} 参考日预算", "before": str(action.baseline_daily_budget), "after": str(action.proposed_daily_budget)},
            {"item": "下调比例", "before": "0%", "after": f"{action.adjustment_pct}%"},
        ],
        "evidence_notes": [
            "花费来自 daily_operations.ad_spend。活动级 ROAS 只有在导入且不与每日账单重复的广告 CSV 后才可计算。",
        ],
        "expected_impact": _impact(
            "假设未来日均广告花费等于本周期均值、预算用尽且收入与其他成本不变。这是情景测算，不是已实现利润。",
            days, plan.potential // 2, plan.potential,
        ),
        "campaigns_note": "未把原型中的虚构 ROAS 当作本账本结果。",
    }
    row, replayed = _save(
        session, actor, request_id, kind="budget", report=report, findings=findings, action=action,
        title=f"{action.sku} · 广告预算优化模拟",
        recommendation=f"针对 {action.sku} 将参考日广告预算下调 {action.adjustment_pct}%，只生成模拟预览。",
        proposal=proposal,
        risks=["降低参考预算可能减少流量。", "会计贡献不是因果实验。"],
        missing=["广告活动级 ROAS 与边际获客成本。"],
    )
    return row, replayed


def _price_basis(session, tenant_id: str, scope: MetricsInput, store_id: str, sku: str) -> dict:
    store = session.scalar(select(Store).where(Store.id == store_id, Store.tenant_id == tenant_id))
    if store is None:
        raise DomainError("store_not_found", "店铺不存在", 404)
    product = session.scalar(select(Product).where(Product.tenant_id == tenant_id, Product.sku == sku))
    if product is None:
        raise DomainError("sku_not_found", "SKU 不存在", 404)
    orders = session.scalars(select(Order).where(
        Order.tenant_id == tenant_id, Order.store_id == store_id, Order.product_id == product.id,
        Order.date.between(scope.period.start_date, scope.period.end_date),
    )).all()
    units = sum(row.quantity for row in orders)
    sales = sum(row.sales for row in orders)
    if units <= 0 or sales <= 0:
        raise DomainError("insufficient_price_basis", "该店铺 SKU 在周期内没有可计算售价的订单", 409)
    return {
        "product": product,
        "units": units,
        "sales": sales,
        "cogs": sum(row.cogs for row in orders),
        "discounts": sum(row.discounts for row in orders),
        "platform_fees": sum(row.platform_fees for row in orders),
        "current_asp": int((Decimal(sales) / Decimal(units)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
    }


def create_price_review(session: Session, actor: Actor, request_id: str, payload: PriceProposalInput):
    require_role(actor, "analyst", "approver")
    scope = _scope(session, actor.tenant_id, payload.start_date, payload.end_date, payload.store_id)
    report, findings = _report(session, actor.tenant_id, scope)
    basis = _price_basis(session, actor.tenant_id, scope, payload.store_id, payload.sku)
    units = Decimal(basis["units"])
    unit_cogs = Decimal(basis["cogs"]) / units
    promo = Decimal(basis["discounts"]) / units
    platform_fee = Decimal(payload.proposed_price_cents) * Decimal(basis["platform_fees"]) / Decimal(basis["sales"])
    margin = deal_margin_pct(payload.proposed_price_cents, unit_cogs, platform_fee, promo)
    shown = float(margin.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    ok = margin >= MARGIN_FLOOR
    current_platform = Decimal(basis["current_asp"]) * Decimal(basis["platform_fees"]) / Decimal(basis["sales"])
    current_margin = deal_margin_pct(basis["current_asp"], unit_cogs, current_platform, promo)
    action = SimulationAction(
        type="simulate_price_review", product_id=basis["product"].id, sku=payload.sku,
        source_period=report.period.model_dump(mode="json"),
        proposed_price_cents=payload.proposed_price_cents,
        margin_ok=ok, margin_pct=shown, margin_floor_pct=float(MARGIN_FLOOR),
    )
    check = {
        "ok": ok,
        "margin_pct": shown,
        "floor_pct": float(MARGIN_FLOOR),
        "text": (
            f"拟调后成交毛利率 {shown:.2f}%，{'不低于' if ok else '低于'}红线 {MARGIN_FLOOR}%。"
            + ("可以进入人工审批，通过后也只写模拟预览，不改售价。" if ok else "提案不可批准，也不可模拟执行。")
        ),
    }
    proposal = {
        "kind": "price_review",
        "margin_check": check,
        "basis": {
            "units": basis["units"], "sales": basis["sales"], "cogs": basis["cogs"],
            "discounts": basis["discounts"], "platform_fees": basis["platform_fees"],
            "current_asp": basis["current_asp"],
            "current_margin_pct": float(current_margin.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "formula": "成交毛利 = 拟售价 − 单位采购成本 − 按历史费率估算的平台费 − 单位促销分摊",
        },
        "diffs": [
            {"item": "售价（美分）", "before": str(basis["current_asp"]), "after": str(payload.proposed_price_cents)},
            {"item": "成交毛利率", "before": f"{current_margin.quantize(Decimal('0.01'))}%", "after": f"{shown:.2f}%"},
            {"item": "红线", "before": f"{MARGIN_FLOOR}%", "after": "通过" if ok else "未通过"},
        ],
        "evidence_notes": [
            "单位成本、平台费率和促销分摊来自该店该 SKU 在统计周期内的订单合计，不是原型里的虚构沙发价格。",
        ],
        "expected_impact": _impact("调价复核不修改任何订单或标价。", (report.period.end_date - report.period.start_date).days + 1),
    }
    title = f"{payload.sku} · 调价毛利校验{'未通过' if not ok else ''}"
    row, replayed = _save(
        session, actor, request_id, kind=f"price-{payload.sku}-{payload.proposed_price_cents}",
        report=report, findings=findings, action=action, title=title,
        recommendation=check["text"], proposal=proposal,
        risks=["历史费率不能代表未来活动。", "即使校验通过也不会改价。"],
        missing=["竞品价格与流量弹性不在账本中。"],
    )
    return row, replayed
