"""CSV import with row validation and a read-back reconciliation gate.

The importer is deliberately strict: one invalid row blocks the whole batch.
Partial imports make a profit ledger harder to explain than a rejected file, so
the unit of success is the file, not the row. Re-uploading an already imported
file is a no-op because natural keys are compared field by field.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import IntegrityError

from .audit import record
from .errors import DomainError
from .models import DailyOperation, ImportBatch, Order, Product, Store, new_id
from .schemas import ImportCheck, ImportIssue, ImportSummary, ImportTotals
from .security import Actor

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
MAX_ISSUES = 100
MAX_MONEY_CENTS = 100_000_000_000
MAX_QUANTITY = 1_000_000
EARLIEST_DATE = date(2000, 1, 1)

KINDS = ("orders", "operations")

ORDER_COLUMNS = (
    "order_id", "store_id", "sku", "date", "quantity", "sales", "discounts",
    "refunds", "cogs", "fulfillment", "platform_fees", "other_costs",
)
OPERATION_COLUMNS = (
    "store_id", "sku", "date", "ad_spend", "inventory", "inbound_inventory", "complete",
)
ORDER_MONEY_COLUMNS = ("sales", "discounts", "refunds", "cogs", "fulfillment", "platform_fees", "other_costs")

MONEY_RE = re.compile(r"^-?(?:0|[1-9]\d{0,11})(?:\.\d{1,2})?$")
INTEGER_RE = re.compile(r"^-?\d{1,12}$")

TEMPLATES = {
    "orders": (
        "order_id,store_id,sku,date,quantity,sales,discounts,refunds,cogs,"
        "fulfillment,platform_fees,other_costs\n"
        "ORD-2026-0001,us-store,TRAVEL-BAG-01,2026-11-01,1,49.00,3.92,0.00,"
        "16.00,6.50,7.35,0.90\n"
    ),
    "operations": (
        "store_id,sku,date,ad_spend,inventory,inbound_inventory,complete\n"
        "us-store,TRAVEL-BAG-01,2026-11-01,125.00,210,120,true\n"
    ),
}


@dataclass
class ParsedBatch:
    kind: str
    filename: str
    sha256: str
    total_rows: int = 0
    accepted: list = field(default_factory=list)
    skipped_rows: int = 0
    rejected_rows: int = 0
    duplicate_rows: int = 0
    totals: ImportTotals = field(default_factory=ImportTotals)
    issues: list[ImportIssue] = field(default_factory=list)
    checks: list[ImportCheck] = field(default_factory=list)

    @property
    def can_commit(self) -> bool:
        return self.rejected_rows == 0 and self.total_rows > 0

    @property
    def issue_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.issues:
            counts[item.code] = counts.get(item.code, 0) + 1
        return counts


def template_csv(kind: str) -> str:
    if kind not in KINDS:
        raise DomainError("import_kind_invalid", "仅支持 orders 或 operations 导入", 404)
    return TEMPLATES[kind]


def _chunks(values: list, size: int = 400):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _operation_id(item: dict) -> str:
    natural = f"{item['store_id']}-{item['product_id']}-{item['date']}"
    return natural if len(natural) <= 80 else new_id()


def _issue(batch: ParsedBatch, row: int, code: str, message: str, field_name: str | None = None):
    if len(batch.issues) < MAX_ISSUES:
        batch.issues.append(ImportIssue(row=row, field=field_name, code=code, message=message))


def _money(raw: str | None, row: int, name: str, batch: ParsedBatch) -> int | None:
    text = (raw or "").strip().replace(",", "")
    if text.startswith("$"):
        text = text[1:].strip()
    if not text:
        _issue(batch, row, "missing_money", f"{name} 不能为空", name)
        return None
    if not MONEY_RE.fullmatch(text):
        _issue(batch, row, "invalid_money", f"{name} 必须是最多两位小数的金额", name)
        return None
    try:
        value = Decimal(text) * 100
    except InvalidOperation:
        _issue(batch, row, "invalid_money", f"{name} 无法解析为金额", name)
        return None
    if value != value.to_integral_value():
        # The regex already limits scale, so this is a defensive guard.
        _issue(batch, row, "invalid_money", f"{name} 最多支持两位小数", name)
        return None
    cents = int(value)
    if abs(cents) > MAX_MONEY_CENTS:
        _issue(batch, row, "money_out_of_range", f"{name} 超出允许范围", name)
        return None
    return cents


def _integer(raw: str | None, row: int, name: str, batch: ParsedBatch, *, minimum: int) -> int | None:
    text = (raw or "").strip().replace(",", "")
    if not INTEGER_RE.fullmatch(text):
        _issue(batch, row, "invalid_integer", f"{name} 必须是整数", name)
        return None
    value = int(text)
    if value < minimum:
        _issue(batch, row, "invalid_integer", f"{name} 不能小于 {minimum}", name)
        return None
    if value > MAX_QUANTITY:
        _issue(batch, row, "invalid_integer", f"{name} 超出允许范围", name)
        return None
    return value


def _boolean(raw: str | None, row: int, batch: ParsedBatch) -> bool | None:
    text = (raw or "").strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    _issue(batch, row, "invalid_boolean", "complete 只能是 true/false 或 1/0", "complete")
    return None


def _business_date(raw: str | None, row: int, batch: ParsedBatch) -> date | None:
    text = (raw or "").strip()
    try:
        value = date.fromisoformat(text)
    except ValueError:
        _issue(batch, row, "invalid_date", "date 必须是 YYYY-MM-DD", "date")
        return None
    if value < EARLIEST_DATE or value > datetime.now(timezone.utc).date():
        _issue(batch, row, "date_out_of_range", "date 必须在 2000-01-01 与今天之间", "date")
        return None
    return value


def _header(reader: csv.reader, kind: str, batch: ParsedBatch) -> dict[str, int] | None:
    try:
        raw = next(reader)
    except StopIteration:
        raise DomainError("import_empty", "CSV 文件为空", 422)
    header = [cell.strip().lower().lstrip("\ufeff") for cell in raw]
    if len(set(header)) != len(header):
        raise DomainError("import_header_duplicate", "CSV 表头存在重复列", 422)
    required = ORDER_COLUMNS if kind == "orders" else OPERATION_COLUMNS
    missing = [name for name in required if name not in header]
    if missing:
        raise DomainError(
            "import_header_invalid",
            f"CSV 表头缺少列：{', '.join(missing)}",
            422,
            {"missing": missing, "required": list(required)},
        )
    batch.checks.append(ImportCheck(
        code="header_valid", ok=True,
        detail=f"必需列齐全，忽略额外列 {max(0, len(header) - len(required))} 个",
    ))
    return {name: index for index, name in enumerate(header)}


def _parse_rows(
    session, kind: str, text: str, filename: str, tenant_id: str,
) -> ParsedBatch:
    batch = ParsedBatch(
        kind=kind, filename=filename,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    reader = csv.reader(io.StringIO(text))
    columns = _header(reader, kind, batch)
    if columns is None:
        return batch

    stores = {
        store.id: store
        for store in session.scalars(select(Store).where(Store.tenant_id == tenant_id))
    }
    products = {
        product.sku: product
        for product in session.scalars(select(Product).where(Product.tenant_id == tenant_id))
    }

    candidates: list[dict] = []
    for raw in reader:
        if not any(cell.strip() for cell in raw):
            continue
        batch.total_rows += 1
        if batch.total_rows > MAX_ROWS:
            raise DomainError(
                "import_too_many_rows", f"单次最多导入 {MAX_ROWS} 行", 413,
                {"max_rows": MAX_ROWS},
            )
        row_number = reader.line_num
        before = len(batch.issues)
        values = {name: (raw[index].strip() if index < len(raw) else "") for name, index in columns.items()}

        store_id = values.get("store_id", "")
        if not store_id:
            _issue(batch, row_number, "missing_store", "store_id 不能为空", "store_id")
        elif store_id not in stores:
            _issue(batch, row_number, "unknown_store", f"店铺 {store_id} 不存在", "store_id")
        sku = values.get("sku", "")
        if not sku:
            _issue(batch, row_number, "missing_sku", "sku 不能为空", "sku")
        elif sku not in products:
            _issue(batch, row_number, "unknown_sku", f"SKU {sku} 未在主数据中登记", "sku")
        business_date = _business_date(values.get("date"), row_number, batch)

        if kind == "orders":
            order_id = values.get("order_id", "")
            if not order_id:
                _issue(batch, row_number, "missing_order_id", "order_id 不能为空", "order_id")
            elif len(order_id) > 80:
                _issue(batch, row_number, "invalid_order_id", "order_id 最多 80 个字符", "order_id")
            quantity = _integer(values.get("quantity"), row_number, "quantity", batch, minimum=1)
            money = {
                name: _money(values.get(name), row_number, name, batch)
                for name in ORDER_MONEY_COLUMNS
            }
            if all(value is not None for value in money.values()):
                if money["discounts"] + money["refunds"] > money["sales"]:
                    _issue(
                        batch, row_number, "discount_exceeds_sales",
                        "折扣与退款之和不能超过销售额", "sales",
                    )
            complete = len(batch.issues) == before
            if complete and store_id in stores and sku in products and business_date is not None and order_id:
                candidates.append({
                    "row": row_number, "order_id": order_id, "store_id": store_id,
                    "product_id": products[sku].id, "date": business_date,
                    "quantity": quantity, **money,
                })
        else:
            ad_spend = _money(values.get("ad_spend"), row_number, "ad_spend", batch)
            inventory = _integer(values.get("inventory"), row_number, "inventory", batch, minimum=0)
            inbound = _integer(
                values.get("inbound_inventory"), row_number, "inbound_inventory", batch, minimum=0,
            )
            complete_flag = _boolean(values.get("complete"), row_number, batch)
            complete = len(batch.issues) == before
            if complete and store_id in stores and sku in products and business_date is not None:
                candidates.append({
                    "row": row_number, "store_id": store_id,
                    "product_id": products[sku].id, "date": business_date,
                    "ad_spend": ad_spend, "inventory": inventory,
                    "inbound_inventory": inbound, "complete": complete_flag,
                })

        if len(batch.issues) > before:
            batch.rejected_rows += 1

    if batch.total_rows == 0:
        raise DomainError("import_empty", "CSV 没有数据行", 422)

    batch.accepted = candidates
    _dedupe_in_file(batch, batch.accepted)
    _compare_with_ledger(session, batch, batch.accepted, tenant_id)
    _totals(batch)
    batch.checks.append(ImportCheck(
        code="row_balance", ok=True,
        detail="每行净销售额与贡献利润按整数美分逐行加总",
    ))
    batch.checks.append(ImportCheck(
        code="duplicate_policy", ok=True,
        detail=f"文件内重复 {batch.duplicate_rows} 行已拒绝；与账本一致的重复行跳过 {batch.skipped_rows} 行",
    ))
    return batch


def _dedupe_in_file(batch: ParsedBatch, candidates: list[dict]):
    if batch.kind == "orders":
        key_of = lambda row: row["order_id"]
    else:
        key_of = lambda row: (row["store_id"], row["product_id"], row["date"])
    seen: dict = {}
    kept: list[dict] = []
    for row in candidates:
        key = key_of(row)
        if key in seen:
            batch.duplicate_rows += 1
            batch.rejected_rows += 1
            first = seen[key]
            _issue(
                batch, row["row"], "duplicate_in_file",
                f"与文件第 {first['row']} 行重复；同一批内不允许重复自然键",
            )
            continue
        seen[key] = row
        kept.append(row)
    candidates[:] = kept


def _existing_signature(kind: str, row) -> tuple:
    if kind == "orders":
        return (
            row.store_id, row.product_id, row.date, row.quantity, row.sales,
            row.discounts, row.refunds, row.cogs, row.fulfillment,
            row.platform_fees, row.other_costs,
        )
    return (row.ad_spend, row.inventory, row.inbound_inventory, row.complete)


def _candidate_signature(kind: str, row: dict) -> tuple:
    if kind == "orders":
        return (
            row["store_id"], row["product_id"], row["date"], row["quantity"],
            row["sales"], row["discounts"], row["refunds"], row["cogs"],
            row["fulfillment"], row["platform_fees"], row["other_costs"],
        )
    return (row["ad_spend"], row["inventory"], row["inbound_inventory"], row["complete"])


def _compare_with_ledger(session, batch: ParsedBatch, candidates: list[dict], tenant_id: str):
    existing: dict = {}
    if batch.kind == "orders":
        ids = [row["order_id"] for row in candidates]
        for chunk in _chunks(ids):
            for row in session.scalars(select(Order).where(
                Order.tenant_id == tenant_id, Order.id.in_(chunk),
            )):
                existing[row.id] = _existing_signature(batch.kind, row)
        key_of = lambda row: row["order_id"]
        conflict_code, conflict_message = "order_conflict", "订单已存在且字段与本次文件不一致"
    else:
        keys = [(row["store_id"], row["product_id"], row["date"]) for row in candidates]
        for chunk in _chunks(keys):
            for row in session.scalars(select(DailyOperation).where(
                DailyOperation.tenant_id == tenant_id,
                tuple_(DailyOperation.store_id, DailyOperation.product_id, DailyOperation.date).in_(chunk),
            )):
                existing[(row.store_id, row.product_id, row.date)] = _existing_signature(batch.kind, row)
        key_of = lambda row: (row["store_id"], row["product_id"], row["date"])
        conflict_code, conflict_message = "operation_conflict", "该店铺 SKU 当日已有经营记录且字段不一致"

    kept: list[dict] = []
    for row in candidates:
        key = key_of(row)
        if key not in existing:
            kept.append(row)
            continue
        if existing[key] == _candidate_signature(batch.kind, row):
            batch.skipped_rows += 1
            continue
        batch.rejected_rows += 1
        _issue(batch, row["row"], conflict_code, conflict_message)
    candidates[:] = kept


def _totals(batch: ParsedBatch):
    totals = ImportTotals(rows=len(batch.accepted))
    for row in batch.accepted:
        if batch.kind == "orders":
            for name in ORDER_MONEY_COLUMNS:
                setattr(totals, name, getattr(totals, name) + row[name])
            totals.net_sales += row["sales"] - row["discounts"] - row["refunds"]
        else:
            totals.ad_spend += row["ad_spend"]
            totals.inventory += row["inventory"]
            totals.inbound_inventory += row["inbound_inventory"]
    if batch.kind == "orders":
        totals.net_profit = totals.net_sales - sum(
            getattr(totals, name) for name in ("cogs", "ad_spend", "fulfillment", "platform_fees", "other_costs")
        )
    batch.totals = totals


def analyse_import(session, kind: str, text: str, filename: str, actor: Actor) -> ParsedBatch:
    if kind not in KINDS:
        raise DomainError("import_kind_invalid", "仅支持 orders 或 operations 导入", 404)
    if not text.strip():
        raise DomainError("import_empty", "CSV 文件为空", 422)
    batch = _parse_rows(session, kind, text.lstrip("\ufeff"), filename, actor.tenant_id)
    batch.checks.insert(0, ImportCheck(
        code="parse_complete", ok=batch.rejected_rows == 0,
        detail=f"共 {batch.total_rows} 行，拒绝 {batch.rejected_rows} 行",
    ))
    batch.checks.insert(1, ImportCheck(
        code="money_exact", ok=not any(item.code == "invalid_money" for item in batch.issues),
        detail="金额按十进制精确转换为整数美分，不经过浮点数",
    ))
    return batch


def preview_import(session, kind: str, text: str, filename: str, actor: Actor) -> ImportSummary:
    return summary_of(analyse_import(session, kind, text, filename, actor))


def commit_import(
    session, kind: str, text: str, filename: str, actor: Actor, request_id: str,
) -> ImportSummary:
    batch = analyse_import(session, kind, text, filename, actor)
    if not batch.can_commit:
        record(
            session, actor, "import.rejected", "import_batch", batch.sha256,
            request_id, outcome="rejected",
            details={
                "kind": kind, "filename": filename,
                "rejected_rows": batch.rejected_rows,
                "issue_counts": batch.issue_counts,
            },
        )
        raise DomainError(
            "import_rejected",
            f"导入被拒绝：{batch.rejected_rows} 行未通过校验，未写入任何数据",
            409,
            {"summary": summary_of(batch).model_dump(mode="json")},
        )

    status = "committed" if batch.accepted else "noop"
    row = ImportBatch(
        id=new_id(), tenant_id=actor.tenant_id, kind=kind, filename=filename,
        content_sha256=batch.sha256, status=status,
        total_rows=batch.total_rows, accepted_rows=len(batch.accepted),
        skipped_rows=batch.skipped_rows, rejected_rows=0,
        summary=batch.totals.model_dump(), issues=[],
        created_by=actor.id, request_id=request_id,
    )
    session.add(row)
    try:
        session.flush()
        if kind == "orders":
            session.add_all([Order(
                id=item["order_id"], tenant_id=actor.tenant_id,
                store_id=item["store_id"], product_id=item["product_id"],
                date=item["date"], quantity=item["quantity"],
                sales=item["sales"], discounts=item["discounts"], refunds=item["refunds"],
                cogs=item["cogs"], fulfillment=item["fulfillment"],
                platform_fees=item["platform_fees"], other_costs=item["other_costs"],
            ) for item in batch.accepted])
        else:
            session.add_all([DailyOperation(
                id=_operation_id(item),
                tenant_id=actor.tenant_id, store_id=item["store_id"],
                product_id=item["product_id"], date=item["date"],
                ad_spend=item["ad_spend"], inventory=item["inventory"],
                inbound_inventory=item["inbound_inventory"], complete=item["complete"],
            ) for item in batch.accepted])
        session.flush()
    except IntegrityError as exc:
        raise DomainError(
            "import_conflict", "导入期间出现并发写入冲突，请重新预览后提交", 409,
        ) from exc

    verified, detail = _read_back(session, batch, actor.tenant_id)
    batch.checks.append(ImportCheck(code="db_roundtrip", ok=verified, detail=detail))
    if not verified:
        raise DomainError(
            "import_reconciliation_failed", "写库后回读金额与文件不一致，事务已回滚", 500,
            {"detail": detail},
        )

    row.summary = {
        **batch.totals.model_dump(),
        "duplicate_rows": batch.duplicate_rows,
        "checks": [item.model_dump() for item in batch.checks],
    }
    record(
        session, actor, "import.committed", "import_batch", row.id, request_id,
        details={
            "kind": kind, "filename": filename, "status": status,
            "accepted_rows": len(batch.accepted), "skipped_rows": batch.skipped_rows,
            "totals": batch.totals.model_dump(),
        },
    )
    return summary_of(batch, row_id=row.id, status=status, created_at=row.created_at,
                      created_by=actor.id)


def _read_back(session, batch: ParsedBatch, tenant_id: str) -> tuple[bool, str]:
    if batch.kind == "orders":
        ids = [item["order_id"] for item in batch.accepted]
        expected = batch.totals
        actual = {name: 0 for name in ("sales", "discounts", "refunds", "cogs", "fulfillment", "platform_fees", "other_costs")}
        count = 0
        for chunk in _chunks(ids):
            row = session.execute(select(
                func.count(),
                func.sum(Order.sales), func.sum(Order.discounts), func.sum(Order.refunds),
                func.sum(Order.cogs), func.sum(Order.fulfillment),
                func.sum(Order.platform_fees), func.sum(Order.other_costs),
            ).where(Order.tenant_id == tenant_id, Order.id.in_(chunk))).one()
            count += int(row[0] or 0)
            for index, name in enumerate(actual, start=1):
                actual[name] += int(row[index] or 0)
        ok = count == len(ids) and all(actual[name] == getattr(expected, name) for name in actual)
        detail = f"回读 {count} 行；销售额 {actual['sales']}、采购 {actual['cogs']} 与文件一致" if ok else (
            f"回读 {count} 行，期望 {len(ids)} 行；金额合计不一致"
        )
        return ok, detail

    keys = [(item["store_id"], item["product_id"], item["date"]) for item in batch.accepted]
    expected = batch.totals
    ad_spend = inventory = inbound = 0
    count = 0
    for chunk in _chunks(keys):
        row = session.execute(select(
            func.count(), func.sum(DailyOperation.ad_spend),
            func.sum(DailyOperation.inventory), func.sum(DailyOperation.inbound_inventory),
        ).where(
            DailyOperation.tenant_id == tenant_id,
            tuple_(DailyOperation.store_id, DailyOperation.product_id, DailyOperation.date).in_(chunk),
        )).one()
        count += int(row[0] or 0)
        ad_spend += int(row[1] or 0)
        inventory += int(row[2] or 0)
        inbound += int(row[3] or 0)
    ok = (
        count == len(keys) and ad_spend == expected.ad_spend
        and inventory == expected.inventory and inbound == expected.inbound_inventory
    )
    detail = f"回读 {count} 行；广告费 {ad_spend}、库存 {inventory} 与文件一致" if ok else (
        f"回读 {count} 行，期望 {len(keys)} 行；指标合计不一致"
    )
    return ok, detail


def summary_of(
    batch: ParsedBatch, *, row_id: str | None = None, status: str | None = None,
    created_at=None, created_by: str | None = None,
) -> ImportSummary:
    if created_at is not None and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if status is None:
        status = "ready" if batch.can_commit else "blocked"
    return ImportSummary(
        id=row_id, kind=batch.kind, filename=batch.filename, status=status,
        can_commit=batch.can_commit, replayed=status == "noop",
        content_sha256=batch.sha256,
        total_rows=batch.total_rows, accepted_rows=len(batch.accepted),
        skipped_rows=batch.skipped_rows, rejected_rows=batch.rejected_rows,
        duplicate_rows=batch.duplicate_rows, totals=batch.totals,
        issues=batch.issues, issue_counts=batch.issue_counts,
        checks=batch.checks, created_by=created_by, created_at=created_at,
    )


def list_batches(session, tenant_id: str, limit: int = 50) -> list[ImportBatch]:
    return list(session.scalars(select(ImportBatch).where(
        ImportBatch.tenant_id == tenant_id,
    ).order_by(ImportBatch.created_at.desc()).limit(limit)))


def get_batch(session, batch_id: str, tenant_id: str) -> ImportBatch:
    row = session.scalar(select(ImportBatch).where(
        ImportBatch.id == batch_id, ImportBatch.tenant_id == tenant_id,
    ))
    if row is None:
        raise DomainError("import_batch_not_found", "导入批次不存在", 404)
    return row


def batch_view(row: ImportBatch) -> dict:
    from .views import iso_utc

    summary = row.summary or {}
    checks = summary.get("checks", [])
    totals = {
        key: value for key, value in summary.items()
        if key not in {"checks", "duplicate_rows"}
    }
    return {
        "id": row.id, "kind": row.kind, "filename": row.filename,
        "status": row.status, "can_commit": False, "replayed": row.status == "noop",
        "content_sha256": row.content_sha256, "total_rows": row.total_rows,
        "accepted_rows": row.accepted_rows, "skipped_rows": row.skipped_rows,
        "rejected_rows": row.rejected_rows,
        "duplicate_rows": summary.get("duplicate_rows", 0),
        "totals": ImportTotals(**{
            key: value for key, value in totals.items()
            if key in ImportTotals.model_fields
        }).model_dump(),
        "issues": row.issues or [], "issue_counts": {},
        "checks": checks, "created_by": row.created_by,
        "created_at": iso_utc(row.created_at),
    }
