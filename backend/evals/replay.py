"""Offline recorded-response replay evaluation.

The recording is a fixed JSON file of provider responses. This runner feeds it
through the production native-tool loop, Pydantic output schema and evidence
validation, without a network call or a paid model. The checked-in recording is
synthetic (generated from deterministic eval fixtures), not a real transcript;
its purpose is regression coverage for the replay/parse/evidence boundary.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402
from app.llm import LLMError, LLMResult  # noqa: E402

from evals.graders import build_oracle, check_integrity, load_cases  # noqa: E402
from evals.native import NativeScenario, execute_native_run  # noqa: E402

CASE_DIR = BACKEND / "evals" / "llm_cases"
RECORDING = BACKEND / "evals" / "recordings" / "synthetic_native_v1.json"
REPORT_DIR = BACKEND / "evals" / "reports"


class ReplayProvider:
    provider_type = "replay"

    def __init__(self, turns: list[dict[str, Any]], model: str = "synthetic-replay-v1"):
        self.turns = list(turns)
        self.model = model
        self.calls = 0

    def chat(self, messages, *, tools=None, tool_choice=None, response_format=None, max_tokens=1200, temperature=0.2):
        if self.calls >= len(self.turns):
            raise LLMError("llm_empty_response", "replay 脚本已耗尽", retryable=False)
        turn = self.turns[self.calls]
        self.calls += 1
        return LLMResult(
            content=turn["content"],
            model=self.model,
            latency_ms=0.0,
            usage=turn.get("usage"),
            attempts=1,
            request_id=f"replay-{self.calls}",
            finish_reason=turn.get("finish_reason"),
        )


def run_case(case: dict[str, Any], recording_case: dict[str, Any], settings: Settings) -> dict[str, Any]:
    integrity = check_integrity(case)
    if integrity.raw is None:
        return {"case_id": case["case_id"], "passed": False, "error": "integrity_failed", "integrity": integrity.as_dict()}
    oracle = build_oracle(integrity.raw)
    expected_status = recording_case.get("expected_status")
    if expected_status != oracle.labels["llm_status"]:
        return {
            "case_id": case["case_id"], "passed": False, "error": "recording_oracle_mismatch",
            "expected": expected_status, "oracle": oracle.labels["llm_status"],
        }

    provider = ReplayProvider(recording_case.get("turns", []))
    scenario = NativeScenario(
        name="replay:final",
        expected_status=expected_status,
        expected_error_code=None,
        expected_reason=recording_case.get("expected_reason"),
        expected_tools=(),
        expect_termination="final_answer" if expected_status == "llm_success" else None,
    )
    run = execute_native_run(
        case, oracle, provider,
        settings=settings, factory=None, ledger_factory=None, scenario=scenario,
    )
    passed = run["status"] == expected_status
    checks: dict[str, Any] = {"status": passed}
    if expected_status == "llm_success":
        evidence = run.get("evidence_checks") or {}
        checks.update({
            "termination": run.get("termination_reason") == "final_answer",
            "output_present": run.get("output") is not None,
            "evidence_valid": bool(evidence.get("valid")),
            "primary_evidence_hit": bool(evidence.get("primary_hit")),
            "causes_have_evidence": bool(evidence.get("all_causes_have_evidence")),
            "recommendations_have_evidence": bool(evidence.get("all_recommendations_have_evidence")),
            "no_tool_calls": run.get("tool_calls") == [],
            "safety": bool((run.get("safety_checks") or {}).get("passed")),
            "provider_replay": run.get("provider_type") == "replay",
        })
        passed = passed and all(checks.values())
    else:
        checks["reason"] = run.get("reason") == recording_case.get("expected_reason")
        checks["no_provider_call"] = provider.calls == 0
        passed = passed and all(checks.values())

    return {
        "case_id": case["case_id"], "passed": passed, "checks": checks,
        "status": run["status"], "reason": run.get("reason"),
        "error": run.get("error"), "provider_calls": provider.calls,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ProfitOps AI offline LLM replay evaluation")
    parser.add_argument("--case-dir", type=Path, default=CASE_DIR)
    parser.add_argument("--recording", type=Path, default=RECORDING)
    parser.add_argument("--cases", nargs="*", default=None)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()

    recording = json.loads(args.recording.read_text(encoding="utf-8"))
    recorded = {item["case_id"]: item for item in recording.get("cases", [])}
    cases = load_cases(args.case_dir)
    if args.cases:
        wanted = set(args.cases)
        cases = [case for case in cases if case["case_id"] in wanted]
        missing = wanted - {case["case_id"] for case in cases}
        if missing:
            raise SystemExit(f"unknown cases: {', '.join(sorted(missing))}")

    settings = Settings(
        _env_file=None, llm_api_key="", llm_native_tools=True,
        llm_fallback_policy="rules",
    )
    results = []
    for case in cases:
        item = recorded.get(case["case_id"])
        if item is None:
            results.append({"case_id": case["case_id"], "passed": False, "error": "missing_recording"})
            continue
        results.append(run_case(case, item, settings))

    passed = bool(results) and all(item["passed"] for item in results)
    report = {
        "suite": "profitops-llm-replay-v1",
        "recording_id": recording.get("recording_id"),
        "source_type": recording.get("source_type"),
        "network_calls": 0,
        "passed": passed,
        "cases": results,
    }
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "suite": report["suite"], "recording_id": report["recording_id"],
        "passed": passed, "cases": len(results),
        "failed": [item["case_id"] for item in results if not item["passed"]],
        "network_calls": 0,
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
