"""Atomic multi-source CSV ingestion. Never substitutes missing cost with zero."""
import csv
import hashlib
import io
import json
import re
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .audit import record
from .errors import DomainError
from .models import DailyOperation, ImportBatch, Order, Product, SourceRecord, Store, new_id
from .imports import MAX_BYTES, MAX_ROWS, ORDER_COLUMNS, ORDER_MONEY_COLUMNS, ParsedBatch, _money, summary_of
from .schemas import ImportIssue

COLUMNS = {
    "orders": "store_id,order_id,order_date,sku,quantity,gross_sales,refund_amount,platform_fee,shipping_fee,other_fee,cogs,currency",
    "ads": "store_id,date,sku,campaign_id,campaign_name,ad_spend,attributed_sales,attributed_orders,clicks,impressions,complete",
    "inventory": "store_id,snapshot_date,sku,on_hand,reserved,inbound_quantity,inbound_eta,lead_time_days,safety_stock_days,moq,case_pack",
}
ALIASES = {"order_date": "date", "gross_sales": "sales", "refund_amount": "refunds", "platform_fee": "platform_fees", "shipping_fee": "fulfillment", "other_fee": "other_costs"}


def mapped_csv(text, mapping):
    """Mapping is canonical field -> input header, applied server-side."""
    if not mapping:
        return text
    try:
        mapping = json.loads(mapping)
        if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
            raise ValueError()
        rows = list(csv.reader(io.StringIO(text.lstrip('\ufeff')), strict=True))
        reverse = {v: k for k, v in mapping.items() if v}
        if len(reverse) != len([v for v in mapping.values() if v]):
            raise ValueError()
        rows[0] = [reverse.get(h, h) for h in rows[0]]
        output = io.StringIO()
        csv.writer(output).writerows(rows)
        return output.getvalue()
    except (ValueError, IndexError, csv.Error):
        raise DomainError("invalid_mapping", "字段映射或 CSV 格式无效", 422) from None


