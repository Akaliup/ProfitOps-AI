from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select

from .errors import DomainError
from .models import DailyOperation, Order, Product, Store
from .schemas import (
    AnalysisRequest, Change, DataQuality, Driver, DriverReport, MetricsInput,
    MetricsReport, Period, RawMetrics, Totals,
)

MONEY_FIELDS = ("sales", "discounts", "refunds", "cogs", "ad_spend", "fulfillment", "platform_fees", "other_costs")
LABELS = {
    "sales": "销售额", "discounts": "折扣", "refunds": "退款", "cogs": "采购成本",
    "ad_spend": "广告支出", "fulfillment": "物流履约", "platform_fees": "平台手续费", "other_costs": "其他可变成本",
}


def rounded_ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return float((Decimal(numerator) / Decimal(denominator) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate_totals(totals: Totals) -> Totals:
    net_sales = totals.sales - totals.discounts - totals.refunds
    profit = net_sales - sum(getattr(totals, field) for field in ("cogs", "ad_spend", "fulfillment", "platform_fees", "other_costs"))
    return totals.model_copy(update={"net_sales": net_sales, "net_profit": profit, "margin_pct": rounded_ratio(profit, net_sales)})


def resolve_scope(session, request: AnalysisRequest, tenant_id: str = "demo") -> MetricsInput:
    if request.store_id:
        store = session.scalar(select(Store).where(Store.id == request.store_id, Store.tenant_id == tenant_id))
        if store is None:
            raise DomainError("store_not_found", "店铺不存在", 404)
    if request.start_date is not None:
        period = Period(start_date=request.start_date, end_date=request.end_date)
    else:
        last_day = session.scalar(
            select(func.max(DailyOperation.date)).where(DailyOperation.tenant_id == tenant_id)
        )
        if last_day is None:
            raise DomainError("no_data", "尚无经营数据，请初始化演示数据", 409)
        period = Period(start_date=last_day - timedelta(days=29), end_date=last_day)
    return MetricsInput(period=period, store_id=request.store_id)


def fetch_metrics(session, scope: MetricsInput, tenant_id: str = "demo") -> RawMetrics:
    days = (scope.period.end_date - scope.period.start_date).days + 1
    previous = Period(start_date=scope.period.start_date - timedelta(days=days), end_date=scope.period.start_date - timedelta(days=1))
    store_query = select(Store).where(Store.tenant_id == tenant_id)
    if scope.store_id:
        store_query = store_query.where(Store.id == scope.store_id)
    stores = session.scalars(store_query).all()
    if scope.store_id and not stores:
        raise DomainError("store_not_found", "店铺不存在", 404)
    products = session.scalars(select(Product).where(Product.tenant_id == tenant_id).order_by(Product.sku)).all()
    store_ids = [store.id for store in stores]
    orders = session.scalars(select(Order).where(
        Order.tenant_id == tenant_id,
        Order.date.between(previous.start_date, scope.period.end_date), Order.store_id.in_(store_ids),
    )).all()
    operations = session.scalars(select(DailyOperation).where(
        DailyOperation.tenant_id == tenant_id,
        DailyOperation.date.between(previous.start_date, scope.period.end_date), DailyOperation.store_id.in_(store_ids),
    )).all()
    daily = {previous.start_date + timedelta(days=i): Totals() for i in range(days * 2)}
    sku = {product.id: {"current": Totals(), "previous": Totals(), "product": product, "inventory": 0, "inbound_inventory": 0} for product in products}
    current = Totals()
    prior = Totals()
    for order in orders:
        current_period = order.date >= scope.period.start_date
        targets = [daily[order.date], current if current_period else prior, sku[order.product_id]["current" if current_period else "previous"]]
        for target in targets:
            for field in MONEY_FIELDS:
                if field != "ad_spend":
                    setattr(target, field, getattr(target, field) + getattr(order, field))
            target.orders += 1
            target.units += order.quantity
    for operation in operations:
        current_period = operation.date >= scope.period.start_date
        for target in [daily[operation.date], current if current_period else prior, sku[operation.product_id]["current" if current_period else "previous"]]:
            target.ad_spend += operation.ad_spend
        if operation.date == scope.period.end_date:
            sku[operation.product_id]["inventory"] += operation.inventory
            sku[operation.product_id]["inbound_inventory"] += operation.inbound_inventory
    observed = {(row.store_id, row.product_id, row.date) for row in operations}
    expected = len(stores) * len(products) * days * 2
    missing_examples = []
    missing_count = 0
    for business_date in daily:
        for store in stores:
            for product in products:
                if (store.id, product.id, business_date) not in observed:
                    missing_count += 1
                    if len(missing_examples) < 10:
                        missing_examples.append(f"{business_date}/{store.id}/{product.sku}")
    incomplete = sum(not row.complete for row in operations)
    issues = []
    if not stores or not products:
        issues.append("缺少店铺或 SKU 主数据")
    if missing_count:
        issues.append(f"当前与上一周期缺少 {missing_count} 条每日经营记录；不将缺失值当成经营零值")
    if incomplete:
        issues.append(f"{incomplete} 条每日经营记录被标记为不完整")
    breakdown = []
    for item in sku.values():
        product = item["product"]
        now, before = calculate_totals(item["current"]), calculate_totals(item["previous"])
        breakdown.append({
            "product_id": product.id, "sku": product.sku, "name": product.name,
            "current": now.model_dump(), "previous": before.model_dump(),
            "profit_delta": now.net_profit - before.net_profit,
            "ad_spend_delta": now.ad_spend - before.ad_spend,
            "inventory": item["inventory"], "inbound_inventory": item["inbound_inventory"],
        })
    return RawMetrics(
        period=scope.period, previous_period=previous, store_id=scope.store_id,
        current=current, previous=prior,
        trend=[{"date": str(day), **calculate_totals(value).model_dump()} for day, value in daily.items()],
        sku_breakdown=breakdown,
        data_quality=DataQuality(
            complete=not issues, expected_records=expected, observed_records=len(observed),
            missing_records=missing_count, incomplete_records=incomplete, missing_examples=missing_examples, issues=issues,
        ),
    )


def build_report(raw: RawMetrics) -> MetricsReport:
    now, before = calculate_totals(raw.current), calculate_totals(raw.previous)
    changes = {}
    for field in (*MONEY_FIELDS, "net_sales", "net_profit", "orders", "units"):
        delta = getattr(now, field) - getattr(before, field)
        changes[field] = Change(absolute=delta, pct=rounded_ratio(delta, abs(getattr(before, field))))
    changes["margin_pct"] = Change(
        absolute=round(now.margin_pct - before.margin_pct, 2) if now.margin_pct is not None and before.margin_pct is not None else 0,
        pct=None,
    )
    drivers = []
    for field in MONEY_FIELDS:
        delta = getattr(now, field) - getattr(before, field)
        drivers.append(Driver(
            metric=field, label=LABELS[field], previous=getattr(before, field), current=getattr(now, field),
            contribution=delta if field == "sales" else -delta,
            source="daily_operations.ad_spend" if field == "ad_spend" else f"orders.{field}",
        ))
    return MetricsReport(
        **raw.model_dump(exclude={"current", "previous"}), current=now, previous=before,
        changes=changes, drivers=sorted(drivers, key=lambda d: d.contribution),
        anomaly=now.sales > before.sales and now.net_profit < before.net_profit,
        bridge_verified=sum(d.contribution for d in drivers) == now.net_profit - before.net_profit,
    )


def analyze_drivers(report: MetricsReport) -> DriverReport:
    negatives = [driver for driver in report.drivers if driver.contribution < 0]
    primary = negatives[0] if negatives else None
    if not report.data_quality.complete:
        conclusion = "数据覆盖不足，暂停经营建议。请补齐每日经营记录后重新分析。"
    elif report.anomaly and primary:
        conclusion = f"销售额上涨、净利润下降。{primary.label}增加是最大负向会计贡献项；这是利润桥归因，不代表已证明业务因果。"
    else:
        conclusion = "未检测到销售额上涨且利润下降的组合异常，暂不创建动作建议。"
    evidence = [
        {
            "id": f"ev-{driver.metric}", "metric": driver.metric, "label": driver.label,
            "current": driver.current, "previous": driver.previous, "contribution": driver.contribution,
            "period": report.period.model_dump(mode="json"),
            "previous_period": report.previous_period.model_dump(mode="json"),
            "store_id": report.store_id, "currency": "USD", "source": driver.source,
            "formula": "current - previous" if driver.metric == "sales" else "-(current - previous)",
        } for driver in report.drivers
    ]
    return DriverReport(
        anomaly=report.anomaly, conclusion=conclusion, primary_driver=primary,
        drivers=report.drivers, evidence=evidence,
        bridge_total=sum(driver.contribution for driver in report.drivers),
    )


def verify_evidence(report: MetricsReport, findings: DriverReport):
    if not report.data_quality.complete:
        raise DomainError("insufficient_data", "经营数据不完整，未生成建议", 409, report.data_quality.model_dump())
    expected = analyze_drivers(report)
    if not report.bridge_verified or findings.model_dump() != expected.model_dump():
        raise DomainError("evidence_mismatch", "证据与确定性计算不一致", 422)
    if calculate_totals(report.current) != report.current or calculate_totals(report.previous) != report.previous:
        raise DomainError("evidence_mismatch", "利润口径校验失败", 422)
