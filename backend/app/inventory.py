"""Deterministic inventory policy; Decimal throughout and no external effects."""
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING

from pydantic import Field
from sqlalchemy import select

from .models import Order, Product, SourceRecord, Store
from .schemas import StrictModel
from .errors import DomainError


class InventoryPolicy(StrictModel):
    lookback_days: int = Field(28, ge=1, le=365)
    min_observed_days: int = Field(7, ge=1, le=365)
    max_snapshot_age_days: int = Field(7, ge=0, le=365)
    review_days: int = Field(7, ge=0, le=365)
    high_buffer_days: int = Field(3, ge=0, le=365)
    medium_buffer_days: int = Field(7, ge=0, le=365)


def calculate_inventory(payload, units, days, observed_days, as_of, snapshot_date, policy):
    available = payload['on_hand'] - payload['reserved']
    rate = Decimal(units) / days
    coverage = Decimal(available) / rate if rate else None
    lead, safety = payload['lead_time_days'], payload['safety_stock_days']
    eta = date.fromisoformat(payload['inbound_eta']) if payload.get('inbound_eta') else None
    eta_days = (eta - as_of).days if eta else None
    timely = payload['inbound_quantity'] if eta_days is not None and 0 <= eta_days <= lead and (coverage is None or Decimal(eta_days) <= coverage) else 0
    insufficient = observed_days < policy.min_observed_days or (as_of - snapshot_date).days > policy.max_snapshot_age_days
    target = rate * (lead + safety + policy.review_days)
    needed = max(Decimal(0), target - available - timely)
    quantity = int(needed.to_integral_value(rounding=ROUND_CEILING))
    if quantity:
        quantity = max(quantity, payload['moq'])
        quantity = ((quantity + payload['case_pack'] - 1) // payload['case_pack']) * payload['case_pack']
    effective = (Decimal(available + timely) / rate) if rate else None
    risk = 'INSUFFICIENT_DATA' if insufficient else 'LOW'
    if not insufficient and effective is not None:
        risk = 'CRITICAL' if effective < lead else 'HIGH' if effective <= lead + policy.high_buffer_days else 'MEDIUM' if effective <= lead + safety + policy.medium_buffer_days else 'LOW'
    return {**payload, 'available': available, 'daily_sales': str(rate.quantize(Decimal('.0001'))), 'coverage_days': str(coverage.quantize(Decimal('.01'))) if coverage is not None else None, 'safety_stock': str(rate * safety), 'reorder_point': str(rate * (lead + safety)), 'timely_inbound': timely, 'risk': risk, 'reorder_quantity': None if insufficient else quantity, 'observed_days': observed_days, 'status': 'insufficient_data' if insufficient else 'no_demand' if not units else 'ok'}


def inventory_report(session, tenant_id, as_of, store_id=None, policy=None):
    policy = policy or InventoryPolicy()
    stores = session.scalars(select(Store).where(Store.tenant_id == tenant_id)).all()
    if store_id and store_id not in {s.id for s in stores}:
        raise DomainError('store_not_found', '店铺不存在', 404)
    products = {p.id: p.sku for p in session.scalars(select(Product).where(Product.tenant_id == tenant_id))}
    query = select(SourceRecord).where(SourceRecord.tenant_id == tenant_id, SourceRecord.kind == 'inventory', SourceRecord.date <= as_of)
    if store_id:
        query = query.where(SourceRecord.store_id == store_id)
    snapshots = {}
    for row in session.scalars(query.order_by(SourceRecord.date.desc())):
        snapshots.setdefault((row.store_id, row.product_id), row)
    start = as_of - timedelta(days=policy.lookback_days - 1)
    orders = session.scalars(select(Order).where(Order.tenant_id == tenant_id, Order.date.between(start, as_of))).all()
    items = []
    for (store, product), snapshot in snapshots.items():
        matching = [o for o in orders if o.store_id == store and o.product_id == product]
        # Without a feed coverage manifest, only dates with orders are confirmed.
        observed = len({o.date for o in matching})
        result = calculate_inventory(snapshot.payload, sum(o.quantity for o in matching), policy.lookback_days, observed, as_of, snapshot.date, policy)
        items.append(dict(result, store_id=store, sku=products[product], product_id=product, snapshot_date=str(snapshot.date), evidence_id='inventory-' + snapshot.id, source_id=snapshot.id, import_batch_id=snapshot.batch_id, order_ids=[o.id for o in matching]))
    return {'as_of': str(as_of), 'period': {'start_date': str(start), 'end_date': str(as_of)}, 'policy': policy.model_dump(), 'items': items, 'missing_inventory': not bool(items), 'coverage_note': '没有订单覆盖声明时仅将有订单的日期视为已观察日期 零销量与缺失数据不混同'}
