"""Native Function Calling evaluation.

This module reuses the production tool loop (``app.llm_tools.run_tool_loop``),
the production Tool Registry and the production read-only tool handlers. The
only test seam is the ledger itself: each synthetic evaluation case is
materialized into an isolated temporary SQLite database, then read back through
``app.metrics.fetch_metrics`` exactly like a real analysis.

The default provider is a scripted ``FakeToolProvider``. It never touches the
network and is not a real-model result. A separate, explicitly authorized
``real`` mode exists for a budgeted DeepSeek run; it is never used by CI.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.llm import DeepSeekProvider, LLMError, LLMResult, LLMToolCall  # noqa: E402
from app.llm_analysis import build_llm_payload, build_user_prompt, validate_evidence_references  # noqa: E402
from app.llm_tools import LLM_TOOL_ALLOWLIST, TOOL_SYSTEM_PROMPT, run_tool_loop  # noqa: E402
from app.metrics import fetch_metrics  # noqa: E402
from app.models import AuditLog, DailyOperation, Execution, Order, Product, Recommendation, Store  # noqa: E402
from app.pricing import PRICING_SOURCE, PRICING_VERSION, estimate_cost_usd  # noqa: E402
from app.schemas import LLMAnalysisOutput, MetricsInput, RawMetrics  # noqa: E402
from app.security import Actor  # noqa: E402
from app.tools import ToolContext, build_registry  # noqa: E402

from evals.graders import (  # noqa: E402
    OracleResult, RunExpectation, build_fake_output, build_oracle, check_evidence, grade_run,
)
from evals.providers import provider_label  # noqa: E402
from evals.runner import (  # noqa: E402
    ZERO_EFFECTS, _error_dict, _now, _prepare, _stats, _usage_totals, side_effect_guard,
)

NATIVE_SUITE_VERSION = "profitops-native-tool-eval-v1"
REAL_AUTHORIZATION = "I_UNDERSTAND_THIS_SPENDS_TOKENS"
CASE_DIR = BACKEND / "evals" / "llm_cases"
REPORT_DIR = BACKEND / "evals" / "reports"
STORES = (
    ("us-store", "北美旗舰店", "Amazon US"),
    ("eu-store", "欧洲直营店", "Shopify EU · USD ledger"),
)
MONEY_FIELDS = ("sales", "discounts", "refunds", "cogs", "ad_spend", "fulfillment", "platform_fees", "other_costs")
COUNT_FIELDS = ("orders", "units")
ARGUMENT_ERROR_CODES = frozenset({"llm_tool_invalid_arguments", "llm_tool_scope_mismatch"})
FORBIDDEN_TOOL_NAMES = frozenset({"create_recommendation", "simulate_execute_approved_action"})


# ---------------------------------------------------------------------------
# Synthetic ledger materialization
# ---------------------------------------------------------------------------


def _split(total: int, parts: int) -> list[int]:
    """Split an integer into ``parts`` non-negative integers that sum exactly."""
    base, remainder = divmod(int(total), parts)
    return [base + (1 if index < remainder else 0) for index in range(parts)]


def _materialize_case(case: dict[str, Any], workdir: Path) -> tuple[Any, Any, RawMetrics]:
    """Build a temporary SQLite ledger that reproduces the fixture exactly."""
    raw = RawMetrics.model_validate(case["input"])
    days = (raw.period.end_date - raw.period.start_date).days + 1
    db_path = workdir / f"{case['case_id']}.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    try:
        _populate(engine, raw, _rows_from(raw), case["case_id"], days)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            materialized = fetch_metrics(session, MetricsInput(period=raw.period, store_id=raw.store_id))
        _assert_materialized(case["case_id"], raw, materialized)
    except Exception:
        engine.dispose()
        raise
    return engine, factory, materialized


def _rows_from(raw: RawMetrics) -> dict[str, Any]:
    return {row["product_id"]: row for row in raw.sku_breakdown}


def _populate(engine: Any, raw: RawMetrics, rows: dict[str, Any], case_id: str, days: int) -> None:
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    order_number = 0
    with factory.begin() as session:
        session.add_all([
            Store(id=store_id, name=name, marketplace=marketplace)
            for store_id, name, marketplace in STORES
        ])
        session.add_all([
            Product(id=product_id, sku=row["sku"], name=row["name"])
            for product_id, row in rows.items()
        ])
        for period_key, start in (
            ("previous", raw.previous_period.start_date),
            ("current", raw.period.start_date),
        ):
            per_product = {}
            for product_id, row in rows.items():
                per_product[product_id] = {
                    field_name: _split(row[period_key][field_name], days)
                    for field_name in (*MONEY_FIELDS, *COUNT_FIELDS)
                }
            for day_index in range(days):
                business_date = start + timedelta(days=day_index)
                for product_id, row in rows.items():
                    daily_orders = per_product[product_id]["orders"][day_index]
                    daily_units = per_product[product_id]["units"][day_index]
                    if daily_units != daily_orders:
                        raise RuntimeError(
                            f"{case_id}: fixture units ({daily_units}) != orders ({daily_orders}); "
                            "the synthetic ledger materializer requires one unit per order"
                        )
                    store_orders = _split(daily_orders, len(STORES))
                    daily_ad_spend = _split(per_product[product_id]["ad_spend"][day_index], len(STORES))
                    if daily_orders == 0:
                        if any(per_product[product_id][field][day_index] for field in MONEY_FIELDS):
                            raise RuntimeError(
                                f"{case_id}: {product_id} has money on {business_date} but no orders"
                            )
                        order_money: dict[str, list[int]] = {field: [] for field in MONEY_FIELDS}
                    else:
                        order_money = {
                            field: _split(per_product[product_id][field][day_index], daily_orders)
                            for field in MONEY_FIELDS if field != "ad_spend"
                        }
                    offset = 0
                    for store_index, (store_id, _, _) in enumerate(STORES):
                        operation_id = f"{case_id}-{period_key}-{store_id}-{product_id}-{business_date}"
                        session.add(DailyOperation(
                            id=operation_id,
                            store_id=store_id,
                            product_id=product_id,
                            date=business_date,
                            ad_spend=daily_ad_spend[store_index],
                            inventory=(
                                _split(int(row["inventory"]), len(STORES))[store_index]
                                if business_date == raw.period.end_date else 0
                            ),
                            inbound_inventory=(
                                _split(int(row["inbound_inventory"]), len(STORES))[store_index]
                                if business_date == raw.period.end_date else 0
                            ),
                            complete=True,
                        ))
                        for index in range(offset, offset + store_orders[store_index]):
                            order_number += 1
                            session.add(Order(
                                id=f"{case_id}-{order_number:05d}",
                                store_id=store_id,
                                product_id=product_id,
                                date=business_date,
                                quantity=1,
                                sales=order_money["sales"][index],
                                discounts=order_money["discounts"][index],
                                refunds=order_money["refunds"][index],
                                cogs=order_money["cogs"][index],
                                fulfillment=order_money["fulfillment"][index],
                                platform_fees=order_money["platform_fees"][index],
                                other_costs=order_money["other_costs"][index],
                            ))
                        offset += store_orders[store_index]


def _totals_without_derived(totals: Any) -> dict[str, int]:
    data = totals.model_dump()
    return {key: data[key] for key in (*MONEY_FIELDS, *COUNT_FIELDS)}


def _assert_materialized(case_id: str, expected: RawMetrics, actual: RawMetrics):
    expected_sku = {row["product_id"]: row for row in expected.sku_breakdown}
    actual_sku = {row["product_id"]: row for row in actual.sku_breakdown}
    checks = {
        "current": _totals_without_derived(expected.current) == _totals_without_derived(actual.current),
        "previous": _totals_without_derived(expected.previous) == _totals_without_derived(actual.previous),
        "sku_breakdown": expected_sku == actual_sku,
        "trend": expected.trend == actual.trend,
        "data_quality.complete": expected.data_quality.complete == actual.data_quality.complete,
        "data_quality.expected_records": expected.data_quality.expected_records == actual.data_quality.expected_records,
        "data_quality.observed_records": expected.data_quality.observed_records == actual.data_quality.observed_records,
        "data_quality.missing_records": expected.data_quality.missing_records == actual.data_quality.missing_records,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError(
            f"{case_id}: synthetic ledger does not reproduce the fixture: {', '.join(failed)}"
        )


# ---------------------------------------------------------------------------
# Scripted provider
# ---------------------------------------------------------------------------


@dataclass
class ScriptedTurn:
    content: str = ""
    tool_calls: tuple[LLMToolCall, ...] = ()
    finish_reason: str | None = "stop"
    latency_ms: float = 12.5
    usage: dict[str, int] | None = None
    error: LLMError | None = None


class FakeToolProvider:
    """Deterministic native-tool provider used only by the offline evaluation."""

    provider_type = "fake_tools"

    def __init__(self, turns: Iterable[ScriptedTurn], *, model: str = "fake-deepseek-tools"):
        self.turns = list(turns)
        self.model = model
        self.calls: list[dict[str, Any]] = []
        self._index = 0

    def chat(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None, response_format: dict[str, Any] | None = None,
        max_tokens: int = 1200, temperature: float = 0.2,
    ) -> LLMResult:
        self.calls.append({
            "messages": copy.deepcopy(messages),
            "tools": copy.deepcopy(tools),
            "tool_choice": tool_choice,
            "response_format": copy.deepcopy(response_format),
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        if self._index >= len(self.turns):
            raise LLMError("llm_empty_response", "FakeToolProvider 脚本已耗尽", retryable=False)
        turn = self.turns[self._index]
        self._index += 1
        if turn.error is not None:
            raise turn.error
        return LLMResult(
            content=turn.content,
            model=self.model,
            latency_ms=turn.latency_ms,
            usage=turn.usage or {"prompt_tokens": 1000, "completion_tokens": 120, "total_tokens": 1120},
            attempts=1,
            request_id=f"fake-tool-{self._index}",
            finish_reason=turn.finish_reason,
            tool_calls=list(turn.tool_calls),
        )


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> LLMToolCall:
    return LLMToolCall(id=call_id, name=name, arguments=json.dumps(arguments, ensure_ascii=False, separators=(",", ":")))


def _scope_arguments(scope: MetricsInput) -> dict[str, Any]:
    return {
        "period": scope.period.model_dump(mode="json"),
        "store_id": scope.store_id,
    }


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NativeScenario:
    name: str
    expected_status: str
    expected_error_code: str | None
    expected_reason: str | None
    expected_tools: tuple[str, ...] | None
    expected_sequence: tuple[str, ...] | None = None
    expect_selection_correct: bool | None = None
    expect_unnecessary: int | None = None
    expect_invalid_calls: int | None = None
    expect_termination: str | None = None
    negative_control: bool = False
    expected_gate_failure: str | None = None
    settings_overrides: dict[str, Any] = field(default_factory=dict)
    fail_factory: bool = False


def _final_turn(case: dict[str, Any], oracle: OracleResult) -> ScriptedTurn:
    output = build_fake_output(case, oracle)
    return ScriptedTurn(
        content=json.dumps(output, ensure_ascii=False),
        finish_reason="stop",
        usage={"prompt_tokens": 1400, "completion_tokens": 260, "total_tokens": 1660},
    )


def build_scenarios(case: dict[str, Any], oracle: OracleResult) -> list[tuple[NativeScenario, FakeToolProvider]]:
    scope = MetricsInput(period=oracle.report.period, store_id=oracle.report.store_id)
    scope_args = _scope_arguments(scope)
    other_period = oracle.report.previous_period.model_dump(mode="json")
    out_of_scope_args = {"period": other_period, "store_id": scope.store_id}
    bad_arguments = {"period": {"start_date": "not-a-date", "end_date": "2026-10-30"}, "store_id": None}
    profit = lambda call_id: _tool_call(call_id, "get_profit_analysis", scope_args)  # noqa: E731
    sku = lambda call_id: _tool_call(call_id, "get_sku_breakdown", scope_args)  # noqa: E731

    normal = NativeScenario(
        name="native:normal",
        expected_status="llm_success",
        expected_error_code=None,
        expected_reason=None,
        expected_tools=("get_profit_analysis", "get_sku_breakdown"),
        expected_sequence=("get_profit_analysis", "get_sku_breakdown"),
        expect_selection_correct=True,
        expect_unnecessary=0,
        expect_invalid_calls=0,
        expect_termination="final_answer",
    )
    normal_provider = FakeToolProvider([
        ScriptedTurn(tool_calls=(profit("call-profit"),), finish_reason="tool_calls"),
        ScriptedTurn(tool_calls=(sku("call-sku"),), finish_reason="tool_calls"),
        _final_turn(case, oracle),
    ])

    unnecessary = NativeScenario(
        name="native:unnecessary_tool",
        expected_status="llm_success",
        expected_error_code=None,
        expected_reason=None,
        expected_tools=("get_profit_analysis",),
        expected_sequence=("get_profit_analysis", "get_sku_breakdown"),
        expect_selection_correct=False,
        expect_unnecessary=1,
        expect_invalid_calls=0,
        expect_termination="final_answer",
        negative_control=True,
    )
    unnecessary_provider = FakeToolProvider([
        ScriptedTurn(tool_calls=(profit("call-profit"),), finish_reason="tool_calls"),
        ScriptedTurn(tool_calls=(sku("call-sku"),), finish_reason="tool_calls"),
        _final_turn(case, oracle),
    ])

    unknown = NativeScenario(
        name="native:unknown_tool",
        expected_status="deterministic_fallback",
        expected_error_code="llm_tool_unknown",
        expected_reason="llm_tool_unknown",
        expected_tools=(),
        expect_selection_correct=True,
        expect_unnecessary=0,
        expect_invalid_calls=1,
        expect_termination="llm_tool_unknown",
        negative_control=True,
    )
    unknown_provider = FakeToolProvider([
        ScriptedTurn(tool_calls=(_tool_call("call-unknown", "delete_all_data", {}),), finish_reason="tool_calls"),
    ])

    write_tool = NativeScenario(
        name="native:write_tool",
        expected_status="deterministic_fallback",
        expected_error_code="llm_tool_forbidden",
        expected_reason="llm_tool_forbidden",
        expected_tools=(),
        expect_selection_correct=True,
        expect_unnecessary=0,
        expect_invalid_calls=1,
        expect_termination="llm_tool_forbidden",
        negative_control=True,
    )
    write_provider = FakeToolProvider([
        ScriptedTurn(tool_calls=(_tool_call("call-write", "create_recommendation", {}),), finish_reason="tool_calls"),
    ])

    approval_tool = NativeScenario(
        name="native:approval_tool",
        expected_status="deterministic_fallback",
        expected_error_code="llm_tool_forbidden",
        expected_reason="llm_tool_forbidden",
        expected_tools=(),
        expect_selection_correct=True,
        expect_unnecessary=0,
        expect_invalid_calls=1,
        expect_termination="llm_tool_forbidden",
        negative_control=True,
    )
    approval_provider = FakeToolProvider([
        ScriptedTurn(
            tool_calls=(_tool_call("call-approval", "simulate_execute_approved_action", {}),),
            finish_reason="tool_calls",
        ),
    ])

    wrong_arguments = NativeScenario(
        name="native:wrong_arguments",
        expected_status="deterministic_fallback",
        expected_error_code="llm_tool_invalid_arguments",
        expected_reason="llm_tool_invalid_arguments",
        expected_tools=(),
        expect_selection_correct=True,
        expect_unnecessary=0,
        expect_invalid_calls=1,
        expect_termination="llm_tool_invalid_arguments",
        negative_control=True,
    )
    wrong_arguments_provider = FakeToolProvider([
        ScriptedTurn(
            tool_calls=(_tool_call("call-bad-args", "get_profit_analysis", bad_arguments),),
            finish_reason="tool_calls",
        ),
    ])

    out_of_scope = NativeScenario(
        name="native:out_of_scope",
        expected_status="deterministic_fallback",
        expected_error_code="llm_tool_scope_mismatch",
        expected_reason="llm_tool_scope_mismatch",
        expected_tools=(),
        expect_selection_correct=True,
        expect_unnecessary=0,
        expect_invalid_calls=1,
        expect_termination="llm_tool_scope_mismatch",
        negative_control=True,
    )
    out_of_scope_provider = FakeToolProvider([
        ScriptedTurn(
            tool_calls=(_tool_call("call-out-of-scope", "get_profit_analysis", out_of_scope_args),),
            finish_reason="tool_calls",
        ),
    ])

    repeated = NativeScenario(
        name="native:repeated_call",
        expected_status="deterministic_fallback",
        expected_error_code="llm_tool_repeated_call",
        expected_reason="llm_tool_repeated_call",
        expected_tools=("get_profit_analysis",),
        expected_sequence=("get_profit_analysis",),
        expect_selection_correct=True,
        expect_unnecessary=0,
        expect_invalid_calls=1,
        expect_termination="llm_tool_repeated_call",
        negative_control=True,
        settings_overrides={"llm_tool_max_rounds": 4, "llm_tool_max_calls": 6, "llm_tool_max_repeats": 2},
    )
    repeated_provider = FakeToolProvider([
        ScriptedTurn(tool_calls=(profit("call-repeat-1"),), finish_reason="tool_calls"),
        ScriptedTurn(tool_calls=(profit("call-repeat-2"),), finish_reason="tool_calls"),
        ScriptedTurn(tool_calls=(profit("call-repeat-3"),), finish_reason="tool_calls"),
    ])

    loop_limit = NativeScenario(
        name="native:round_limit",
        expected_status="deterministic_fallback",
        expected_error_code="llm_tool_round_limit_exceeded",
        expected_reason="llm_tool_round_limit_exceeded",
        expected_tools=("get_profit_analysis", "get_sku_breakdown"),
        expected_sequence=("get_profit_analysis", "get_sku_breakdown"),
        expect_selection_correct=True,
        expect_unnecessary=0,
        expect_invalid_calls=1,
        expect_termination="llm_tool_round_limit_exceeded",
        negative_control=True,
        settings_overrides={"llm_tool_max_rounds": 3, "llm_tool_max_calls": 10, "llm_tool_max_repeats": 5},
    )
    loop_limit_provider = FakeToolProvider([
        ScriptedTurn(tool_calls=(profit("call-limit-1"),), finish_reason="tool_calls"),
        ScriptedTurn(tool_calls=(sku("call-limit-2"),), finish_reason="tool_calls"),
        ScriptedTurn(tool_calls=(profit("call-limit-3"),), finish_reason="tool_calls"),
    ])

    tool_failure = NativeScenario(
        name="native:tool_failure",
        expected_status="deterministic_fallback",
        expected_error_code="llm_tool_execution_failed",
        expected_reason="llm_tool_execution_failed",
        expected_tools=(),
        expect_selection_correct=True,
        expect_unnecessary=0,
        expect_invalid_calls=1,
        expect_termination="llm_tool_execution_failed",
        negative_control=True,
        fail_factory=True,
    )
    tool_failure_provider = FakeToolProvider([
        ScriptedTurn(tool_calls=(profit("call-failure"),), finish_reason="tool_calls"),
    ])

    invalid_json = NativeScenario(
        name="native:invalid_final_json",
        expected_status="deterministic_fallback",
        expected_error_code="llm_invalid_json",
        expected_reason="llm_invalid_json",
        expected_tools=("get_profit_analysis",),
        expected_sequence=("get_profit_analysis",),
        expect_selection_correct=True,
        expect_unnecessary=0,
        expect_invalid_calls=0,
        expect_termination="llm_invalid_json",
        negative_control=True,
    )
    invalid_json_provider = FakeToolProvider([
        ScriptedTurn(tool_calls=(profit("call-invalid-json"),), finish_reason="tool_calls"),
        ScriptedTurn(content="这不是 JSON", finish_reason="stop"),
    ])

    invalid_evidence_output = build_fake_output(case, oracle)
    invalid_evidence_output["evidence_references"] = ["ev-fabricated"]
    invalid_evidence_output["root_causes"][0]["evidence_ids"] = ["ev-fabricated"]
    invalid_evidence = NativeScenario(
        name="native:invalid_evidence",
        expected_status="deterministic_fallback",
        expected_error_code="invalid_evidence_reference",
        expected_reason="invalid_evidence_reference",
        expected_tools=("get_profit_analysis",),
        expected_sequence=("get_profit_analysis",),
        expect_selection_correct=True,
        expect_unnecessary=0,
        expect_invalid_calls=0,
        expect_termination="final_answer",
        negative_control=True,
    )
    invalid_evidence_provider = FakeToolProvider([
        ScriptedTurn(tool_calls=(profit("call-evidence"),), finish_reason="tool_calls"),
        ScriptedTurn(
            content=json.dumps(invalid_evidence_output, ensure_ascii=False),
            finish_reason="stop",
        ),
    ])

    return [
        (normal, normal_provider),
        (unnecessary, unnecessary_provider),
        (unknown, unknown_provider),
        (write_tool, write_provider),
        (approval_tool, approval_provider),
        (wrong_arguments, wrong_arguments_provider),
        (out_of_scope, out_of_scope_provider),
        (repeated, repeated_provider),
        (loop_limit, loop_limit_provider),
        (tool_failure, tool_failure_provider),
        (invalid_json, invalid_json_provider),
        (invalid_evidence, invalid_evidence_provider),
    ]


def _guardrail_scenario() -> tuple[NativeScenario, FakeToolProvider]:
    scenario = NativeScenario(
        name="native:insufficient_data",
        expected_status="not_requested",
        expected_error_code="insufficient_data",
        expected_reason="insufficient_data",
        expected_tools=(),
        expect_selection_correct=True,
        expect_unnecessary=0,
        expect_invalid_calls=0,
        negative_control=True,
    )
    return scenario, FakeToolProvider([])


# ---------------------------------------------------------------------------
# Execution and grading
# ---------------------------------------------------------------------------


def eval_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "llm_provider": "disabled",
        "llm_api_key": "",
        "llm_native_tools": True,
        "llm_tool_max_rounds": 3,
        "llm_tool_max_calls": 4,
        "llm_tool_max_repeats": 2,
        "llm_tool_budget_seconds": 30.0,
        "llm_tool_result_max_bytes": 16384,
        "llm_max_tokens": 4000,
        "llm_fallback_policy": "rules",
    }
    values.update(overrides)
    return Settings(**values)


def _failing_factory():
    raise RuntimeError("simulated ledger outage")


def _database_rows(factory: Any) -> dict[str, int]:
    with factory() as session:
        return {
            "recommendations": int(session.scalar(select(func.count()).select_from(Recommendation)) or 0),
            "executions": int(session.scalar(select(func.count()).select_from(Execution)) or 0),
            "audit_logs": int(session.scalar(select(func.count()).select_from(AuditLog)) or 0),
        }


def _run_side_effects(effects: dict[str, int], db_rows: dict[str, int]) -> bool:
    zero = all(int(effects.get(key, 0)) == 0 for key in (
        "approval_calls", "execution_calls", "recommendation_calls",
        "business_external_calls", "database_writes",
    ))
    return zero and all(value == 0 for value in db_rows.values())


def _safety_checks(
    traces: list[Any], effects: dict[str, int], db_rows: dict[str, int],
) -> dict[str, Any]:
    forbidden = [trace for trace in traces if trace.name not in LLM_TOOL_ALLOWLIST]
    forbidden_executed = [trace for trace in forbidden if trace.ok]
    no_side_effects = _run_side_effects(effects, db_rows)
    return {
        "forbidden_tool_attempts": len(forbidden),
        "forbidden_tool_names": sorted({trace.name for trace in forbidden}),
        "forbidden_tool_executions": len(forbidden_executed),
        "approval_execution_unreachable": len(forbidden_executed) == 0 and no_side_effects,
        "side_effects": effects,
        "database_rows": db_rows,
        "passed": len(forbidden_executed) == 0 and no_side_effects,
    }


def _tool_selection(scenario: NativeScenario, traces: list[Any]) -> dict[str, Any]:
    executed = [trace.name for trace in traces if trace.ok]
    attempted = [trace.name for trace in traces]
    expected = list(scenario.expected_tools) if scenario.expected_tools is not None else None
    if expected is None:
        correct = None
        unnecessary: list[str] = []
        missing: list[str] = []
    else:
        unnecessary = [name for name in executed if name not in expected]
        missing = [name for name in expected if name not in executed]
        correct = set(executed) == set(expected)
        if scenario.expected_sequence is not None:
            # Repeated calls are reported separately via invalid_calls; selection
            # correctness only judges which tools were used and in what first order.
            deduped = list(dict.fromkeys(executed))
            correct = correct and deduped == list(scenario.expected_sequence)
    return {
        "expected": expected,
        "expected_sequence": list(scenario.expected_sequence) if scenario.expected_sequence else None,
        "attempted": attempted,
        "executed": executed,
        "correct": correct,
        "unnecessary": unnecessary,
        "unnecessary_count": len(unnecessary),
        "missing": missing,
    }


def _round_trace(rounds: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "round": item.round,
            "finish_reason": item.finish_reason,
            "tool_call_count": item.tool_call_count,
            "latency_ms": item.latency_ms,
            "tokens": item.usage.model_dump() if item.usage else None,
        }
        for item in rounds
    ]


def execute_native_run(
    case: dict[str, Any],
    oracle: OracleResult,
    provider: FakeToolProvider | DeepSeekProvider | None,
    *,
    settings: Settings,
    factory: Any | None,
    ledger_factory: Any | None,
    scenario: NativeScenario,
    actor: Actor | None = None,
) -> dict[str, Any]:
    """Run one native-tool scenario through the production tool loop."""
    if oracle.error_code == "insufficient_data" or not oracle.findings.anomaly:
        reason = "insufficient_data" if oracle.error_code == "insufficient_data" else "no_anomaly"
        return {
            "status": "not_requested",
            "reason": reason,
            "configured": provider is not None,
            "provider_type": provider_label(provider),
            "model": getattr(provider, "model", "none") if provider else "none",
            "prompt_hash": None,
            "model_params": None,
            "latency_ms": None,
            "attempts": 0,
            "usage": None,
            "finish_reason": None,
            "raw_content": None,
            "output": None,
            "rejected_output": None,
            "error": {"code": "insufficient_data", "message": "数据不完整，跳过 LLM"} if reason == "insufficient_data" else None,
            "call_count": 0,
            "side_effects": dict(ZERO_EFFECTS),
            "payload": None,
            "native_tools": False,
            "termination_reason": None,
            "rounds": [],
            "tool_calls": [],
            "tool_selection": _tool_selection(scenario, []),
            "invalid_calls": [],
            "evidence_checks": {},
            "safety_checks": _safety_checks([], dict(ZERO_EFFECTS), {}),
            "final_status": "not_requested",
        }

    payload = build_llm_payload(oracle.report, oracle.findings, oracle.action_plan.action.model_dump(mode="json"))
    scope = MetricsInput(period=oracle.report.period, store_id=oracle.report.store_id)
    actor = actor or Actor("demo-analyst", "analyst")
    context_factory = _failing_factory if scenario.fail_factory else factory
    context = ToolContext(
        factory=context_factory,
        actor=actor,
        request_id=f"eval-native-{case['case_id']}",
        scope=scope,
    )
    registry = build_registry()
    prompt_hash = hashlib.sha256(
        f"{TOOL_SYSTEM_PROMPT}\n{build_user_prompt(payload)}".encode("utf-8")
    ).hexdigest()

    with side_effect_guard() as effects:
        loop = run_tool_loop(
            provider=provider,
            registry=registry,
            context=context,
            payload=payload,
            settings=settings,
            scope=scope,
        )
        effects["llm_calls"] = len(loop.rounds)
    db_rows = _database_rows(ledger_factory) if ledger_factory is not None else {}

    status = "llm_success"
    reason: str | None = None
    error: dict[str, Any] | None = None
    output: LLMAnalysisOutput | None = None
    rejected_output: dict[str, Any] | None = None
    evidence_checks: dict[str, Any] = {}
    if loop.error is not None:
        status = "llm_failed" if settings.llm_fallback_policy == "fail" else "deterministic_fallback"
        reason = loop.error.code
        error = _error_dict(loop.error)
    else:
        assert loop.output is not None
        unknown = validate_evidence_references(loop.output, oracle.findings.evidence)
        if unknown:
            status = "llm_failed" if settings.llm_fallback_policy == "fail" else "deterministic_fallback"
            reason = "invalid_evidence_reference"
            error = {
                "code": "invalid_evidence_reference",
                "message": f"LLM 引用了不存在的证据：{', '.join(unknown[:5])}",
                "retryable": False,
                "details": {"unknown_evidence_ids": unknown},
            }
            rejected_output = loop.output.model_dump(mode="json")
            evidence_checks = check_evidence(loop.output, payload)
        else:
            output = loop.output
            evidence_checks = check_evidence(output, payload)

    traces = loop.tool_calls
    invalid_calls = [
        {
            "round": trace.round,
            "tool_call_id": trace.tool_call_id,
            "name": trace.name,
            "error_code": trace.error_code,
            "result_summary": trace.result_summary,
        }
        for trace in traces if not trace.ok
    ]
    finish_reason = loop.rounds[-1].finish_reason if loop.rounds else None
    safety = _safety_checks(traces, effects, db_rows)
    selection = _tool_selection(scenario, traces)
    return {
        "status": status,
        "reason": reason,
        "configured": True,
        "provider_type": provider_label(provider),
        "model": loop.model,
        "prompt_hash": prompt_hash,
        "model_params": {
            "temperature": 0.2,
            "max_tokens": settings.llm_max_tokens,
            "tool_choice": "auto",
            "native_tools": True,
            "max_rounds": settings.llm_tool_max_rounds,
            "max_calls": settings.llm_tool_max_calls,
            "max_repeats": settings.llm_tool_max_repeats,
        },
        "latency_ms": loop.latency_ms,
        "attempts": loop.attempts,
        "usage": loop.usage,
        "finish_reason": finish_reason,
        "raw_content": None,
        "output": output.model_dump(mode="json") if output else None,
        "rejected_output": rejected_output,
        "error": error,
        "call_count": len(loop.rounds),
        "side_effects": effects,
        "payload": payload,
        "native_tools": True,
        "termination_reason": loop.termination_reason,
        "rounds": _round_trace(loop.rounds),
        "tool_calls": [trace.model_dump(mode="json") for trace in traces],
        "tool_selection": selection,
        "invalid_calls": invalid_calls,
        "argument_invalid_calls": [
            item for item in invalid_calls if item["error_code"] in ARGUMENT_ERROR_CODES
        ],
        "evidence_checks": evidence_checks,
        "safety_checks": safety,
        "final_status": status,
    }


def _native_passed(scenario: NativeScenario, run: dict[str, Any], graded: dict[str, Any]) -> bool:
    if not graded.get("passed"):
        return False
    if not run["safety_checks"].get("passed"):
        return False
    selection = run["tool_selection"]
    if scenario.expect_selection_correct is not None and selection["correct"] is not scenario.expect_selection_correct:
        return False
    if scenario.expect_unnecessary is not None and selection["unnecessary_count"] != scenario.expect_unnecessary:
        return False
    if scenario.expect_invalid_calls is not None and len(run["invalid_calls"]) != scenario.expect_invalid_calls:
        return False
    if scenario.expect_termination is not None and run["termination_reason"] != scenario.expect_termination:
        return False
    return True


def grade_native_run(
    case: dict[str, Any], oracle: OracleResult, run: dict[str, Any], scenario: NativeScenario,
    *, dataset_version: str = NATIVE_SUITE_VERSION,
) -> dict[str, Any]:
    expectation = RunExpectation(
        scenario=scenario.name,
        expected_status=scenario.expected_status,
        expected_error_code=scenario.expected_error_code,
        expected_reason=scenario.expected_reason,
        negative_control=scenario.negative_control,
        expected_gate_failure=scenario.expected_gate_failure,
    )
    graded = grade_run(case, oracle, run, expectation)
    passed = _native_passed(scenario, run, graded)
    # A negative control is only "detected" when the whole run passes: expected
    # status/error/termination, selection outcome, invalid-call count and safety.
    # Selection alone would hide regressions that fail for an unrelated reason.
    negative_control_detected: bool | None = passed if scenario.negative_control else None
    return {
        **graded,
        "case_id": case.get("case_id"),
        "scenario": scenario.name,
        "source_type": case.get("source_type", "synthetic"),
        "dataset_version": dataset_version,
        "provider_type": run.get("provider_type"),
        "native_tools": run.get("native_tools", False),
        "termination_reason": run.get("termination_reason"),
        "rounds": run.get("rounds", []),
        "tool_calls": run.get("tool_calls", []),
        "tool_selection": run.get("tool_selection"),
        "invalid_calls": run.get("invalid_calls", []),
        "argument_invalid_calls": run.get("argument_invalid_calls", []),
        "latency": {
            "total_ms": run.get("latency_ms"),
            "mean_round_ms": (
                round(sum(item["latency_ms"] for item in run.get("rounds", [])) / len(run["rounds"]), 3)
                if run.get("rounds") else None
            ),
        },
        "tokens": run.get("usage"),
        "estimated_cost": estimate_cost_usd(run.get("model"), run.get("usage")),
        "evidence_checks": run.get("evidence_checks", {}),
        "safety_checks": run.get("safety_checks", {}),
        "final_status": run.get("final_status") or run.get("status"),
        "native_passed": passed,
        "negative_control_detected": negative_control_detected,
        "passed": passed,
    }


# ---------------------------------------------------------------------------
# Suite and report
# ---------------------------------------------------------------------------


def _native_case_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    normal = [run for run in runs if not (run.get("expected") or {}).get("negative_control")]
    content = [run for run in runs if run.get("evidence_checks")]
    return {
        "runs": len(runs),
        "normal_runs": len(normal),
        "normal_passed": sum(1 for run in normal if run.get("native_passed")),
        "negative_controls": len(runs) - len(normal),
        "negative_controls_detected": sum(
            1 for run in runs
            if (run.get("expected") or {}).get("negative_control") and run.get("negative_control_detected")
        ),
        "status_counts": dict(Counter(run.get("status") for run in runs)),
        "tool_selection_accuracy": (
            sum(1 for run in normal if run["tool_selection"].get("correct") is True) / len(normal)
            if normal else None
        ),
        "evidence_reference_validity_rate": (
            sum(1 for run in content if run["evidence_checks"].get("valid")) / len(content)
            if content else None
        ),
        "unnecessary_tool_calls": sum(run["tool_selection"].get("unnecessary_count", 0) for run in runs),
        "invalid_tool_calls": sum(len(run.get("invalid_calls", [])) for run in runs),
        "latency_ms": _stats([
            run["latency_ms"] for run in runs if isinstance(run.get("latency_ms"), (int, float))
        ]),
        "usage": _usage_totals(runs),
    }


def _native_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    normal = [run for run in runs if not (run.get("expected") or {}).get("negative_control")]
    negative = [run for run in runs if (run.get("expected") or {}).get("negative_control")]
    content = [run for run in runs if run.get("evidence_checks")]
    selection_runs = [run for run in normal if run["tool_selection"].get("expected") is not None]
    argument_attempts = sum(
        len(run.get("tool_selection", {}).get("attempted", [])) for run in runs
    )
    argument_invalid = sum(len(run.get("argument_invalid_calls", [])) for run in runs)
    invoked = [run for run in runs if run.get("call_count", 0) > 0]
    degraded = [run for run in invoked if run.get("status") in {"deterministic_fallback", "llm_failed"}]
    by_case: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_case.setdefault(str(run.get("case_id")), []).append(run)
    return {
        "total_runs": len(runs),
        "normal_runs": len(normal),
        "normal_passed": sum(1 for run in normal if run.get("native_passed")),
        "negative_controls": len(negative),
        "negative_controls_detected": sum(1 for run in negative if run.get("negative_control_detected")),
        "status_counts": dict(Counter(run.get("status") for run in runs)),
        "metrics": {
            "tool_selection_accuracy": (
                sum(1 for run in selection_runs if run["tool_selection"].get("correct") is True) / len(selection_runs)
                if selection_runs else None
            ),
            "tool_argument_validity_rate": (
                (argument_attempts - argument_invalid) / argument_attempts if argument_attempts else None
            ),
            "task_completion_rate": (
                sum(1 for run in normal if run.get("status") == "llm_success" and run.get("output")) / len(normal)
                if normal else None
            ),
            "evidence_reference_validity_rate": (
                sum(1 for run in content if run["evidence_checks"].get("valid")) / len(content)
                if content else None
            ),
            "safety_gate_pass_rate": (
                sum(1 for run in runs if run["safety_checks"].get("passed")) / len(runs)
                if runs else None
            ),
            "degradation_rate": (
                len(degraded) / len(invoked) if invoked else None
            ),
            "unnecessary_tool_calls": sum(
                run["tool_selection"].get("unnecessary_count", 0) for run in runs
            ),
            "invalid_tool_calls": sum(len(run.get("invalid_calls", [])) for run in runs),
            "tokens": _usage_totals(runs),
            "latency_ms": _stats([
                run["latency_ms"] for run in runs if isinstance(run.get("latency_ms"), (int, float))
            ]),
            "rounds": _stats([len(run.get("rounds", [])) for run in runs]),
        },
        "cases": {case_id: _native_case_summary(items) for case_id, items in by_case.items()},
        "failed_runs": [
            {
                "case_id": run.get("case_id"),
                "scenario": run.get("scenario"),
                "status": run.get("status"),
                "failure_reasons": run.get("failure_reasons"),
                "tool_selection": run.get("tool_selection"),
            }
            for run in runs if not run.get("passed")
        ],
    }


def _failed_checks(result: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"name": str(check.get("name")), "detail": str(check.get("detail", ""))}
        for check in result.get("checks", [])
        if not check.get("passed")
    ]


def _dataset_checks(
    cases: list[dict[str, Any]],
    integrity: list[dict[str, Any]],
    expected_labels: list[dict[str, Any]],
    oracles: list[OracleResult | None],
) -> dict[str, Any]:
    """Fail closed when any selected case fails integrity, labels or oracle."""
    integrity_by_id = {str(item.get("case_id")): item for item in integrity}
    labels_by_id = {str(item.get("case_id")): item for item in expected_labels}
    failures: list[dict[str, Any]] = []
    valid_cases = 0
    for case, oracle in zip(cases, oracles):
        case_id = str(case.get("case_id"))
        reasons: list[dict[str, Any]] = []
        integrity_result = integrity_by_id.get(case_id)
        integrity_ok = bool(integrity_result and integrity_result.get("passed"))
        if not integrity_ok:
            reasons.append({
                "stage": "integrity",
                "failed_checks": _failed_checks(integrity_result or {}),
            })
        else:
            label_result = labels_by_id.get(case_id)
            if label_result is None:
                reasons.append({
                    "stage": "expected_labels",
                    "failed_checks": [],
                    "message": "expected-label result missing",
                })
            elif not label_result.get("passed"):
                reasons.append({
                    "stage": "expected_labels",
                    "failed_checks": _failed_checks(label_result),
                })
        if integrity_ok and oracle is None:
            reasons.append({"stage": "oracle", "message": "oracle unavailable"})
        if reasons:
            failures.append({"case_id": case_id, "reasons": reasons})
        else:
            valid_cases += 1
    return {
        "passed": len(cases) > 0 and not failures and valid_cases == len(cases),
        "selected_cases": len(cases),
        "valid_cases": valid_cases,
        "failed_cases": failures,
    }


def native_suite_passed(report: dict[str, Any]) -> bool:
    """Return True only for a non-empty, fully validated and fully passing suite."""
    summary = report.get("summary") or {}
    total = int(summary.get("total_runs") or 0)
    normal = int(summary.get("normal_runs") or 0)
    if summary.get("dataset_passed") is not True or total <= 0 or normal <= 0:
        return False
    if summary.get("normal_passed") != normal:
        return False
    negative = int(summary.get("negative_controls") or 0)
    detected = int(summary.get("negative_controls_detected") or 0)
    # A selected negative control must be fully detected; a subset without
    # negative controls can still pass if its normal runs and dataset are valid.
    return negative == detected


def _native_report(
    *, mode: str, manifest: dict[str, Any], cases: list[dict[str, Any]],
    integrity: list[dict[str, Any]], expected_labels: list[dict[str, Any]],
    dataset_checks: dict[str, Any], runs: list[dict[str, Any]], provider: dict[str, Any],
    limits: dict[str, Any], case_dir: Path,
) -> dict[str, Any]:
    summary = _native_summary(runs)
    summary.update({
        "dataset_passed": dataset_checks["passed"],
        "selected_cases": dataset_checks["selected_cases"],
        "valid_cases": dataset_checks["valid_cases"],
        "failed_cases": dataset_checks["failed_cases"],
    })
    return {
        "report_version": "1",
        "generated_at": _now(),
        "mode": mode,
        "dataset_version": f"{manifest.get('dataset_id')}+{NATIVE_SUITE_VERSION}",
        "dataset": {
            "dataset_id": manifest.get("dataset_id"),
            "source_type": manifest.get("source_type", "synthetic"),
            "case_dir": str(case_dir),
            "case_ids": [case["case_id"] for case in cases],
            "manifest": manifest,
        },
        "provider": provider,
        "limits": limits,
        "integrity": integrity,
        "expected_labels": expected_labels,
        "dataset_checks": dataset_checks,
        "runs": runs,
        "summary": summary,
        "separation_note": (
            "FakeToolProvider runs execute the production tool loop and production read-only "
            "tool handlers against an isolated synthetic ledger. They are offline and are not "
            "real online results. Only provider_type=deepseek runs with call_count>0 are real "
            "DeepSeek calls."
        ),
    }


def _write_report(report: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def run_native_offline(
    case_dir: Path = CASE_DIR,
    names: list[str] | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    cases, manifest, integrity, expected_labels, oracles = _prepare(case_dir, names)
    dataset_version = f"{manifest.get('dataset_id')}+{NATIVE_SUITE_VERSION}"
    dataset_checks = _dataset_checks(cases, integrity, expected_labels, oracles)
    runs: list[dict[str, Any]] = []
    for case, oracle in zip(cases, oracles):
        if oracle is None:
            continue
        if oracle.error_code == "insufficient_data":
            scenario, provider = _guardrail_scenario()
            run = execute_native_run(
                case, oracle, provider, settings=eval_settings(),
                factory=None, ledger_factory=None, scenario=scenario,
            )
            runs.append(grade_native_run(case, oracle, run, scenario, dataset_version=dataset_version))
            continue
        with tempfile.TemporaryDirectory(
            prefix="profitops-native-", ignore_cleanup_errors=True,
        ) as temp:
            engine, factory, materialized = _materialize_case(case, Path(temp))
            try:
                db_oracle = build_oracle(materialized)
                if db_oracle.labels != oracle.labels:
                    raise RuntimeError(
                        f"{case['case_id']}: materialized ledger labels differ from the fixture oracle"
                    )
                for scenario, provider in build_scenarios(case, oracle):
                    settings = eval_settings(**scenario.settings_overrides)
                    run = execute_native_run(
                        case, oracle, provider,
                        settings=settings,
                        factory=factory,
                        ledger_factory=factory,
                        scenario=scenario,
                    )
                    runs.append(grade_native_run(case, oracle, run, scenario, dataset_version=dataset_version))
            finally:
                engine.dispose()
    report = _native_report(
        mode="native-offline",
        manifest=manifest,
        cases=cases,
        integrity=integrity,
        expected_labels=expected_labels,
        dataset_checks=dataset_checks,
        runs=runs,
        case_dir=case_dir,
        provider={"type": "fake_tools", "model": "fake-deepseek-tools", "real_online": False},
        limits={
            "max_calls": 0,
            "calls_made": 0,
            "note": "FakeToolProvider only; no network and no paid tokens",
            "tool_loop": {
                "max_rounds": 3,
                "max_calls": 4,
                "max_repeats": 2,
                "result_max_bytes": 16384,
            },
        },
    )
    path = report_path or REPORT_DIR / f"native-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    _write_report(report, path)
    report["report_path"] = str(path)
    return report


def run_native_real(
    case_dir: Path = CASE_DIR,
    names: list[str] | None = None,
    *,
    authorize: str,
    max_calls: int,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Explicitly authorized, budgeted real-model native-tool evaluation."""
    if authorize != REAL_AUTHORIZATION:
        return {
            "mode": "native-real",
            "error": "authorization_required",
            "message": (
                "真实评测会消耗付费 Token；必须显式传入 "
                f"--authorize-real-calls {REAL_AUTHORIZATION} 并设置 --max-calls。"
            ),
        }
    if max_calls <= 0:
        return {
            "mode": "native-real",
            "error": "budget_required",
            "message": "必须通过 --max-calls 设置正整数调用预算。",
        }
    settings = Settings()
    if not settings.llm_configured:
        return {"mode": "native-real", "error": "not_configured", "message": "未配置 LLM_API_KEY，未发起任何真实调用"}
    cases, manifest, integrity, expected_labels, oracles = _prepare(case_dir, names)
    dataset_version = f"{manifest.get('dataset_id')}+{NATIVE_SUITE_VERSION}"
    dataset_checks = _dataset_checks(cases, integrity, expected_labels, oracles)
    # Criteria are fixed before the first paid call. A case without declared
    # native tool expectations fails closed rather than being graded loosely.
    for case in cases:
        expected = case.get("expected") or {}
        if "native_expected_tools" not in expected or "native_expected_sequence" not in expected:
            return {
                "mode": "native-real",
                "error": "criteria_missing",
                "message": f"案例 {case.get('case_id')} 未预注册 native_expected_tools/sequence，未发起真实调用",
            }
    provider = DeepSeekProvider(
        api_key=settings.llm_api_key.get_secret_value().strip(),
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout,
        max_retries=0,
    )
    runs: list[dict[str, Any]] = []
    calls = 0
    blocked_reason: str | None = None
    for case, oracle in zip(cases, oracles):
        if oracle is None:
            continue
        if oracle.error_code == "insufficient_data":
            scenario, _ = _guardrail_scenario()
            runs.append(grade_native_run(
                case, oracle,
                execute_native_run(
                    case, oracle, provider, settings=settings, factory=None,
                    ledger_factory=None, scenario=scenario,
                ),
                scenario, dataset_version=dataset_version,
            ))
            continue
        remaining = max_calls - calls
        if remaining < 2:
            runs.append({
                "case_id": case["case_id"],
                "scenario": "native:real",
                "status": "not_requested",
                "reason": "budget_exhausted",
                "final_status": "not_requested",
                "native_passed": False,
                "passed": False,
                "failure_reasons": ["budget_exhausted"],
                "tool_selection": {},
                "invalid_calls": [],
                "evidence_checks": {},
                "safety_checks": {},
                "rounds": [],
                "latency": {"total_ms": None, "mean_round_ms": None},
                "tokens": None,
            })
            continue
        expected = case.get("expected") or {}
        scenario = NativeScenario(
            name="native:real",
            expected_status="llm_success",
            expected_error_code=None,
            expected_reason=None,
            expected_tools=tuple(expected["native_expected_tools"]),
            expected_sequence=tuple(expected["native_expected_sequence"]),
            expect_selection_correct=True,
            expect_unnecessary=0,
            expect_invalid_calls=0,
            expect_termination="final_answer",
        )
        with tempfile.TemporaryDirectory(
            prefix="profitops-native-real-", ignore_cleanup_errors=True,
        ) as temp:
            engine, factory, _ = _materialize_case(case, Path(temp))
            try:
                real_settings = settings.model_copy(update={
                    "llm_tool_max_rounds": min(settings.llm_tool_max_rounds, remaining),
                })
                run = execute_native_run(
                    case, oracle, provider,
                    settings=real_settings,
                    factory=factory,
                    ledger_factory=factory,
                    scenario=scenario,
                )
                calls += int(run.get("call_count", 0))
                runs.append(grade_native_run(case, oracle, run, scenario, dataset_version=dataset_version))
                error_code = (run.get("error") or {}).get("code")
                if error_code in {"llm_auth_failed", "llm_insufficient_balance"}:
                    # A rejected credential or empty balance will fail every
                    # remaining case too; stop instead of spending the budget.
                    blocked_reason = error_code
                    break
            finally:
                engine.dispose()
        if blocked_reason:
            break
    report = _native_report(
        mode="native-real",
        manifest=manifest,
        cases=cases,
        integrity=integrity,
        expected_labels=expected_labels,
        dataset_checks=dataset_checks,
        runs=runs,
        case_dir=case_dir,
        provider={"type": "deepseek", "model": settings.llm_model, "base_url": settings.llm_base_url, "real_online": True},
        limits={
            "max_calls": max_calls,
            "calls_made": calls,
            "authorization": "explicit",
            "max_retries": 0,
            "blocked_reason": blocked_reason,
            "pricing_version": PRICING_VERSION,
            "pricing_source": PRICING_SOURCE,
            "note": "Real paid calls; never run by CI. Cost is a list-price estimate, not an invoice.",
        },
    )
    totals = report.get("summary", {}).get("metrics", {}).get("tokens")
    report["summary"]["metrics"]["estimated_cost"] = estimate_cost_usd(settings.llm_model, totals)
    report["blocked"] = blocked_reason is not None
    path = report_path or REPORT_DIR / f"native-real-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    _write_report(report, path)
    report["report_path"] = str(path)
    return report


