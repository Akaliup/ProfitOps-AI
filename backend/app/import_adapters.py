"""Offline report-column adapters for common marketplace exports."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ImportAdapter:
    id: str
    label: str
    kind: str
    mapping: dict[str, str]
    description: str


ADAPTERS = (
    ImportAdapter("amazon-orders", "Amazon 风格订单报表", "orders", {
        "store_id": "store_id", "order_id": "order-id", "order_date": "purchase-date",
        "sku": "sku", "quantity": "quantity", "gross_sales": "item-price",
        "refund_amount": "refund-amount", "cogs": "cogs", "currency": "currency",
    }, "将 Amazon 导出列规范化为订单账本字段"),
    ImportAdapter("shopify-orders", "Shopify 风格订单导出", "orders", {
        "store_id": "store_id", "order_id": "Name", "order_date": "Created at",
        "sku": "Lineitem sku", "quantity": "Lineitem quantity", "gross_sales": "Total",
        "refund_amount": "Refunded Amount", "cogs": "Cost", "currency": "Currency",
    }, "将 Shopify 导出列规范化为订单账本字段"),
    ImportAdapter("amazon-ads", "Amazon 风格广告报表", "ads", {
        "store_id": "store_id", "date": "date", "sku": "advertised-sku",
        "campaign_id": "campaign-id", "campaign_name": "campaign-name", "ad_spend": "cost",
        "attributed_sales": "sales", "attributed_orders": "orders", "clicks": "clicks", "impressions": "impressions",
    }, "将 Amazon 广告导出列规范化为活动明细"),
)


def adapter_catalog(kind: str | None = None) -> list[dict]:
    return [{"id": item.id, "label": item.label, "kind": item.kind,
             "mapping": item.mapping, "description": item.description}
            for item in ADAPTERS if kind is None or item.kind == kind]


def get_adapter(adapter_id: str, kind: str) -> ImportAdapter:
    for item in ADAPTERS:
        if item.id == adapter_id and item.kind == kind:
            return item
    raise ValueError("unknown_adapter")
