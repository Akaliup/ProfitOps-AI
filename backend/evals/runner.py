"""Evaluation runner for ProfitOps AI.

Modes:
  offline  deterministic FakeProvider scenarios; never touches the network
  real     explicit DeepSeek pilot with a hard call budget

Run from the project root:
  .venv/Scripts/python.exe backend/evals/runner.py offline
  .venv/Scripts/python.exe backend/evals/runner.py real --trials 1 --max-calls 3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from pydantic import ValidationError  # noqa: E402

from app import services as service_module  # noqa: E402
from app.config import Settings  # noqa: E402
from app.llm import DeepSeekProvider, LLMError  # noqa: E402
from app.llm_analysis import (  # noqa: E402
    SYSTEM_PROMPT, build_llm_payload, build_user_prompt, validate_evidence_references,
)
from app.schemas import LLMAnalysisOutput  # noqa: E402

from evals.graders import (  # noqa: E402
    ExpectedResult, IntegrityResult, OracleResult, RunExpectation, build_fake_output,
    build_oracle, check_expected, check_integrity, grade_run, load_cases,
)
from evals.providers import FakeProvider, provider_label  # noqa: E402

CASE_DIR = BACKEND / "evals" / "llm_cases"
REPORT_DIR = BACKEND / "evals" / "reports"
ZERO_EFFECTS = {
    "approval_calls": 0,
    "execution_calls": 0,
    "recommendation_calls": 0,
    "business_external_calls": 0,
    "database_writes": 0,
    "llm_calls": 0,
}


class BudgetExceeded(RuntimeError):
    pass


@contextmanager
def side_effect_guard():
    """Fail closed if analysis accidentally reaches an approval/execution service."""
    counts = dict(ZERO_EFFECTS)

    def guarded(name: str):
        def wrapper(*args, **kwargs):
            if name == "decide":
                counts["approval_calls"] += 1
            elif name in {"execute_once", "simulate_action"}:
                counts["execution_calls"] += 1
            elif name == "create_recommendation":
                counts["recommendation_calls"] += 1
            raise RuntimeError(f"评测期间禁止调用生产副作用函数：{name}")
        return wrapper

    patches = [
        patch.object(service_module, name, guarded(name))
        for name in ("decide", "execute_once", "simulate_action", "create_recommendation")
        if hasattr(service_module, name)
    ]
    for item in patches:
        item.start()
    try:
        yield counts
    finally:
        for item in patches:
            item.stop()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _truncate(text: str | None, limit: int = 12000) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


def _base_run(
    *, status: str, reason: str | None, configured: bool, provider: Any,
    prompt_hash: str | None, model_params: dict[str, Any] | None,
    latency_ms: float | None, attempts: int, usage: dict[str, int] | None,
    finish_reason: str | None, raw_content: str | None,
    output: dict[str, Any] | None, rejected_output: dict[str, Any] | None,
    error: dict[str, Any] | None, call_count: int, effects: dict[str, int],
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "configured": configured,
        "provider_type": provider_label(provider),
        "model": str(getattr(provider, "model", "none")),
        "prompt_hash": prompt_hash,
        "model_params": model_params,
        "latency_ms": latency_ms,
        "attempts": attempts,
        "usage": usage,
        "finish_reason": finish_reason,
        "raw_content": _truncate(raw_content),
        "output": output,
        "rejected_output": rejected_output,
        "error": error,
        "call_count": call_count,
        "side_effects": effects,
        "payload": payload,
    }


def _error_dict(exc: LLMError) -> dict[str, Any]:
    return {
        "code": exc.code,
        "message": exc.message,
        "retryable": exc.retryable,
        "status_code": exc.status_code,
        "details": exc.details,
    }


def execute_provider_run(
    case: dict[str, Any],
    oracle: OracleResult,
    provider: Any,
    *,
    fallback_policy: str = "rules",
    max_tokens: int = 4000,
) -> dict[str, Any]:
    if oracle.error_code == "insufficient_data":
        return _base_run(
            status="not_requested", reason="insufficient_data", configured=provider is not None,
            provider=provider, prompt_hash=None, model_params=None, latency_ms=None, attempts=0,
            usage=None, finish_reason=None, raw_content=None, output=None, rejected_output=None,
            error={"code": "insufficient_data", "message": oracle.error_message or "数据不完整，跳过 LLM"},
            call_count=0, effects=dict(ZERO_EFFECTS), payload=None,
        )
    if not oracle.findings.anomaly:
        return _base_run(
            status="not_requested", reason="no_anomaly", configured=provider is not None,
            provider=provider, prompt_hash=None, model_params=None, latency_ms=None, attempts=0,
            usage=None, finish_reason=None, raw_content=None, output=None, rejected_output=None,
            error=None, call_count=0, effects=dict(ZERO_EFFECTS), payload=None,
        )
    if oracle.error_code is not None:
        return _base_run(
            status="not_requested", reason=oracle.error_code, configured=provider is not None,
            provider=provider, prompt_hash=None, model_params=None, latency_ms=None, attempts=0,
            usage=None, finish_reason=None, raw_content=None, output=None, rejected_output=None,
            error={"code": oracle.error_code, "message": oracle.error_message or ""},
            call_count=0, effects=dict(ZERO_EFFECTS), payload=None,
        )

    payload = build_llm_payload(oracle.report, oracle.findings, oracle.action_plan.action.model_dump(mode="json"))
    user_prompt = build_user_prompt(payload)
    prompt_hash = _sha256(f"{SYSTEM_PROMPT}\n{user_prompt}")
    model_params = {"temperature": 0.2, "max_tokens": max_tokens}
    if provider is None:
        return _base_run(
            status="deterministic_fallback", reason="not_configured", configured=False,
            provider=None, prompt_hash=prompt_hash, model_params=model_params, latency_ms=None,
            attempts=0, usage=None, finish_reason=None, raw_content=None, output=None,
            rejected_output=None, error={"code": "not_configured", "message": "未配置 LLM_API_KEY"},
            call_count=0, effects=dict(ZERO_EFFECTS), payload=payload,
        )

    fallback_status = "llm_failed" if fallback_policy == "fail" else "deterministic_fallback"
    with side_effect_guard() as effects:
        try:
            result = provider.generate_json(system=SYSTEM_PROMPT, user=user_prompt, max_tokens=max_tokens)
        except LLMError as exc:
            effects["llm_calls"] = 1
            return _base_run(
                status=fallback_status, reason=exc.code, configured=True, provider=provider,
                prompt_hash=prompt_hash, model_params=model_params,
                latency_ms=getattr(exc, "latency_ms", None), attempts=getattr(exc, "attempts", 0),
                usage=None, finish_reason=((exc.details or {}).get("finish_reason") if isinstance(exc.details, dict) else None),
                raw_content=None, output=None, rejected_output=None, error=_error_dict(exc),
                call_count=1, effects=effects, payload=payload,
            )
        except Exception as exc:  # defensive: a provider bug must not look like success
            effects["llm_calls"] = 1
            error = LLMError("llm_unknown_error", f"Provider 异常：{type(exc).__name__}: {exc}")
            return _base_run(
                status=fallback_status, reason=error.code, configured=True, provider=provider,
                prompt_hash=prompt_hash, model_params=model_params, latency_ms=None, attempts=0,
                usage=None, finish_reason=None, raw_content=None, output=None, rejected_output=None,
                error=_error_dict(error), call_count=1, effects=effects, payload=payload,
            )
        effects["llm_calls"] = 1
        try:
            output = LLMAnalysisOutput.model_validate(result.data)
        except ValidationError as exc:
            error = {
                "code": "llm_schema_invalid",
                "message": "LLM 输出不符合 LLMAnalysisOutput Schema",
                "retryable": False,
                "details": {"errors": exc.errors(include_url=False, include_context=False)},
            }
            return _base_run(
                status=fallback_status, reason=error["code"], configured=True, provider=provider,
                prompt_hash=prompt_hash, model_params=model_params, latency_ms=result.meta.latency_ms,
                attempts=result.meta.attempts, usage=result.meta.usage, finish_reason=result.meta.finish_reason,
                raw_content=result.meta.content, output=None, rejected_output=None, error=error,
                call_count=1, effects=effects, payload=payload,
            )
        unknown = validate_evidence_references(output, oracle.findings.evidence)
        if unknown:
            error = {
                "code": "invalid_evidence_reference",
                "message": f"LLM 引用了不存在的证据：{', '.join(unknown[:5])}",
                "retryable": False,
                "details": {"unknown_evidence_ids": unknown},
            }
            return _base_run(
                status=fallback_status, reason=error["code"], configured=True, provider=provider,
                prompt_hash=prompt_hash, model_params=model_params, latency_ms=result.meta.latency_ms,
                attempts=result.meta.attempts, usage=result.meta.usage, finish_reason=result.meta.finish_reason,
                raw_content=result.meta.content, output=None,
                rejected_output=output.model_dump(mode="json"), error=error,
                call_count=1, effects=effects, payload=payload,
            )
        return _base_run(
            status="llm_success", reason=None, configured=True, provider=provider,
            prompt_hash=prompt_hash, model_params=model_params, latency_ms=result.meta.latency_ms,
            attempts=result.meta.attempts, usage=result.meta.usage, finish_reason=result.meta.finish_reason,
            raw_content=result.meta.content, output=output.model_dump(mode="json"),
            rejected_output=None, error=None, call_count=1, effects=effects, payload=payload,
        )


def _load_manifest(case_dir: Path) -> dict[str, Any]:
    path = case_dir / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _select_cases(cases: list[dict[str, Any]], names: list[str] | None) -> list[dict[str, Any]]:
    if not names:
        return cases
    wanted = set(names)
    selected = [case for case in cases if case["case_id"] in wanted]
    missing = wanted - {case["case_id"] for case in selected}
    if missing:
        raise SystemExit(f"未知案例：{', '.join(sorted(missing))}")
    return selected


def _prepare(case_dir: Path, names: list[str] | None) -> tuple[
    list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[OracleResult | None]
]:
    cases = _select_cases(load_cases(case_dir), names)
    integrity: list[dict[str, Any]] = []
    expected_labels: list[dict[str, Any]] = []
    oracles: list[OracleResult | None] = []
    for case in cases:
        result = check_integrity(case)
        integrity.append({"case_id": case["case_id"], **result.as_dict()})
        if result.raw is None:
            oracles.append(None)
            continue
        oracle = build_oracle(result.raw)
        oracles.append(oracle)
        expected = check_expected(case, oracle)
        expected_labels.append({"case_id": case["case_id"], **expected.as_dict()})
    return cases, _load_manifest(case_dir), integrity, expected_labels, oracles


def _stats(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "mean": round(sum(values) / len(values), 3),
    }


def _usage_totals(runs: list[dict[str, Any]]) -> dict[str, int] | None:
    totals: Counter[str] = Counter()
    for run in runs:
        usage = run.get("usage") or {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"):
            if isinstance(usage.get(key), int):
                totals[key] += usage[key]
    return dict(totals) if totals else None


def _gate_rates(runs: list[dict[str, Any]]) -> dict[str, float | None]:
    content = [run for run in runs if run.get("status") == "llm_success" and run.get("output")]
    names = sorted({name for run in content for name in run.get("hard_gates", {})})
    return {
        name: (sum(1 for run in content if run["hard_gates"].get(name) is True) / len(content) if content else None)
        for name in names
    }


def _case_summary(runs: list[dict[str, Any]], *, stability_allowed: bool) -> dict[str, Any]:
    normal = [run for run in runs if not (run.get("expected") or {}).get("negative_control")]
    content = [run for run in normal if run.get("status") == "llm_success" and run.get("output")]
    passed = sum(1 for run in normal if run.get("passed"))
    result: dict[str, Any] = {
        "runs": len(normal),
        "negative_controls": len(runs) - len(normal),
        "passed": passed,
        "failed": len(normal) - passed,
        "status_counts": dict(Counter(run.get("status") for run in normal)),
        "hard_gate_rates": _gate_rates(normal),
        "evidence_reference_valid_rate": (
            sum(1 for run in content if run["evidence_consistency"].get("valid")) / len(content) if content else None
        ),
        "primary_driver_hit_rate": (
            sum(1 for run in content if run["evidence_consistency"].get("primary_hit")) / len(content) if content else None
        ),
        "amount_consistency_rate": (
            sum(1 for run in content if run["number_check"].get("passed")) / len(content) if content else None
        ),
        "latency_ms": _stats([run["latency_ms"] for run in normal if isinstance(run.get("latency_ms"), (int, float))]),
        "usage": _usage_totals(normal),
        "stability_reported": bool(stability_allowed and len(normal) >= 3),
    }
    if result["stability_reported"]:
        result["pass_rate"] = passed / len(normal)
        result["pass_at_1"] = bool(normal[0].get("passed"))
        result["pass_pow_k"] = all(run.get("passed") for run in normal)
    return result


def _summary(runs: list[dict[str, Any]], *, mode: str, fake_calls: int, real_calls: int) -> dict[str, Any]:
    normal = [run for run in runs if not (run.get("expected") or {}).get("negative_control")]
    negative = [run for run in runs if (run.get("expected") or {}).get("negative_control")]
    content = [run for run in normal if run.get("status") == "llm_success" and run.get("output")]
    by_case: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_case.setdefault(str(run.get("case_id")), []).append(run)
    return {
        "mode": mode,
        "total_runs": len(runs),
        "normal_runs": len(normal),
        "normal_passed": sum(1 for run in normal if run.get("passed")),
        "negative_controls": len(negative),
        "negative_controls_detected": sum(1 for run in negative if run.get("negative_control_detected") is True),
        "status_counts": dict(Counter(run.get("status") for run in runs)),
        "hard_gate_rates": _gate_rates(normal),
        "evidence_reference_valid_rate": (
            sum(1 for run in content if run["evidence_consistency"].get("valid")) / len(content) if content else None
        ),
        "primary_driver_hit_rate": (
            sum(1 for run in content if run["evidence_consistency"].get("primary_hit")) / len(content) if content else None
        ),
        "amount_consistency_rate": (
            sum(1 for run in content if run["number_check"].get("passed")) / len(content) if content else None
        ),
        "latency_ms": _stats([run["latency_ms"] for run in runs if isinstance(run.get("latency_ms"), (int, float))]),
        "usage": _usage_totals(runs),
        "fake_provider_calls": fake_calls,
        "real_online_calls": real_calls,
        "failed_runs": [
            {
                "case_id": run.get("case_id"),
                "scenario": run.get("scenario"),
                "status": run.get("status"),
                "failure_reasons": run.get("failure_reasons"),
            }
            for run in runs if not run.get("passed")
        ],
        "cases": {
            case_id: _case_summary(items, stability_allowed=(mode == "real"))
            for case_id, items in by_case.items()
        },
    }


def _report(
    *, mode: str, case_dir: Path, manifest: dict[str, Any], cases: list[dict[str, Any]],
    integrity: list[dict[str, Any]], expected_labels: list[dict[str, Any]],
    runs: list[dict[str, Any]], limits: dict[str, Any], provider: dict[str, Any],
) -> dict[str, Any]:
    fake_calls = sum(run.get("call_count", 0) for run in runs if run.get("provider_type") == "fake")
    real_calls = sum(run.get("call_count", 0) for run in runs if run.get("provider_type") == "deepseek")
    return {
        "report_version": "1",
        "generated_at": _now(),
        "mode": mode,
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
        "runs": runs,
        "summary": _summary(runs, mode=mode, fake_calls=fake_calls, real_calls=real_calls),
        "separation_note": (
            "FakeProvider runs are offline and are not real online results. "
            "Only runs with provider_type=deepseek and call_count=1 are real DeepSeek calls."
        ),
    }


def _write_report(report: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def run_offline(case_dir: Path = CASE_DIR, names: list[str] | None = None, report_path: Path | None = None) -> dict[str, Any]:
    cases, manifest, integrity, expected_labels, oracles = _prepare(case_dir, names)
    oracle_by_case = {case["case_id"]: oracle for case, oracle in zip(cases, oracles) if oracle is not None}
    runs: list[dict[str, Any]] = []
    for case in cases:
        oracle = oracle_by_case.get(case["case_id"])
        if oracle is None:
            continue
        if case["case_id"] == "insufficient_data":
            expectation = RunExpectation(
                "offline:normal", "not_requested",
                expected_error_code="insufficient_data", expected_reason="insufficient_data",
            )
            run = execute_provider_run(case, oracle, None)
            runs.append(grade_run(case, oracle, run, expectation))
            continue
        valid = build_fake_output(case, oracle)
        normal_provider = FakeProvider(data=valid)
        expectation = RunExpectation("offline:normal", "llm_success")
        runs.append(grade_run(case, oracle, execute_provider_run(case, oracle, normal_provider), expectation))

        invalid_evidence = json.loads(json.dumps(valid, ensure_ascii=False))
        invalid_evidence["evidence_references"] = ["ev-fabricated"]
        invalid_provider = FakeProvider(data=invalid_evidence)
        runs.append(grade_run(
            case, oracle, execute_provider_run(case, oracle, invalid_provider),
            RunExpectation("offline:invalid_evidence", "deterministic_fallback", expected_error_code="invalid_evidence_reference", negative_control=True),
        ))

        wrong_amount = json.loads(json.dumps(valid, ensure_ascii=False))
        label = oracle.findings.primary_driver.label if oracle.findings.primary_driver else "成本"
        wrong_amount["summary"] = f"{label}异常，净利润下降 999999 美分。"
        wrong_provider = FakeProvider(data=wrong_amount)
        runs.append(grade_run(
            case, oracle, execute_provider_run(case, oracle, wrong_provider),
            RunExpectation("offline:wrong_amount", "llm_success", negative_control=True, expected_gate_failure="amount_consistency"),
        ))

        invalid_schema_provider = FakeProvider(data={"summary": "缺少其余字段"})
        runs.append(grade_run(
            case, oracle, execute_provider_run(case, oracle, invalid_schema_provider),
            RunExpectation("offline:invalid_schema", "deterministic_fallback", expected_error_code="llm_schema_invalid", negative_control=True),
        ))

        timeout_provider = FakeProvider(
            error=LLMError("llm_timeout", "DeepSeek 请求超时", retryable=True),
            latency_ms=20000.0, attempts=2, finish_reason=None,
        )
        runs.append(grade_run(
            case, oracle, execute_provider_run(case, oracle, timeout_provider),
            RunExpectation("offline:timeout", "deterministic_fallback", expected_error_code="llm_timeout", negative_control=True),
        ))

        truncated_provider = FakeProvider(
            error=LLMError(
                "llm_invalid_json", "模型输出达到 max_tokens 被截断，不是合法 JSON",
                retryable=False, details={"finish_reason": "length"},
            ),
            latency_ms=1234.0, attempts=1, finish_reason=None,
        )
        runs.append(grade_run(
            case, oracle, execute_provider_run(case, oracle, truncated_provider),
            RunExpectation("offline:truncated", "deterministic_fallback", expected_error_code="llm_invalid_json", negative_control=True),
        ))

        runs.append(grade_run(
            case, oracle, execute_provider_run(case, oracle, None),
            RunExpectation("offline:no_key", "deterministic_fallback", expected_error_code="not_configured", expected_reason="not_configured", negative_control=True),
        ))

        runs.append(grade_run(
            case, oracle, execute_provider_run(case, oracle, timeout_provider, fallback_policy="fail"),
            RunExpectation("offline:fail_policy", "llm_failed", expected_error_code="llm_timeout", negative_control=True),
        ))

    report = _report(
        mode="offline", case_dir=case_dir, manifest=manifest, cases=cases,
        integrity=integrity, expected_labels=expected_labels, runs=runs,
        limits={"max_calls": 0, "calls_made": 0, "note": "offline FakeProvider only; no network"},
        provider={"type": "fake", "model": "fake-deepseek-flash", "real_online": False},
    )
    path = report_path or REPORT_DIR / f"offline-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    _write_report(report, path)
    report["report_path"] = str(path)
    return report


def run_real(
    case_dir: Path = CASE_DIR,
    names: list[str] | None = None,
    *,
    trials: int = 1,
    max_calls: int | None = None,
    fallback_policy: str | None = None,
    timeout: float | None = None,
    max_retries: int = 0,
    report_path: Path | None = None,
) -> dict[str, Any]:
    settings = Settings()
    if not settings.llm_configured:
        return {"mode": "real", "error": "not_configured", "message": "未配置 LLM_API_KEY，未发起任何真实调用"}
    cases, manifest, integrity, expected_labels, oracles = _prepare(case_dir, names)
    oracle_by_case = {case["case_id"]: oracle for case, oracle in zip(cases, oracles) if oracle is not None}
    policy = fallback_policy or settings.llm_fallback_policy
    budget = max_calls if max_calls is not None else 3 * max(1, trials)
    provider = DeepSeekProvider(
        api_key=settings.llm_api_key.get_secret_value().strip(),
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        timeout=timeout if timeout is not None else settings.llm_timeout,
        max_retries=max(0, min(3, max_retries)),
    )
    runs: list[dict[str, Any]] = []
    calls = 0
    for case in cases:
        oracle = oracle_by_case.get(case["case_id"])
        if oracle is None:
            continue
        is_guardrail = case["case_id"] == "insufficient_data"
        case_trials = 1 if is_guardrail else max(1, trials)
        for trial in range(1, case_trials + 1):
            if not is_guardrail and calls >= budget:
                runs.append({
                    "case_id": case["case_id"], "trial": trial, "scenario": f"real:trial-{trial}",
                    "status": "not_requested", "reason": "budget_exhausted", "passed": False,
                    "failure_reasons": ["budget_exhausted"], "provider_type": "deepseek",
                    "model": settings.llm_model, "call_count": 0, "side_effects": dict(ZERO_EFFECTS),
                })
                continue
            expectation = (
                RunExpectation("real:guardrail", "not_requested", expected_error_code="insufficient_data", expected_reason="insufficient_data")
                if is_guardrail
                else RunExpectation(f"real:trial-{trial}", "llm_success")
            )
            run = execute_provider_run(
                case, oracle, provider, fallback_policy=policy, max_tokens=settings.llm_max_tokens
            )
            calls += int(run.get("call_count", 0))
            graded = grade_run(case, oracle, run, expectation)
            graded["trial"] = trial
            graded["real_online"] = run.get("call_count", 0) == 1 and run.get("provider_type") == "deepseek"
            runs.append(graded)
    report = _report(
        mode="real", case_dir=case_dir, manifest=manifest, cases=cases,
        integrity=integrity, expected_labels=expected_labels, runs=runs,
        limits={
            "trials": trials, "max_calls": budget, "calls_made": calls,
            "fallback_policy": policy, "timeout": timeout if timeout is not None else settings.llm_timeout,
            "max_retries": max(0, min(3, max_retries)), "max_tokens": settings.llm_max_tokens,
        },
        provider={"type": "deepseek", "model": settings.llm_model, "base_url": settings.llm_base_url, "real_online": True},
    )
    path = report_path or REPORT_DIR / f"real-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    _write_report(report, path)
    report["report_path"] = str(path)
    return report


def regrade_report(report_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    """Re-evaluate saved outputs with the current graders; never calls a provider."""
    source = json.loads(report_path.read_text(encoding="utf-8"))
    case_dir = Path((source.get("dataset") or {}).get("case_dir") or CASE_DIR)
    cases = {case["case_id"]: case for case in load_cases(case_dir)}
    oracles: dict[str, OracleResult] = {}
    for case_id, case in cases.items():
        integrity = check_integrity(case)
        if integrity.raw is not None:
            oracles[case_id] = build_oracle(integrity.raw)
    graded_runs: list[dict[str, Any]] = []
    for saved in source.get("runs") or []:
        case = cases.get(saved.get("case_id"))
        oracle = oracles.get(saved.get("case_id"))
        if case is None or oracle is None:
            continue
        run = dict(saved)
        if run.get("provider_type") == "deepseekprovider":
            run["provider_type"] = "deepseek"
        expected = run.get("expected") or {}
        expectation = RunExpectation(
            str(run.get("scenario") or "regrade"),
            str(expected.get("status") or run.get("status")),
            expected_error_code=expected.get("error_code"),
            expected_reason=expected.get("reason"),
            negative_control=bool(expected.get("negative_control")),
            expected_gate_failure=expected.get("expected_gate_failure"),
        )
        graded = grade_run(case, oracle, run, expectation)
        for key in ("trial", "real_online"):
            if key in run:
                graded[key] = run[key]
        graded_runs.append(graded)
    fake_calls = sum(run.get("call_count", 0) for run in graded_runs if run.get("provider_type") == "fake")
    real_calls = sum(run.get("call_count", 0) for run in graded_runs if run.get("provider_type") == "deepseek")
    source["runs"] = graded_runs
    source["summary"] = _summary(
        graded_runs, mode=str(source.get("mode") or "regrade"),
        fake_calls=fake_calls, real_calls=real_calls,
    )
    source["regraded_at"] = _now()
    source["regrade_note"] = "Re-evaluated saved outputs with the current graders; no new LLM calls were made."
    path = output_path or report_path.with_name(report_path.stem + "-regraded.json")
    _write_report(source, path)
    source["report_path"] = str(path)
    return source


def _print_summary(report: dict[str, Any]):
    summary = report.get("summary") or {}
    print(json.dumps({
        "mode": report.get("mode"),
        "report_path": report.get("report_path"),
        "total_runs": summary.get("total_runs"),
        "normal_runs": summary.get("normal_runs"),
        "normal_passed": summary.get("normal_passed"),
        "negative_controls": summary.get("negative_controls"),
        "negative_controls_detected": summary.get("negative_controls_detected"),
        "real_online_calls": summary.get("real_online_calls"),
        "fake_provider_calls": summary.get("fake_provider_calls"),
        "status_counts": summary.get("status_counts"),
        "failed_runs": summary.get("failed_runs"),
    }, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ProfitOps AI LLM evaluation runner")
    parser.add_argument("--case-dir", type=Path, default=CASE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    sub = parser.add_subparsers(dest="mode", required=True)
    offline = sub.add_parser("offline", help="FakeProvider offline suite")
    offline.add_argument("--cases", nargs="*", default=None)
    offline.add_argument("--report", type=Path, default=None)
    real = sub.add_parser("real", help="real DeepSeek pilot with a hard call budget")
    real.add_argument("--cases", nargs="*", default=None)
    real.add_argument("--trials", type=int, default=1)
    real.add_argument("--max-calls", type=int, default=None)
    real.add_argument("--fallback-policy", choices=["rules", "fail"], default=None)
    real.add_argument("--timeout", type=float, default=None)
    real.add_argument("--max-retries", type=int, default=0)
    real.add_argument("--report", type=Path, default=None)
    native = sub.add_parser("native", help="native Function Calling suite through the production tool loop")
    native.add_argument("--cases", nargs="*", default=None)
    native.add_argument("--report", type=Path, default=None)
    native.add_argument("--real", action="store_true", help="explicit paid DeepSeek run; never used by CI")
    native.add_argument("--authorize-real-calls", default="")
    native.add_argument("--max-calls", type=int, default=0)
    regrade = sub.add_parser("regrade", help="re-evaluate saved outputs without new LLM calls")
    regrade.add_argument("report", type=Path)
    regrade.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.mode == "offline":
        report_path = args.report or args.report_dir / f"offline-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        report = run_offline(args.case_dir, args.cases, report_path)
    elif args.mode == "real":
        report_path = args.report or args.report_dir / f"real-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        report = run_real(
            args.case_dir, args.cases, trials=args.trials, max_calls=args.max_calls,
            fallback_policy=args.fallback_policy, timeout=args.timeout,
            max_retries=args.max_retries, report_path=report_path,
        )
    elif args.mode == "native":
        from evals import native as native_module

        report_path = args.report or args.report_dir / f"native-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        if args.real:
            report = native_module.run_native_real(
                args.case_dir, args.cases,
                authorize=args.authorize_real_calls, max_calls=args.max_calls,
                report_path=report_path,
            )
        else:
            report = native_module.run_native_offline(args.case_dir, args.cases, report_path)
        if report.get("error") in {"authorization_required", "budget_required", "not_configured"}:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        native_module.print_native_summary(report)
        return 0 if native_module.native_suite_passed(report) else 1
    else:
        report = regrade_report(args.report, args.output)
    if report.get("error") == "not_configured":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    _print_summary(report)
    summary = report.get("summary") or {}
    return 0 if summary.get("normal_runs") == summary.get("normal_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