def ingest(session, kind, text, filename, actor, request_id=None):
    if kind not in (*COLUMNS, "orders"):
        raise DomainError("import_kind_invalid", "不支持的数据类型", 404)
    if len(text.encode()) > MAX_BYTES:
        raise DomainError("import_file_too_large", "文件不能超过 5 MB", 413)
    batch = ParsedBatch(kind, filename, hashlib.sha256(text.encode()).hexdigest())
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')), strict=True)
    header = reader.fieldnames or []
    if len(set(header)) != len(header):
        raise DomainError("import_header_duplicate", "重复列名", 422)
    required = {"store_id", "sku"} | ({"order_id", "quantity", "cogs"} if kind == "orders" else {"date", "campaign_id", "ad_spend"} if kind == "ads" else {"snapshot_date", "on_hand", "lead_time_days", "safety_stock_days", "moq", "case_pack"})
    normalized = {ALIASES.get(h, h) for h in header} if kind == "orders" else set(header)
    if kind == "orders":
        required |= {"date", "sales"}
    if len(normalized) != len(header):
        raise DomainError('import_header_duplicate', '字段别名映射后存在重复列', 422)
    if not required <= normalized:
        raise DomainError("import_header_invalid", "缺少字段：" + ', '.join(sorted(required - normalized)), 422)
    stores = {s.id: s for s in session.scalars(select(Store).where(Store.tenant_id == actor.tenant_id).with_for_update())}
    products = {p.sku: p for p in session.scalars(select(Product).where(Product.tenant_id == actor.tenant_id))}
    seen = set()
    preview_rows = []
    try:
        for number, raw in enumerate(reader, 2):
            batch.total_rows += 1
            if batch.total_rows > MAX_ROWS:
                raise DomainError("import_too_many_rows", "最多 5000 行", 413)
            try:
                if None in raw or any(v is None for v in raw.values()):
                    raise ValueError("列数不匹配")
                row = {ALIASES.get(k, k) if kind == "orders" else k: v.strip() for k, v in raw.items()}
                if len(preview_rows) < 5:
                    preview_rows.append(row)
                if row['store_id'] not in stores or row['sku'] not in products:
                    raise ValueError("店铺或 SKU 不存在于当前租户")
                if row.get('currency', 'USD') != 'USD' or stores[row['store_id']].currency != 'USD':
                    raise ValueError("仅支持 USD")
                day = date.fromisoformat(row['snapshot_date' if kind == 'inventory' else 'date'])
                from .models import utcnow
                if not date(2000, 1, 1) <= day <= utcnow().date():
                    raise ValueError("业务日期超出范围")
                payload = {}
                def integer(name, default=0, minimum=0):
                    value = row.get(name) or str(default)
                    if not re.fullmatch(r'\d{1,7}', value) or not minimum <= int(value) <= 1000000:
                        raise ValueError(f"{name} 必须是 {minimum} 到 1000000 的整数")
                    return int(value)
                def money(name, required=False):
                    result = _money(row.get(name) or ('' if required else '0'), number, name, batch)
                    if result is None:
                        raise ValueError(f"{name} 金额无效")
                    if result < 0:
                        raise ValueError(f"{name} 不能为负数")
                    return result
                if kind == 'orders':
                    payload = {k: money(k, k in {'sales', 'cogs'}) for k in ORDER_MONEY_COLUMNS}
                    payload.update(quantity=integer('quantity', minimum=1), order_id=row['order_id'])
                    if not payload['order_id'] or len(payload['order_id']) > 80:
                        raise ValueError("订单号为空或超过 80 字符")
                    if payload['refunds'] + payload['discounts'] > payload['sales']:
                        raise ValueError("退款与折扣不能超过销售额")
                    natural = [actor.tenant_id, kind, row['store_id'], row['sku'], row['order_id']]
                elif kind == 'ads':
                    if not row['campaign_id'] or len(row['campaign_id']) > 100:
                        raise ValueError("campaign_id 必填且最长 100 字符")
                    payload = {k: integer(k) for k in ('attributed_orders', 'clicks', 'impressions')}
                    payload.update(ad_spend=money('ad_spend', True), attributed_sales=money('attributed_sales'), campaign_id=row['campaign_id'], campaign_name=row.get('campaign_name', '')[:200])
                    complete = row.get('complete', '').lower()
                    if complete not in ('', 'true', 'false'):
                        raise ValueError('complete 必须为 true 或 false')
                    payload['complete'] = complete == 'true'
                    natural = [actor.tenant_id, kind, row['store_id'], row['sku'], str(day), row['campaign_id']]
                else:
                    payload = {k: integer(k, 1 if k in ('moq', 'case_pack') else 0, 1 if k == 'case_pack' else 0) for k in ('on_hand', 'reserved', 'inbound_quantity', 'lead_time_days', 'safety_stock_days', 'moq', 'case_pack')}
                    eta = row.get('inbound_eta')
                    payload['inbound_eta'] = str(date.fromisoformat(eta)) if eta else None
                    if payload['reserved'] > payload['on_hand']:
                        raise ValueError("预留库存不能超过现有库存")
                    natural = [actor.tenant_id, kind, row['store_id'], row['sku'], str(day)]
                key = hashlib.sha256(json.dumps(natural).encode()).hexdigest()
                if key in seen:
                    raise ValueError("文件内重复自然键")
                seen.add(key)
                old = session.scalar(select(SourceRecord).where(SourceRecord.tenant_id == actor.tenant_id, SourceRecord.kind == kind, SourceRecord.natural_key == key))
                if old:
                    if old.payload != payload:
                        raise ValueError("相同自然键的数据冲突 不覆盖历史")
                    batch.skipped_rows += 1
                    continue
                product_id = products[row['sku']].id
                if kind == 'orders':
                    legacy = session.scalar(select(Order).where(Order.tenant_id == actor.tenant_id, Order.id == row['order_id'], Order.store_id == row['store_id'], Order.product_id == product_id))
                    if legacy:
                        if legacy.date != day or any(getattr(legacy, k) != v for k, v in payload.items() if k != 'order_id'):
                            raise ValueError('与旧订单账本冲突')
                        batch.skipped_rows += 1
                        continue
                if kind == 'ads':
                    operation = session.scalar(select(DailyOperation).where(DailyOperation.tenant_id == actor.tenant_id, DailyOperation.store_id == row['store_id'], DailyOperation.product_id == product_id, DailyOperation.date == day))
                    if operation and not operation.id.startswith('feed-'):
                        raise ValueError("已有每日经营广告账本 不能与活动明细重复入账")
                batch.accepted.append(dict(key=key, payload=payload, store_id=row['store_id'], product_id=product_id, date=day, row=number))
            except (ValueError, KeyError) as exc:
                batch.rejected_rows += 1
                batch.issues.append(ImportIssue(row=number, field=None, code='invalid_row', message=str(exc)))
    except csv.Error:
        raise DomainError('invalid_csv', 'CSV 引号格式无效', 422) from None
    if not batch.total_rows:
        raise DomainError('import_empty', '没有数据行', 422)
    from .metrics import calculate_totals
    from .schemas import Totals
    totals = Totals()
    for item in batch.accepted:
        for name in ORDER_MONEY_COLUMNS + ('ad_spend',):
            setattr(totals, name, getattr(totals, name) + item['payload'].get(name, 0))
        batch.totals.inventory += item['payload'].get('on_hand', 0)
        batch.totals.inbound_inventory += item['payload'].get('inbound_quantity', 0)
    totals = calculate_totals(totals)
    batch.totals.rows = len(batch.accepted)
    for name in ORDER_MONEY_COLUMNS + ('ad_spend', 'net_sales', 'net_profit'):
        setattr(batch.totals, name, getattr(totals, name))
    if request_id is None:
        result = summary_of(batch)
        result.preview_rows = preview_rows
        return result
    status = 'blocked' if batch.rejected_rows else 'committed' if batch.accepted else 'noop'
    saved = ImportBatch(id=new_id(), tenant_id=actor.tenant_id, kind=kind, filename=filename, content_sha256=batch.sha256, status=status, total_rows=batch.total_rows, accepted_rows=0 if batch.rejected_rows else len(batch.accepted), skipped_rows=batch.skipped_rows, rejected_rows=batch.rejected_rows, summary=batch.totals.model_dump(), issues=[x.model_dump() for x in batch.issues], created_by=actor.id, request_id=request_id)
    session.add(saved)
    session.flush()
    if not batch.rejected_rows:
        try:
            for item in batch.accepted:
                payload = item['payload']
                session.add(SourceRecord(id=new_id(), tenant_id=actor.tenant_id, kind=kind, natural_key=item['key'], batch_id=saved.id, store_id=item['store_id'], product_id=item['product_id'], date=item['date'], payload=payload, row_number=item['row']))
                if kind == 'orders':
                    session.add(Order(id='feed-' + item['key'], tenant_id=actor.tenant_id, store_id=item['store_id'], product_id=item['product_id'], date=item['date'], **{k: v for k, v in payload.items() if k != 'order_id'}))
                elif kind == 'ads':
                    operation = session.scalar(select(DailyOperation).where(DailyOperation.tenant_id == actor.tenant_id, DailyOperation.store_id == item['store_id'], DailyOperation.product_id == item['product_id'], DailyOperation.date == item['date']))
                    if operation is None:
                        operation = DailyOperation(id='feed-' + new_id(), tenant_id=actor.tenant_id, store_id=item['store_id'], product_id=item['product_id'], date=item['date'], ad_spend=0, inventory=0, inbound_inventory=0, complete=False)
                        session.add(operation)
                    operation.ad_spend += payload['ad_spend']
                    prior_sources = session.scalars(select(SourceRecord).where(SourceRecord.tenant_id == actor.tenant_id, SourceRecord.kind == 'ads', SourceRecord.store_id == item['store_id'], SourceRecord.product_id == item['product_id'], SourceRecord.date == item['date'])).all()
                    operation.complete = payload['complete'] and all(r.payload.get('complete', False) for r in prior_sources)
                session.flush()
        except IntegrityError as exc:
            raise DomainError('import_conflict', '并发导入冲突 请重新预览', 409) from exc
    if not batch.rejected_rows:
        # Read back immutable normalized rows; the transaction rolls back on mismatch.
        actual = session.execute(select(SourceRecord.natural_key, SourceRecord.payload).where(SourceRecord.tenant_id == actor.tenant_id, SourceRecord.batch_id == saved.id)).all()
        expected = {x['key']: x['payload'] for x in batch.accepted}
        if dict(actual) != expected:
            raise DomainError('import_reconciliation_failed', '来源行回读不一致 已回滚', 500)
    record(session, actor, 'import.rejected' if batch.rejected_rows else 'import.committed', 'import_batch', saved.id, request_id, details={'kind': kind, 'status': status})
    result = summary_of(batch, row_id=saved.id, status=status, created_at=saved.created_at, created_by=actor.id)
    result.preview_rows = preview_rows
    if batch.rejected_rows:
        result.accepted_rows = 0
    return result
