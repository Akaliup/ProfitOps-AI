"""Offline tests for the LLM evaluation runner and hard gates.

These tests never call the real DeepSeek API. The real pilot is a separate,
explicitly budgeted command in ``backend/evals/runner.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import services as service_module
from app.llm import LLMError
from app.schemas import LLMAnalysisOutput
from evals import runner
from evals.graders import (
    RunExpectation, audit_numbers, build_fake_output, build_oracle, check_action,
    check_expected, check_forbidden_claims, check_integrity, grade_run, load_cases,
)
from evals.providers import FakeProvider

CASE_DIR = Path(__file__).resolve().parents[1] / "evals" / "llm_cases"


@pytest.fixture(scope="module")
def cases() -> dict[str, dict]:
    return {case["case_id"]: case for case in load_cases(CASE_DIR)}


def _oracle(case):
    integrity = check_integrity(case)
    assert integrity.passed, [check for check in integrity.checks if not check.passed]
    assert integrity.raw is not None
    return build_oracle(integrity.raw)


def _valid(case, oracle) -> dict:
    return build_fake_output(case, oracle)


def _payload(oracle) -> dict:
    from app.llm_analysis import build_llm_payload
    return build_llm_payload(oracle.report, oracle.findings, oracle.action_plan.action.model_dump(mode="json"))


def test_all_cases_have_integrity_and_deterministic_expected_labels(cases):
    assert set(cases) == {"ad_spend_pressure", "fulfillment_pressure", "refund_pressure", "insufficient_data"}
    for case in cases.values():
        assert case["source_type"] == "synthetic"
        assert "no LLM" in case["generation_method"]
        integrity = check_integrity(case)
        assert integrity.passed, [check.name for check in integrity.checks if not check.passed]
        oracle = build_oracle(integrity.raw)
        expected = check_expected(case, oracle)
        assert expected.passed, [check for check in expected.checks if not check.passed]


def test_offline_suite_covers_normal_and_negative_controls(tmp_path):
    report = runner.run_offline(CASE_DIR, None, tmp_path / "offline.json")
    summary = report["summary"]
    assert summary["normal_runs"] == 4
    assert summary["normal_passed"] == 4
    assert summary["negative_controls"] == 21
    assert summary["negative_controls_detected"] == 21
    assert summary["real_online_calls"] == 0
    assert summary["fake_provider_calls"] == 21
    assert report["provider"]["type"] == "fake"
    assert "not real online results" in report["separation_note"]
    assert (tmp_path / "offline.json").exists()
    scenarios = {run["scenario"] for run in report["runs"]}
    assert {
        "offline:normal", "offline:invalid_evidence", "offline:wrong_amount",
        "offline:invalid_schema", "offline:timeout", "offline:truncated",
        "offline:no_key", "offline:fail_policy",
    } <= scenarios


def test_normal_run_passes_all_content_gates_and_has_no_business_side_effects(cases):
    case = cases["ad_spend_pressure"]
    oracle = _oracle(case)
    provider = FakeProvider(data=_valid(case, oracle))
    run = runner.execute_provider_run(case, oracle, provider)
    graded = grade_run(case, oracle, run, RunExpectation("test:normal", "llm_success"))
    assert graded["passed"] is True
    assert graded["status"] == "llm_success"
    assert all(value is True for value in graded["hard_gates"].values())
    assert graded["side_effects"]["approval_calls"] == 0
    assert graded["side_effects"]["execution_calls"] == 0
    assert graded["side_effects"]["recommendation_calls"] == 0
    assert graded["side_effects"]["business_external_calls"] == 0
    assert graded["side_effects"]["database_writes"] == 0
    assert graded["side_effects"]["llm_calls"] == 1
    assert len(graded["prompt_hash"]) == 64
    assert graded["model_params"] == {"temperature": 0.2, "max_tokens": 4000}


def test_insufficient_data_skips_provider_entirely(cases):
    case = cases["insufficient_data"]
    oracle = _oracle(case)
    provider = FakeProvider(data={"summary": "不应被调用"})
    run = runner.execute_provider_run(case, oracle, provider)
    assert run["status"] == "not_requested"
    assert run["reason"] == "insufficient_data"
    assert run["call_count"] == 0
    assert provider.calls == []
    graded = grade_run(
        case, oracle, run,
        RunExpectation("test:guardrail", "not_requested", expected_error_code="insufficient_data", expected_reason="insufficient_data"),
    )
    assert graded["passed"] is True
    assert graded["side_effects"]["llm_calls"] == 0


def test_invalid_evidence_reference_degrades_and_is_recorded(cases):
    case = cases["ad_spend_pressure"]
    oracle = _oracle(case)
    data = _valid(case, oracle)
    data["evidence_references"] = ["ev-fabricated"]
    run = runner.execute_provider_run(case, oracle, FakeProvider(data=data))
    graded = grade_run(
        case, oracle, run,
        RunExpectation("test:invalid_evidence", "deterministic_fallback", expected_error_code="invalid_evidence_reference", negative_control=True),
    )
    assert graded["passed"] is True
    assert graded["status"] == "deterministic_fallback"
    assert graded["error"]["code"] == "invalid_evidence_reference"
    assert graded["evidence_consistency"]["valid"] is False
    assert "ev-fabricated" in graded["evidence_consistency"]["unknown"]
    assert graded["rejected_output"] is not None


def test_wrong_amount_is_rejected_by_numeric_gate(cases):
    case = cases["ad_spend_pressure"]
    oracle = _oracle(case)
    data = _valid(case, oracle)
    data["summary"] = "广告支出异常，净利润下降 999999 美分。"
    run = runner.execute_provider_run(case, oracle, FakeProvider(data=data))
    graded = grade_run(
        case, oracle, run,
        RunExpectation("test:wrong_amount", "llm_success", negative_control=True, expected_gate_failure="amount_consistency"),
    )
    assert graded["passed"] is True
    assert graded["negative_control_detected"] is True
    assert graded["hard_gates"]["amount_consistency"] is False
    assert any(item["type"] == "unknown_business_fact" for item in graded["number_check"]["violations"])


def test_invalid_schema_degrades(cases):
    case = cases["ad_spend_pressure"]
    oracle = _oracle(case)
    run = runner.execute_provider_run(case, oracle, FakeProvider(data={"summary": "缺少其余字段"}))
    graded = grade_run(
        case, oracle, run,
        RunExpectation("test:schema", "deterministic_fallback", expected_error_code="llm_schema_invalid", negative_control=True),
    )
    assert graded["passed"] is True
    assert graded["error"]["code"] == "llm_schema_invalid"
    assert graded["call_count"] == 1


def test_timeout_and_truncation_degrade(cases):
    case = cases["ad_spend_pressure"]
    oracle = _oracle(case)
    timeout = FakeProvider(
        error=LLMError("llm_timeout", "超时", retryable=True),
        latency_ms=20000.0, attempts=2, finish_reason=None,
    )
    run = runner.execute_provider_run(case, oracle, timeout)
    graded = grade_run(
        case, oracle, run,
        RunExpectation("test:timeout", "deterministic_fallback", expected_error_code="llm_timeout", negative_control=True),
    )
    assert graded["passed"] is True
    assert graded["attempts"] == 2
    assert graded["latency_ms"] == 20000.0

    truncated = FakeProvider(
        error=LLMError("llm_invalid_json", "模型输出达到 max_tokens 被截断，不是合法 JSON", details={"finish_reason": "length"}),
        latency_ms=1234.0, attempts=1, finish_reason=None,
    )
    run = runner.execute_provider_run(case, oracle, truncated)
    graded = grade_run(
        case, oracle, run,
        RunExpectation("test:truncated", "deterministic_fallback", expected_error_code="llm_invalid_json", negative_control=True),
    )
    assert graded["passed"] is True
    assert graded["finish_reason"] == "length"
    assert "截断" in graded["error"]["message"]


def test_no_key_and_fail_policy(cases):
    case = cases["ad_spend_pressure"]
    oracle = _oracle(case)
    run = runner.execute_provider_run(case, oracle, None)
    graded = grade_run(
        case, oracle, run,
        RunExpectation("test:no_key", "deterministic_fallback", expected_error_code="not_configured", expected_reason="not_configured", negative_control=True),
    )
    assert graded["passed"] is True
    assert graded["configured"] is False
    assert graded["call_count"] == 0
    assert graded["prompt_hash"]

    timeout = FakeProvider(error=LLMError("llm_timeout", "超时", retryable=True), attempts=2)
    run = runner.execute_provider_run(case, oracle, timeout, fallback_policy="fail")
    graded = grade_run(
        case, oracle, run,
        RunExpectation("test:fail_policy", "llm_failed", expected_error_code="llm_timeout", negative_control=True),
    )
    assert graded["passed"] is True
    assert graded["status"] == "llm_failed"


def test_number_audit_separates_facts_assumptions_and_non_amounts(cases):
    case = cases["ad_spend_pressure"]
    oracle = _oracle(case)
    payload = _payload(oracle)
    data = _valid(case, oracle)
    data["summary"] = (
        "净利润下降 26000 美分，广告支出为 450 美元，广告支出是上期的 4.5 倍。"
        "涉及 3 个 SKU，观察 30 天，置信度 0.72；预计节省 3840 美分。"
    )
    output = LLMAnalysisOutput.model_validate(data)
    result = audit_numbers(payload, output)
    assert result["passed"] is True, result["violations"]
    assert any(item["value"] == "3840" for item in result["assumptions"])
    assert any(item["value"] == "0.72" for item in result["non_amount"])
    assert any(item["value"] == "3" for item in result["non_amount"])
    assert any(item["value"] == "30" for item in result["non_amount"])


def test_number_audit_rejects_wrong_budget_percentage_and_unit(cases):
    case = cases["ad_spend_pressure"]
    oracle = _oracle(case)
    payload = _payload(oracle)

    wrong_pct = _valid(case, oracle)
    wrong_pct["summary"] = "建议将广告预算下调 37%，以改善利润。"
    result = audit_numbers(payload, LLMAnalysisOutput.model_validate(wrong_pct))
    assert result["passed"] is False
    assert any(item["type"] == "percentage_mismatch" for item in result["violations"])

    wrong_unit = _valid(case, oracle)
    wrong_unit["summary"] = "广告支出为 45000 美元，明显高于上期。"
    result = audit_numbers(payload, LLMAnalysisOutput.model_validate(wrong_unit))
    assert result["passed"] is False
    assert any(item["type"] == "unit_mismatch" for item in result["violations"])

    wrong_multiplier = _valid(case, oracle)
    wrong_multiplier["summary"] = "广告支出是上期的 3.5 倍。"
    result = audit_numbers(payload, LLMAnalysisOutput.model_validate(wrong_multiplier))
    assert result["passed"] is False
    assert any(item["type"] == "multiplier_mismatch" for item in result["violations"])

    # "履约" contains the character 约 but must not trigger approximate matching.
    fulfillment_word = _valid(case, oracle)
    fulfillment_word["summary"] = "履约记录显示广告支出变化 16.0%，需要复核。"
    result = audit_numbers(payload, LLMAnalysisOutput.model_validate(fulfillment_word))
    assert result["passed"] is False
    assert any(item["type"] == "percentage_mismatch" for item in result["violations"])


def test_action_gate_rejects_wrong_target_and_review_promise(cases):
    fulfillment = cases["fulfillment_pressure"]
    oracle = _oracle(fulfillment)
    payload = _payload(oracle)
    wrong_target = _valid(fulfillment, oracle)
    wrong_target["recommendations"][0]["action"] = "为 USB-HUB-03 创建成本核查任务"
    wrong_target["recommendations"][0]["rationale"] = "核对 USB-HUB-03 的履约账单"
    action = check_action(LLMAnalysisOutput.model_validate(wrong_target), payload)
    assert action["target_consistency"] is False

    promise = _valid(fulfillment, oracle)
    promise["expected_impact"]["direction"] = "improve"
    action = check_action(LLMAnalysisOutput.model_validate(promise), payload)
    assert action["impact_direction_consistency"] is False


def test_refund_review_caveat_and_action_percentage_are_accepted(cases):
    case = cases["refund_pressure"]
    oracle = _oracle(case)
    payload = _payload(oracle)
    data = _valid(case, oracle)
    data["expected_impact"] = {
        "direction": "improve",
        "horizon_days": 14,
        "explanation": "若审核定位并遏制退款，净利有望从 27000 美分回升；但幅度取决于退款成因，短期难以恢复到上期 44000 美分水平。",
    }
    data["missing_information"] = ["15% 调整幅度的对象（价格或预算）与审批阈值未在动作中明确"]
    output = LLMAnalysisOutput.model_validate(data)
    action = check_action(output, payload)
    assert action["impact_direction_consistency"] is True, action
    numbers = audit_numbers(payload, output)
    assert numbers["passed"] is True, numbers["violations"]

    # The same value outside an action context is not a known business fact.
    unrelated = _valid(case, oracle)
    unrelated["summary"] = "退款率为 15%，需要核查。"
    result = audit_numbers(payload, LLMAnalysisOutput.model_validate(unrelated))
    assert result["passed"] is False
    assert any(item["type"] == "percentage_mismatch" for item in result["violations"])


def test_review_task_accepts_review_caveats_without_budget_conflict(cases):
    case = cases["fulfillment_pressure"]
    oracle = _oracle(case)
    payload = _payload(oracle)

    data = _valid(case, oracle)
    data["expected_impact"] = {
        "direction": "improve",
        "horizon_days": 14,
        "explanation": "复核任务仅走人工审批流程，不调用真实广告或采购系统；若确认履约异常并落地调整，有望收窄履约成本缺口，但不改变当前已实现的 30500 美分利润。",
    }
    output = LLMAnalysisOutput.model_validate(data)
    action = check_action(output, payload)
    assert action["impact_direction_consistency"] is True, action

    question = _valid(case, oracle)
    question["missing_information"] = ["拟议日预算184未变化的原因及15%调整的作用对象"]
    result = audit_numbers(payload, LLMAnalysisOutput.model_validate(question))
    assert result["passed"] is True, result["violations"]


def test_budget_action_rejects_increase(cases):
    case = cases["ad_spend_pressure"]
    oracle = _oracle(case)
    payload = _payload(oracle)
    data = _valid(case, oracle)
    data["recommendations"][0]["action"] = "将 TRAVEL-BAG-01 的日广告预算提高 15%"
    action = check_action(LLMAnalysisOutput.model_validate(data), payload)
    assert action["action_type_consistency"] is False


def test_budget_down_wording_and_payload_counts_are_accepted(cases):
    case = cases["ad_spend_pressure"]
    oracle = _oracle(case)
    payload = _payload(oracle)
    data = _valid(case, oracle)
    data["recommendations"][0]["action"] = "对 TRAVEL-BAG-01 执行模拟降预算15%（日预算852→724美分）"
    data["root_causes"][0]["reasoning"] = "订单从120增至140，采购成本增加60.00美元、履约增加40.00美元、平台费增加10.00美元。"
    output = LLMAnalysisOutput.model_validate(data)
    action = check_action(output, payload)
    assert action["action_type_consistency"] is True, action
    numbers = audit_numbers(payload, output)
    assert numbers["passed"] is True, numbers["violations"]

    # A count value placed next to a money word is still not a count fact.
    wrong_count = _valid(case, oracle)
    wrong_count["root_causes"][0]["reasoning"] = "订单成本 120，采购成本增加60.00美元。"
    result = audit_numbers(payload, LLMAnalysisOutput.model_validate(wrong_count))
    assert result["passed"] is False
    assert any(item["type"] == "unknown_business_fact" for item in result["violations"])


def test_number_audit_accepts_action_parameter_reference(cases):
    case = cases["refund_pressure"]
    oracle = _oracle(case)
    payload = _payload(oracle)
    data = _valid(case, oracle)
    data["recommendations"][0]["rationale"] = (
        "计划动作为create_review_task且adjustment_pct=15；"
        "模拟复核可评估退款与成本影响，不调用真实广告或采购系统。"
    )
    result = audit_numbers(payload, LLMAnalysisOutput.model_validate(data))
    assert result["passed"] is True, result["violations"]


def test_forbidden_claims(cases):
    case = cases["ad_spend_pressure"]
    oracle = _oracle(case)
    data = _valid(case, oracle)
    data["summary"] = "保证利润提升，无需审批。"
    result = check_forbidden_claims(LLMAnalysisOutput.model_validate(data))
    assert result["passed"] is False
    assert {item["type"] for item in result["violations"]} >= {"guaranteed_profit", "approval_bypass"}

    safe = _valid(case, oracle)
    safe["summary"] = "不保证利润提升；这是模拟，不调用真实广告系统。"
    result = check_forbidden_claims(LLMAnalysisOutput.model_validate(safe))
    assert result["passed"] is True


def test_side_effect_guard_blocks_approval_and_execution():
    with pytest.raises(RuntimeError):
        with runner.side_effect_guard() as effects:
            service_module.decide(None, "rec-1", "approve", "", None, "req-1")
    assert effects["approval_calls"] == 1

    with pytest.raises(RuntimeError):
        with runner.side_effect_guard() as effects:
            service_module.execute_once(None, "rec-1", "key", None, "req-1")
    assert effects["execution_calls"] == 1


def test_real_mode_without_key_makes_no_call(monkeypatch, tmp_path):
    class NoKeySettings:
        llm_configured = False

    monkeypatch.setattr(runner, "Settings", lambda: NoKeySettings())
    report = runner.run_real(CASE_DIR, ["ad_spend_pressure"], report_path=tmp_path / "real.json")
    assert report["error"] == "not_configured"
    assert not (tmp_path / "real.json").exists()


def test_report_contains_required_real_call_fields(tmp_path):
    report = runner.run_offline(CASE_DIR, ["ad_spend_pressure"], tmp_path / "offline.json")
    normal = [run for run in report["runs"] if run["scenario"] == "offline:normal"][0]
    for field in (
        "case_id", "model", "prompt_hash", "model_params", "status", "latency_ms",
        "usage", "finish_reason", "hard_gates", "failure_reasons",
        "evidence_consistency", "number_check", "action_consistency", "side_effects",
    ):
        assert field in normal
    assert normal["prompt_hash"]
    assert normal["finish_reason"] == "stop"
    json.dumps(report, ensure_ascii=False)
