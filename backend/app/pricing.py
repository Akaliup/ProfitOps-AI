"""Versioned model pricing used only for cost estimates.

Prices are public list prices, not invoices. A missing model, missing usage or
missing price returns ``None`` so callers never render an unknown cost as $0.
"""
from __future__ import annotations

from decimal import Decimal
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
