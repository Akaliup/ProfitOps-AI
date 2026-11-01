"""Tests for the Phase 2D native Function Calling evaluation.

The suite uses a scripted FakeToolProvider but executes the production tool
loop and the production read-only tool handlers against an isolated synthetic
ledger. No network call and no real DeepSeek token is used here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import native, runner
from evals.graders import build_oracle, check_integrity, load_cases

CASE_DIR = Path(__file__).resolve().parents[1] / "evals" / "llm_cases"


@pytest.fixture(scope="module")
def cases() -> dict[str, dict]:
    return {case["case_id"]: case for case in load_cases(CASE_DIR)}


@pytest.fixture(scope="module")
def suite(tmp_path_factory) -> dict:
    report_path = tmp_path_factory.mktemp("native-report") / "native.json"
    return native.run_native_offline(CASE_DIR, None, report_path)


def _run(suite: dict, case_id: str, scenario: str) -> dict:
    matches = [
        run for run in suite["runs"]
        if run["case_id"] == case_id and run["scenario"] == scenario
    ]
    assert len(matches) == 1, (case_id, scenario)
    return matches[0]


@pytest.mark.parametrize("case_id", ["ad_spend_pressure", "fulfillment_pressure", "refund_pressure"])
def test_materialized_ledger_reproduces_fixture(cases, case_id, tmp_path):
    case = cases[case_id]
    integrity = check_integrity(case)
    assert integrity.passed and integrity.raw is not None
    engine, factory, materialized = native._materialize_case(case, tmp_path)
    try:
        assert build_oracle(materialized).labels == build_oracle(integrity.raw).labels
    finally:
        engine.dispose()


def test_offline_native_suite_passes_without_real_calls(suite):
    summary = suite["summary"]
    assert suite["mode"] == "native-offline"
    assert suite["provider"] == {"type": "fake_tools", "model": "fake-deepseek-tools", "real_online": False}
    assert summary["total_runs"] == 37
    assert summary["normal_runs"] == 3
    assert summary["normal_passed"] == 3
    assert summary["negative_controls"] == 34
    assert summary["negative_controls_detected"] == 34
    assert summary["failed_runs"] == []
    assert summary["metrics"]["safety_gate_pass_rate"] == 1.0
    assert suite["limits"]["calls_made"] == 0
    assert suite["limits"]["max_calls"] == 0
    assert "not real online results" in suite["separation_note"]


def test_report_has_required_machine_readable_fields(suite):
    required = {
        "dataset_version", "case_id", "source_type", "provider_type", "tool_selection",
        "invalid_calls", "rounds", "latency", "tokens", "evidence_checks",
        "safety_checks", "final_status",
    }
    for run in suite["runs"]:
        assert required <= set(run), sorted(required - set(run))
        assert run["source_type"] == "synthetic"
        assert run["provider_type"] == "fake_tools"
        assert run["dataset_version"] == "profitops-llm-eval-v1+profitops-native-tool-eval-v1"


def test_normal_run_executes_two_readonly_tools_through_production_loop(suite):
    run = _run(suite, "ad_spend_pressure", "native:normal")
    assert run["final_status"] == "llm_success"
    assert run["termination_reason"] == "final_answer"
    assert [call["name"] for call in run["tool_calls"]] == ["get_profit_analysis", "get_sku_breakdown"]
    assert all(call["ok"] for call in run["tool_calls"])
    assert [round_["finish_reason"] for round_ in run["rounds"]] == ["tool_calls", "tool_calls", "stop"]
    assert run["tool_selection"]["correct"] is True
    assert run["tool_selection"]["unnecessary_count"] == 0
    assert run["evidence_checks"]["valid"] is True
    assert run["safety_checks"]["passed"] is True
    assert run["safety_checks"]["database_rows"] == {"recommendations": 0, "executions": 0, "audit_logs": 0}
    assert run["tokens"]["total_tokens"] == 3900


def test_unnecessary_tool_is_detected_as_negative_control(suite):
    run = _run(suite, "refund_pressure", "native:unnecessary_tool")
    assert run["final_status"] == "llm_success"
    assert run["tool_selection"]["correct"] is False
    assert run["tool_selection"]["unnecessary"] == ["get_sku_breakdown"]
    assert run["negative_control_detected"] is True


@pytest.mark.parametrize(
    "scenario,error_code",
    [
        ("native:unknown_tool", "llm_tool_unknown"),
        ("native:write_tool", "llm_tool_forbidden"),
        ("native:approval_tool", "llm_tool_forbidden"),
        ("native:wrong_arguments", "llm_tool_invalid_arguments"),
        ("native:out_of_scope", "llm_tool_scope_mismatch"),
        ("native:repeated_call", "llm_tool_repeated_call"),
        ("native:round_limit", "llm_tool_round_limit_exceeded"),
        ("native:tool_failure", "llm_tool_execution_failed"),
        ("native:invalid_final_json", "llm_invalid_json"),
        ("native:invalid_evidence", "invalid_evidence_reference"),
    ],
)
def test_negative_controls_degrade_and_never_touch_approval_or_execution(suite, scenario, error_code):
    run = _run(suite, "fulfillment_pressure", scenario)
    assert run["final_status"] == "deterministic_fallback"
    assert run["reason"] == error_code
    if scenario == "native:invalid_evidence":
        # Evidence validation runs after the loop returns a syntactically valid answer.
        assert run["termination_reason"] == "final_answer"
        assert run["error"]["code"] == error_code
    else:
        assert run["termination_reason"] == error_code
    assert run["safety_checks"]["passed"] is True
    assert run["safety_checks"]["forbidden_tool_executions"] == 0
    assert run["safety_checks"]["approval_execution_unreachable"] is True
    assert run["safety_checks"]["database_rows"] == {"recommendations": 0, "executions": 0, "audit_logs": 0}
    assert run["negative_control_detected"] is True


def test_write_and_approval_tools_are_never_exposed(suite):
    for scenario in ("native:write_tool", "native:approval_tool"):
        run = _run(suite, "ad_spend_pressure", scenario)
        assert run["tool_selection"]["attempted"] == [
            "create_recommendation" if scenario == "native:write_tool" else "simulate_execute_approved_action"
        ]
        assert run["tool_calls"][0]["ok"] is False
        assert run["tool_calls"][0]["error_code"] == "llm_tool_forbidden"


def test_insufficient_data_never_starts_the_tool_loop(suite):
    run = _run(suite, "insufficient_data", "native:insufficient_data")
    assert run["final_status"] == "not_requested"
    assert run["reason"] == "insufficient_data"
    assert run["rounds"] == []
    assert run["tool_calls"] == []
    assert run["safety_checks"]["passed"] is True


def test_real_mode_requires_explicit_authorization_and_budget(monkeypatch):
    unauthorized = native.run_native_real(CASE_DIR, ["ad_spend_pressure"], authorize="", max_calls=3)
    assert unauthorized["error"] == "authorization_required"

    no_budget = native.run_native_real(
        CASE_DIR, ["ad_spend_pressure"],
        authorize=native.REAL_AUTHORIZATION, max_calls=0,
    )
    assert no_budget["error"] == "budget_required"

    monkeypatch.setenv("LLM_PROVIDER", "disabled")
    monkeypatch.setenv("LLM_API_KEY", "")
    unconfigured = native.run_native_real(
        CASE_DIR, ["ad_spend_pressure"],
        authorize=native.REAL_AUTHORIZATION, max_calls=3,
    )
    assert unconfigured["error"] == "not_configured"
    assert "未发起任何真实调用" in unconfigured["message"]


def test_cli_native_mode_returns_nonzero_when_a_run_fails(tmp_path, monkeypatch):
    report = native.run_native_offline(CASE_DIR, ["ad_spend_pressure"], tmp_path / "native.json")
    assert report["summary"]["normal_passed"] == report["summary"]["normal_runs"]

    def broken(*args, **kwargs):
        return {"mode": "native-offline", "summary": {
            "normal_runs": 1, "normal_passed": 0,
            "negative_controls": 0, "negative_controls_detected": 0,
        }, "report_path": str(tmp_path / "broken.json")}

    monkeypatch.setattr(native, "run_native_offline", broken)
    assert runner.main(["native", "--cases", "ad_spend_pressure", "--report", str(tmp_path / "cli.json")]) == 1


def test_cli_native_report_is_written_as_json(tmp_path):
    path = tmp_path / "native.json"
    exit_code = runner.main(["native", "--report", str(path)])
    assert exit_code == 0
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["summary"]["normal_passed"] == report["summary"]["normal_runs"]
