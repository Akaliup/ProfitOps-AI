"""Read models for the nine-view workspace. Numbers come from the tenant ledger."""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import record
from .errors import DomainError
from .inventory import InventoryPolicy, inventory_report
from .metrics import analyze_drivers, build_report, fetch_metrics, resolve_scope
from .models import AuditLog, DailyOperation, ImportBatch, SourceRecord, Store
from .proposals import MARGIN_FLOOR, deal_margin_pct
from .schemas import AnalysisRequest, MetricsInput
from .security import Actor, require_role
from .views import iso_utc


ROAS_SCALE = Decimal("2.5")


def _scope(session, tenant_id, start, end, store_id) -> MetricsInput:
    try:
        request = AnalysisRequest(start_date=start, end_date=end, store_id=store_id)
    except ValueError as exc:
        raise DomainError("invalid_period", str(exc), 422) from exc
    return resolve_scope(session, request, tenant_id)


def _campaigns(session, tenant_id, period: MetricsInput["period"].__class__ | object, store_id: str | None):
    query = select(SourceRecord).where(
        SourceRecord.tenant_id == tenant_id,
        SourceRecord.kind == "ads",
        SourceRecord.date.between(period.start_date, period.end_date),
    )
    if store_id:
        query = query.where(SourceRecord.store_id == store_id)
    grouped: dict[tuple, dict] = {}
    for record in session.scalars(query):
        cid = record.payload.get("campaign_id") or "default"
        key = (record.store_id, cid)
        row = grouped.setdefault(key, {
            "store_id": record.store_id, "campaign_id": cid,
            "campaign_name": record.payload.get("campaign_name") or cid,
            "ad_spend": 0, "attributed_sales": 0, "attributed_orders": 0,
            "evidence_id": f"{record.store_id}:campaign:{cid}",
        })
        row["ad_spend"] += int(record.payload.get("ad_spend") or 0)
        row["attributed_sales"] += int(record.payload.get("attributed_sales") or 0)
        row["attributed_orders"] += int(record.payload.get("attributed_orders") or 0)
    items = []
    for row in grouped.values():
        spend = row["ad_spend"]
        roas = (Decimal(row["attributed_sales"]) / Decimal(spend)) if spend else None
        row["roas"] = float(roas.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if roas is not None else None
        row["below_threshold"] = bool(roas is not None and roas < ROAS_SCALE)
        items.append(row)
    items.sort(key=lambda item: (-item["ad_spend"], item["campaign_id"]))
    return items


def _inventory_counts(session, tenant_id, as_of: date, store_id: str | None) -> dict:
    try:
        report = inventory_report(session, tenant_id, as_of, store_id, InventoryPolicy())
    except DomainError as exc:
        return {"as_of": str(as_of), "missing": True, "low": 0, "watch": 0, "healthy": 0, "over": 0, "names": [], "note": exc.message}
    low, watch, healthy, over, names = [], [], [], [], []
    for item in report["items"]:
        risk = item.get("risk")
        if risk in {"CRITICAL", "HIGH"}:
            low.append(item)
            names.append(item.get("sku"))
        elif risk == "MEDIUM":
            watch.append(item)
        elif risk == "LOW":
            coverage = item.get("coverage_days")
            safety_days = item.get("safety_stock_days") or 0
            if coverage is not None and Decimal(str(coverage)) > Decimal(safety_days) * 4 and safety_days:
                over.append(item)
            else:
                healthy.append(item)
    return {
        "as_of": report["as_of"], "missing": bool(report["missing_inventory"]),
        "low": len(low), "watch": len(watch), "healthy": len(healthy), "over": len(over),
        "names": names[:8],
        "note": report["coverage_note"],
    }


def build_overview(session: Session, tenant_id: str, start, end, store_id: str | None) -> dict:
    scope = _scope(session, tenant_id, start, end, store_id)
    report = build_report(fetch_metrics(session, scope, tenant_id))
    findings = analyze_drivers(report)
    stores = session.scalars(select(Store).where(Store.tenant_id == tenant_id).order_by(Store.name)).all()
    if store_id:
        stores = [store for store in stores if store.id == store_id]
    contributions = []
    sales_sum = profit_sum = 0
    for store in stores:
        child = build_report(fetch_metrics(session, MetricsInput(period=scope.period, store_id=store.id), tenant_id))
        sales_sum += child.current.sales
        profit_sum += child.current.net_profit
        contributions.append({
            "id": store.id, "name": store.name, "marketplace": store.marketplace,
            "current": child.current.model_dump(mode="json"),
            "previous": child.previous.model_dump(mode="json"),
            "profit_delta": child.changes["net_profit"].absolute,
        })
    inventory = _inventory_counts(session, tenant_id, scope.period.end_date, store_id)
    campaigns = _campaigns(session, tenant_id, scope.period, store_id)
    weak = [row for row in campaigns if row["below_threshold"]]
    spend = sum(row["ad_spend"] for row in campaigns)
    attributed = sum(row["attributed_sales"] for row in campaigns)
    anomalies = []
    sales_change = report.changes["sales"].pct
    profit_abs = report.changes["net_profit"].absolute
    if report.anomaly:
        driver = findings.primary_driver.label if findings.primary_driver else "费用"
        anomalies.append({
            "code": "sales_up_profit_down",
            "title": "销售上涨，利润下降",
            "text": f"销售额变化 {sales_change if sales_change is not None else '无可比基数'}%，净利润变化 {profit_abs} 美分。最大负向贡献来自{driver}。",
            "target": "investigate",
        })
    if inventory["low"]:
        anomalies.append({
            "code": "low_stock",
            "title": f"{inventory['low']} 个 SKU 低于补货门槛",
            "text": "、".join(inventory["names"]) or "见库存建议",
            "target": "inventory",
        })
    if weak:
        anomalies.append({
            "code": "weak_roas",
            "title": f"{len(weak)} 条广告计划低于 ROAS {ROAS_SCALE}",
            "text": "；".join(f"{row['campaign_name']} {row['roas']}" for row in weak[:4]),
            "target": "ads",
        })
    elif report.anomaly and findings.primary_driver and findings.primary_driver.metric == "ad_spend":
        anomalies.append({
            "code": "ad_spend_pressure",
            "title": "广告账单上升",
            "text": "当前周期的广告费来自每日经营记录。活动级 ROAS 需要独立的广告 CSV，且不能与已有每日账单叠加入账。",
            "target": "ads",
        })
    return {
        "summary": report.model_dump(mode="json"),
        "stores": contributions,
        "stores_reconciled": sales_sum == report.current.sales and profit_sum == report.current.net_profit,
        "inventory": inventory,
        "campaigns": {
            "count": len(campaigns), "weak": len(weak), "spend": spend,
            "attributed_sales": attributed,
            "roas": float((Decimal(attributed) / Decimal(spend)).quantize(Decimal("0.01"))) if spend else None,
            "threshold": float(ROAS_SCALE),
            "note": "归因销售额不计入利润桥。没有活动行时这里为 0，不借用原型样例。",
        },
        "anomalies": anomalies,
        "currency": "USD",
        "source": "orders + daily_operations + source_records",
        "note": "数字由当前租户账本计算，不是交互原型里的虚构金额，也不是生产结果。",
    }


def list_campaigns(session, tenant_id, start, end, store_id):
    scope = _scope(session, tenant_id, start, end, store_id)
    current = _campaigns(session, tenant_id, scope.period, store_id)
    from datetime import timedelta
    days = (scope.period.end_date - scope.period.start_date).days + 1
    prev_period_end = scope.period.start_date - timedelta(days=1)
    prev_period_start = scope.period.start_date - timedelta(days=days)
    class _P:
        start_date = prev_period_start
        end_date = prev_period_end
    previous = _campaigns(session, tenant_id, _P, store_id)
    prev_map = {(row["store_id"], row["campaign_id"]): row for row in previous}
    for row in current:
        prior = prev_map.get((row["store_id"], row["campaign_id"]))
        row["previous_roas"] = prior["roas"] if prior else None
        row["previous_spend"] = prior["ad_spend"] if prior else 0
    spend = sum(row["ad_spend"] for row in current)
    attributed = sum(row["attributed_sales"] for row in current)
    return {
        "period": scope.period.model_dump(mode="json"),
        "items": current,
        "spend": spend,
        "attributed_sales": attributed,
        "roas": float((Decimal(attributed) / Decimal(spend)).quantize(Decimal("0.01"))) if spend else None,
        "weak": sum(1 for row in current if row["below_threshold"]),
        "threshold": float(ROAS_SCALE),
        "currency": "USD",
        "note": "仅统计已导入且未与每日广告账单冲突的活动行。种子账本的广告费在每日经营记录里，不会在这里被重写成计划 ROAS。",
    }


def access_view(actor: Actor, *, llm_configured: bool, llm_provider: str, llm_model: str) -> dict:
    columns = ["读取账本", "发起分析", "导入预览", "提交导入", "审批", "模拟执行", "知识写入", "运维汇总"]
    return {
        "actor": {"id": actor.id, "role": actor.role, "tenant_id": actor.tenant_id},
        "enforced_by": "server",
        "columns": columns,
        "rows": [
            {"role": "analyst", "label": "经营分析师", "implemented": True, "current": actor.role == "analyst",
             "cells": [True, True, True, False, False, False, False, False]},
            {"role": "approver", "label": "审批负责人", "implemented": True, "current": actor.role == "approver",
             "cells": [True, True, True, True, True, True, True, True]},
            {"role": "ad_operator", "label": "广告专员", "implemented": False, "current": False,
             "cells": [False] * 8, "note": "规划中。后端没有该角色，按钮不会授予权限。"},
            {"role": "warehouse", "label": "仓配", "implemented": False, "current": False,
             "cells": [False] * 8, "note": "规划中。后端没有该角色。"},
            {"role": "visitor", "label": "只读访客", "implemented": False, "current": False,
             "cells": [False] * 8, "note": "规划中。未登录请求返回 401。"},
        ],
        "integrations": [
            {"name": "订单 / 库存 CSV", "state": "available", "detail": "预览与提交走现有导入接口，提交仅 approver。"},
            {"name": "广告 CSV", "state": "constrained", "detail": "若当天已有非导入产生的每日广告账单，服务端拒绝叠加入账，避免广告费双计。"},
            {"name": "LLM", "state": "configured" if llm_configured else "offline",
             "detail": f"{llm_provider}/{llm_model} 已配置" if llm_configured else "未配置密钥。分析标记 deterministic_fallback，规则结果不会被称作模型输出。"},
            {"name": "Langfuse / 外部追踪", "state": "not_integrated", "detail": "未接入，页面不显示已连接。"},
            {"name": "天猫 / 京东 / 抖音 / ERP", "state": "not_connected", "detail": "不会发起授权、拉单、改价、改预算或采购。"},
        ],
        "simulation": True,
        "note": "角色以服务端 token 为准，不读取 X-Role。矩阵里未实现的角色不能被前端开关打开。",
    }


def demo_csv(session: Session, tenant_id: str, kind: str) -> tuple[str, str]:
    if kind not in {"orders", "inventory"}:
        raise DomainError(
            "demo_ads_conflicts_with_ledger",
            "不为演示账本生成可叠加入账的广告 CSV。每日广告账单已经存在，再导入活动花费会重复计算。预算模拟请使用账本广告费。",
            409,
        )
    end = session.scalar(select(func.max(DailyOperation.date)).where(DailyOperation.tenant_id == tenant_id))
    stores = session.scalars(select(Store).where(Store.tenant_id == tenant_id).order_by(Store.id)).all()
    if end is None or not stores:
        raise DomainError("no_data", "当前租户没有可附加的演示账本", 409)
    from .models import Product
    products = session.scalars(select(Product).where(Product.tenant_id == tenant_id).order_by(Product.sku)).all()
    if not products:
        raise DomainError("no_data", "当前租户没有 SKU", 409)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if kind == "orders":
        writer.writerow(["store_id", "order_id", "order_date", "sku", "quantity", "gross_sales", "refund_amount", "platform_fee", "shipping_fee", "other_fee", "cogs", "currency"])
        product = products[0]
        for store in stores:
            writer.writerow([store.id, f"WS-{store.id}-{product.sku}-{end.isoformat()}", end.isoformat(), product.sku, 1, "49.00", "0.00", "7.35", "6.50", "0.90", "16.00", "USD"])
        filename = f"workspace-orders-{end.isoformat()}.csv"
    else:
        writer.writerow(["store_id", "snapshot_date", "sku", "on_hand", "reserved", "inbound_quantity", "inbound_eta", "lead_time_days", "safety_stock_days", "moq", "case_pack"])
        for index, product in enumerate(products):
            for store in stores:
                low = index == 0
                writer.writerow([
                    store.id, end.isoformat(), product.sku,
                    1 if low else 400, 0, 0, "",
                    14 if low else 7, 14 if low else 3, 6 if low else 1, 1,
                ])
        filename = f"workspace-inventory-{end.isoformat()}.csv"
    return buffer.getvalue(), filename


def run_offline_eval(session: Session, actor: Actor, request_id: str) -> dict:
    """Recompute a few ledger invariants and persist the outcome. No model calls."""
    require_role(actor, "analyst", "approver")
    scope = _scope(session, actor.tenant_id, None, None, None)
    report = build_report(fetch_metrics(session, scope, actor.tenant_id))
    overview = build_overview(session, actor.tenant_id, scope.period.start_date, scope.period.end_date, None)
    margin = deal_margin_pct(259900, Decimal(198000), Decimal(15594), Decimal(6200))
    margin_shown = float(margin.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    cases = [
        {
            "id": "bridge",
            "scene": "利润桥加总",
            "ok": bool(report.bridge_verified),
            "detail": "贡献项合计与净利润变化一致" if report.bridge_verified else "贡献核对失败",
        },
        {
            "id": "store_sum",
            "scene": "分店加总等于总览",
            "ok": bool(overview["stores_reconciled"]),
            "detail": f"销售额 {overview['summary']['current']['sales']} 美分，分店已对账" if overview["stores_reconciled"] else "分店合计与总览不一致",
        },
        {
            "id": "margin_formula",
            "scene": "毛利公式可复算",
            "ok": abs(margin_shown - 15.43) < 0.02,
            "detail": f"固定输入 259900/198000/15594/6200 得到 {margin_shown:.2f}%，用于校验公式，不是账本里的商品。",
        },
        {
            "id": "no_model",
            "scene": "离线评测不调用模型",
            "ok": True,
            "detail": "本次 llm_calls=0。未配置或未授权的模型不会被记成成功。",
        },
    ]
    passed = sum(1 for case in cases if case["ok"])
    payload = {
        "engine": "deterministic-checks-v1",
        "llm_calls": 0,
        "external_calls": 0,
        "passed": passed,
        "failed": len(cases) - passed,
        "total": len(cases),
        "cases": cases,
        "period": scope.period.model_dump(mode="json"),
        "note": "这是当场重算的检查结果，不是手写通过率，也不是 LLM-as-judge。",
    }
    record(
        session, actor, "eval.completed", "eval_run", "offline",
        request_id, "success" if payload["failed"] == 0 else "failure", payload,
    )
    batches = session.scalar(select(func.count()).select_from(ImportBatch).where(ImportBatch.tenant_id == actor.tenant_id)) or 0
    payload["import_batches"] = batches
    return payload


def list_evals(session: Session, tenant_id: str, limit: int = 20) -> dict:
    rows = session.scalars(select(AuditLog).where(
        AuditLog.tenant_id == tenant_id, AuditLog.action == "eval.completed",
    ).order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return {
        "items": [
            {
                "id": row.id, "created_at": iso_utc(row.created_at),
                "outcome": row.outcome, "actor": row.actor, "details": row.details,
            } for row in rows
        ],
        "note": "没有记录时表示尚未运行。页面不得显示手写分数。",
    }