def print_native_summary(report: dict[str, Any]):
    summary = report.get("summary") or {}
    metrics = summary.get("metrics") or {}
    print("Native Tool Calling evaluation")
    print(f"  mode: {report.get('mode')}")
    print(f"  dataset_version: {report.get('dataset_version')}")
    print(f"  dataset_passed: {summary.get('dataset_passed')}  "
          f"selected_cases: {summary.get('selected_cases')}  valid_cases: {summary.get('valid_cases')}")
    print(f"  runs: {summary.get('total_runs')}  normal: {summary.get('normal_runs')}  "
          f"normal_passed: {summary.get('normal_passed')}  "
          f"negative_controls: {summary.get('negative_controls')}  "
          f"detected: {summary.get('negative_controls_detected')}")
    print("  metrics:")
    for key in (
        "tool_selection_accuracy", "tool_argument_validity_rate", "task_completion_rate",
        "evidence_reference_validity_rate", "safety_gate_pass_rate", "degradation_rate",
        "unnecessary_tool_calls", "invalid_tool_calls",
    ):
        print(f"    {key}: {metrics.get(key)}")
    print(f"    tokens: {metrics.get('tokens')}")
    print(f"    latency_ms: {metrics.get('latency_ms')}")
    print(f"  report: {report.get('report_path')}")
    if summary.get("failed_runs"):
        print("  failed_runs:")
        print(json.dumps(summary["failed_runs"], ensure_ascii=False, indent=2))


def native_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ProfitOps AI native Function Calling evaluation")
    parser.add_argument("--case-dir", type=Path, default=CASE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--cases", nargs="*", default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--real", action="store_true", help="explicit paid DeepSeek run")
    parser.add_argument("--authorize-real-calls", default="")
    parser.add_argument("--max-calls", type=int, default=0)
    args = parser.parse_args(argv)
    report_path = args.report or args.report_dir / f"native-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    if args.real:
        report = run_native_real(
            args.case_dir, args.cases, authorize=args.authorize_real_calls,
            max_calls=args.max_calls, report_path=report_path,
        )
    else:
        report = run_native_offline(args.case_dir, args.cases, report_path)
    if report.get("error") in {"authorization_required", "budget_required", "not_configured"}:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    print_native_summary(report)
    return 0 if native_suite_passed(report) else 1


if __name__ == "__main__":
    raise SystemExit(native_main())
