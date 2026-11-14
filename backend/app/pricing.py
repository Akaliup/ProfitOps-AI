"""Two deterministic money helpers. Neither calls a model or a marketplace.

Retail price suggestions (``suggest_price``)
    Integer-cent, read-only rules for a saved weekly report. They never write a
    price, never call a seller API, and never enter the approval executor.
    A variable-cost total of zero is not a price basis unless the caller sets
    ``costs_known=True`` (every cost component was observed and really is zero).
    The weekly report cannot prove that omitted fee columns were imported, so it
    leaves ``costs_known`` false and returns ``INSUFFICIENT_DATA``.

LLM token cost estimates (``estimate_cost_usd``)
    Public list prices for usage accounting, not invoices. A missing model,
    missing usage or missing price returns ``None`` so callers never render an
    unknown model cost as $0.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

PRICING_VERSION = "2026-11-02"
PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"

# USD per 1,000,000 tokens. Peak rates are used so estimates are conservative.
MODEL_PRICING: dict[str, dict[str, Any]] = {
    "deepseek-flash": {
        "currency": "USD",
        "unit_tokens": 1_000_000,
        "input_cache_hit": Decimal("0.006"),
        "input_cache_miss": Decimal("0.30"),
        "output": Decimal("1.20"),
        "rate_period": "peak",
        "pricing_version": PRICING_VERSION,
        "source": PRICING_SOURCE,
    },
}

RULE_VERSION = "pricing-rules-v1"


def suggest_price(*, net_sales: int, units: int, refunds: int, variable_costs: int,
                  inventory_risk: str | None = None, current_price: int | None = None,
                  min_margin_pct: Decimal = Decimal("10"), costs_known: bool = False) -> dict[str, Any]:
    """Produce a bounded price suggestion in integer cents without side effects.

    ``costs_known`` distinguishes a confirmed zero-cost basis from a missing one.
    Positive ``variable_costs`` are themselves evidence of an observed cost.
    Zero is usable only when the caller proves the components were imported.
    """
    if units <= 0 or net_sales <= 0:
        return {"status": "INSUFFICIENT_DATA", "suggested_price": None, "current_price": current_price,
                "reason_code": "missing_units_or_sales", "rule_version": RULE_VERSION}
    if variable_costs < 0 or (variable_costs == 0 and not costs_known):
        return {"status": "INSUFFICIENT_DATA", "suggested_price": None, "current_price": current_price,
                "reason_code": "missing_cost_basis", "rule_version": RULE_VERSION}
    asp = (Decimal(net_sales) / Decimal(units)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    baseline = current_price if current_price and current_price > 0 else int(asp)
    refund_rate = Decimal(refunds) / Decimal(net_sales)
    margin = Decimal(net_sales - variable_costs) / Decimal(net_sales) * 100
    if refund_rate >= Decimal("0.15"):
        reason, target = "observe_refunds", baseline
    elif inventory_risk in {"CRITICAL", "HIGH"} and margin >= min_margin_pct:
        reason, target = "protect_margin_with_low_stock", int((Decimal(baseline) * Decimal("1.05")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    elif margin < min_margin_pct:
        reason, target = "test_demand_for_low_margin", int((Decimal(baseline) * Decimal("0.95")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    else:
        reason, target = "observe", baseline
    return {"status": "OK", "suggested_price": target, "current_price": baseline,
            "asp": int(asp),
            "refund_rate_pct": float((refund_rate * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "margin_pct": float(margin.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "reason_code": reason, "rule_version": RULE_VERSION}


def estimate_cost_usd(model: str | None, usage: dict[str, Any] | None) -> dict[str, Any] | None:
    """Estimate USD cost from provider token usage.

    Returns ``None`` when the model has no versioned price or when usage is
    missing/invalid. The result is an estimate only; it is not billing data.
    """
    pricing = MODEL_PRICING.get(str(model or ""))
    if pricing is None or not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if not isinstance(prompt, int) or isinstance(prompt, bool):
        return None
    if not isinstance(completion, int) or isinstance(completion, bool):
        return None
    cached = usage.get("cached_tokens", 0)
    if not isinstance(cached, int) or isinstance(cached, bool) or cached < 0:
        cached = 0
    cached = min(cached, prompt)
    uncached = prompt - cached
    unit = Decimal(pricing["unit_tokens"])
    amount = (
        Decimal(cached) * pricing["input_cache_hit"]
        + Decimal(uncached) * pricing["input_cache_miss"]
        + Decimal(completion) * pricing["output"]
    ) / unit
    return {
        "currency": pricing["currency"],
        "amount_usd": float(round(amount, 6)),
        "pricing_version": pricing["pricing_version"],
        "rate_period": pricing["rate_period"],
        "source": pricing["source"],
        "model": model,
        "tokens": {
            "prompt": prompt,
            "cached": cached,
            "uncached": uncached,
            "completion": completion,
        },
        "note": "Public list-price estimate, not an invoice.",
    }
