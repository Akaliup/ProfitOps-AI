import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.llm import DeepSeekProvider, LLMError, LLMJsonResult, LLMResult, build_llm_provider, parse_json_object
from app.llm_analysis import validate_evidence_references
from app.main import create_app
from app.schemas import LLMAnalysisOutput, LLMRun

VALID_OUTPUT = {
    "summary": "广告支出增长快于销售额，吞噬了增长带来的毛利。",
    "root_causes": [{
        "cause": "广告支出增长快于销售额",
        "reasoning": "广告支出的负向贡献绝对值最大，是利润下降的主要会计驱动项。",
        "evidence_ids": ["ev-ad_spend"],
    }],
    "evidence_references": ["ev-ad_spend", "ev-sales"],
    "recommendations": [{
        "action": "对主要 SKU 的日广告预算做 15% 的受控模拟下调",
        "rationale": "先以模拟验证节省空间，再根据后续转化数据决定是否扩大。",
        "evidence_ids": ["ev-ad_spend"],
        "priority": "high",
    }],
    "expected_impact": {
        "direction": "improve",
        "horizon_days": 30,
        "explanation": "若流量与转化不变，广告支出下降会改善利润；这是条件情景，不是收益承诺。",
    },
    "risks": ["预算下调可能减少流量和转化。"],
    "missing_information": ["广告活动级 ROAS"],
    "confidence": 0.72,
}


def success_payload(content: str | None = None) -> dict:
    return {
        "id": "chatcmpl-test",
        "model": "deepseek-flash",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content or json.dumps(VALID_OUTPUT)}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 10},
        },
    }


class FakeProvider:
    def __init__(self, output=None, error=None, *, model="deepseek-flash", usage=None, latency_ms=12.5, attempts=1):
        self.output = output
        self.error = error
        self.model = model
        self.usage = usage
        self.latency_ms = latency_ms
        self.attempts = attempts
        self.calls = 0
        self.max_tokens = None

    def generate_json(self, *, system: str, user: str, max_tokens: int = 1400) -> LLMJsonResult:
        self.calls += 1
        self.max_tokens = max_tokens
        if self.error:
            raise self.error
        return LLMJsonResult(
            data=self.output,
            meta=LLMResult(
                content=json.dumps(self.output), model=self.model, latency_ms=self.latency_ms,
                usage=self.usage, attempts=self.attempts,
            ),
        )


@pytest.fixture
def llm_client(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'llm.db'}", auto_seed=True, llm_api_key=""))
    with TestClient(app) as client:
        yield client


def run(client, provider=None):
    if provider is not None:
        client.app.state.llm = provider
    headers = {"Authorization": "Bearer demo-analyst"}
    created = client.post("/api/analyses", json={}, headers=headers)
    assert created.status_code == 202, created.text
    detail = client.get(f"/api/analyses/{created.json()['id']}", headers=headers).json()
    return detail


def recommendation_of(client, detail):
    assert detail["recommendation_id"], detail
    return client.get(f"/api/recommendations/{detail['recommendation_id']}", headers={"Authorization": "Bearer demo-analyst"}).json()


def test_provider_success_parses_content_usage_and_latency():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=success_payload())

    provider = DeepSeekProvider(api_key="test-key", timeout=2, max_retries=1, retry_backoff=0, transport=httpx.MockTransport(handler))
    result = provider.generate_json(system="return json", user="please return json")
    assert captured["path"] == "/chat/completions"
    assert captured["auth"] == "Bearer test-key"
    assert captured["body"]["model"] == "deepseek-flash"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert result.data["summary"] == VALID_OUTPUT["summary"]
    assert result.meta.model == "deepseek-flash"
    assert result.meta.usage == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "cached_tokens": 10}
    assert result.meta.attempts == 1
    assert result.meta.latency_ms >= 0


def test_settings_read_plain_llm_environment(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "env-test-key")
    monkeypatch.setenv("LLM_MODEL", "deepseek-flash")
    monkeypatch.setenv("LLM_MAX_TOKENS", "4321")
    settings = Settings(_env_file=None)
    assert settings.llm_configured is True
    assert settings.llm_api_key.get_secret_value() == "env-test-key"
    assert settings.llm_model == "deepseek-flash"
    assert settings.llm_max_tokens == 4321
    provider = build_llm_provider(settings)
    assert provider is not None
    assert provider.url == "https://api.deepseek.com/chat/completions"
    monkeypatch.setenv("LLM_PROVIDER", "disabled")
    assert Settings(_env_file=None).llm_configured is False


def test_provider_plain_text_generation():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "deepseek-flash", "choices": [{"message": {"content": "plain answer"}}]})

    provider = DeepSeekProvider(api_key="test-key", timeout=2, max_retries=0, retry_backoff=0, transport=httpx.MockTransport(handler))
    result = provider.generate([{"role": "user", "content": "hello"}])
    assert result.content == "plain answer"
    assert result.model == "deepseek-flash"
    assert result.attempts == 1
    assert result.usage is None


def test_provider_retries_server_error_then_succeeds():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        return httpx.Response(200, json=success_payload())

    provider = DeepSeekProvider(api_key="test-key", timeout=2, max_retries=1, retry_backoff=0, transport=httpx.MockTransport(handler))
    result = provider.generate_json(system="json", user="json")
    assert len(calls) == 2
    assert result.meta.attempts == 2


def test_provider_auth_failure_is_not_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, json={"error": {"message": "Authentication Fails"}})

    provider = DeepSeekProvider(api_key="bad-key", timeout=2, max_retries=2, retry_backoff=0, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as caught:
        provider.generate_json(system="json", user="json")
    assert caught.value.code == "llm_auth_failed"
    assert caught.value.retryable is False
    assert caught.value.attempts == 1
    assert len(calls) == 1


def test_provider_timeout_is_bounded():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.TimeoutException("timed out", request=request)

    provider = DeepSeekProvider(api_key="test-key", timeout=0.01, max_retries=1, retry_backoff=0, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as caught:
        provider.generate_json(system="json", user="json")
    assert caught.value.code == "llm_timeout"
    assert caught.value.attempts == 2
    assert len(calls) == 2


def test_provider_rejects_invalid_json_and_empty_content():
    def invalid(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=success_payload("this is not json"))

    provider = DeepSeekProvider(api_key="test-key", timeout=2, max_retries=0, retry_backoff=0, transport=httpx.MockTransport(invalid))
    with pytest.raises(LLMError) as caught:
        provider.generate_json(system="json", user="json")
    assert caught.value.code == "llm_invalid_json"

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=success_payload("   "))

    provider = DeepSeekProvider(api_key="test-key", timeout=2, max_retries=0, retry_backoff=0, transport=httpx.MockTransport(empty))
    with pytest.raises(LLMError) as caught:
        provider.generate_json(system="json", user="json")
    assert caught.value.code == "llm_empty_response"


def test_provider_reports_truncated_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "model": "deepseek-flash",
            "choices": [{"message": {"content": '{"summary":"cut'}, "finish_reason": "length"}],
        })

    provider = DeepSeekProvider(api_key="test-key", timeout=2, max_retries=0, retry_backoff=0, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as caught:
        provider.generate_json(system="json", user="json")
    assert caught.value.code == "llm_invalid_json"
    assert caught.value.details["finish_reason"] == "length"
    assert "截断" in caught.value.message


def test_parse_json_object_tolerates_markdown_fences():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('prefix {"a": 2} suffix') == {"a": 2}


def test_structured_schema_accepts_valid_output():
    output = LLMAnalysisOutput.model_validate(VALID_OUTPUT)
    assert output.confidence == 0.72
    assert output.root_causes[0].evidence_ids == ["ev-ad_spend"]


def test_structured_schema_rejects_missing_or_invalid_fields():
    missing = {key: value for key, value in VALID_OUTPUT.items() if key != "confidence"}
    with pytest.raises(ValidationError):
        LLMAnalysisOutput.model_validate(missing)
    with pytest.raises(ValidationError):
        LLMAnalysisOutput.model_validate({**VALID_OUTPUT, "confidence": 1.5})
    with pytest.raises(ValidationError):
        LLMAnalysisOutput.model_validate({**VALID_OUTPUT, "root_causes": []})
    without_risks = {key: value for key, value in VALID_OUTPUT.items() if key != "risks"}
    with pytest.raises(ValidationError):
        LLMAnalysisOutput.model_validate(without_risks)
    with pytest.raises(ValidationError):
        LLMRun(status="llm_success")


def test_evidence_reference_validation_reports_unknown_ids():
    output = LLMAnalysisOutput.model_validate({
        **VALID_OUTPUT,
        "evidence_references": ["ev-ad_spend", "ev-unknown"],
        "root_causes": [{**VALID_OUTPUT["root_causes"][0], "evidence_ids": ["ev-ghost"]}],
    })
    unknown = validate_evidence_references(output, [{"id": "ev-ad_spend"}, {"id": "ev-sales"}])
    assert unknown == ["ev-ghost", "ev-unknown"]


def test_analysis_without_key_uses_deterministic_fallback(llm_client):
    detail = run(llm_client)
    assert detail["status"] == "waiting_approval"
    assert detail["result"]["engine"] == "deterministic-rules-v1"
    assert detail["result"]["llm"]["status"] == "deterministic_fallback"
    assert detail["result"]["llm"]["configured"] is False
    assert detail["result"]["llm"]["reason"] == "not_configured"
    recommendation = recommendation_of(llm_client, detail)
    assert recommendation["body"]["engine"] == "deterministic-rules-v1"
    assert recommendation["body"]["llm"]["status"] == "deterministic_fallback"
    logs = llm_client.get(f"/api/audit-logs?object_id={detail['id']}", headers={"Authorization": "Bearer demo-analyst"}).json()["items"]
    assert any(log["action"] == "analysis.llm_fallback" and log["details"]["reason"] == "not_configured" for log in logs)


def test_llm_success_is_persisted_and_used_in_recommendation(llm_client):
    provider = FakeProvider(VALID_OUTPUT, usage={"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200}, latency_ms=321.5)
    detail = run(llm_client, provider)
    run_meta = detail["result"]["llm"]
    assert run_meta["status"] == "llm_success"
    assert run_meta["configured"] is True
    assert run_meta["model"] == "deepseek-flash"
    assert run_meta["latency_ms"] == 321.5
    assert run_meta["usage"]["total_tokens"] == 200
    assert run_meta["output"]["root_causes"][0]["evidence_ids"] == ["ev-ad_spend"]
    assert detail["result"]["engine"] == "deepseek-llm-v1"
    recommendation = recommendation_of(llm_client, detail)
    assert recommendation["body"]["engine"] == "deepseek-llm-v1"
    assert recommendation["body"]["problem"] == VALID_OUTPUT["summary"]
    assert recommendation["body"]["llm"]["output"]["confidence"] == 0.72
    known = {item["id"] for item in recommendation["body"]["evidence"]}
    assert set(recommendation["body"]["llm"]["output"]["evidence_references"]) <= known
    logs = llm_client.get(f"/api/audit-logs?object_id={detail['id']}", headers={"Authorization": "Bearer demo-analyst"}).json()["items"]
    assert any(log["action"] == "analysis.llm_succeeded" and log["details"]["status"] == "llm_success" for log in logs)


def test_workflow_uses_configured_max_tokens(tmp_path):
    app = create_app(Settings(
        database_url=f"sqlite:///{tmp_path / 'llm-tokens.db'}",
        auto_seed=True,
        llm_api_key="",
        llm_max_tokens=4321,
    ))
    provider = FakeProvider(VALID_OUTPUT)
    with TestClient(app) as client:
        detail = run(client, provider)
    assert detail["result"]["llm"]["status"] == "llm_success"
    assert provider.max_tokens == 4321


def test_invalid_evidence_reference_falls_back(llm_client):
    bad = {**VALID_OUTPUT, "evidence_references": ["ev-ad_spend", "ev-fabricated"]}
    detail = run(llm_client, FakeProvider(bad))
    run_meta = detail["result"]["llm"]
    assert run_meta["status"] == "deterministic_fallback"
    assert run_meta["error"]["code"] == "invalid_evidence_reference"
    assert run_meta["output"] is None
    assert detail["status"] == "waiting_approval"
    recommendation = recommendation_of(llm_client, detail)
    assert recommendation["body"]["llm"]["status"] == "deterministic_fallback"
    assert recommendation["body"]["problem"] != VALID_OUTPUT["summary"]
    logs = llm_client.get(f"/api/audit-logs?object_id={detail['id']}", headers={"Authorization": "Bearer demo-analyst"}).json()["items"]
    assert any(log["action"] == "analysis.llm_fallback" and log["details"]["reason"] == "invalid_evidence_reference" for log in logs)


def test_llm_timeout_falls_back_and_is_audited(llm_client):
    error = LLMError("llm_timeout", "DeepSeek 请求超过 20s", retryable=True)
    error.attempts, error.latency_ms = 2, 20000.0
    detail = run(llm_client, FakeProvider(error=error))
    run_meta = detail["result"]["llm"]
    assert run_meta["status"] == "deterministic_fallback"
    assert run_meta["error"]["code"] == "llm_timeout"
    assert run_meta["attempts"] == 2
    assert run_meta["latency_ms"] == 20000.0
    assert detail["status"] == "waiting_approval"


def test_llm_schema_invalid_falls_back(llm_client):
    detail = run(llm_client, FakeProvider({"summary": "缺少其余字段"}))
    run_meta = detail["result"]["llm"]
    assert run_meta["status"] == "deterministic_fallback"
    assert run_meta["error"]["code"] == "llm_schema_invalid"
    assert detail["recommendation_id"]


def test_llm_fail_policy_stops_analysis(tmp_path):
    app = create_app(Settings(
        database_url=f"sqlite:///{tmp_path / 'fail.db'}", auto_seed=True,
        llm_api_key="", llm_fallback_policy="fail",
    ))
    with TestClient(app) as client:
        detail = run(client, FakeProvider(error=LLMError("llm_auth_failed", "演示鉴权失败")))
    assert detail["status"] == "failed"
    assert detail["error"]["code"] == "llm_failed"
    assert detail["recommendation_id"] is None
    assert detail["result"]["llm"]["status"] == "llm_failed"
    assert "llm_root_cause_analysis" in [step["name"] for step in detail["steps"]]
    assert "create_recommendation" not in [step["name"] for step in detail["steps"]]


def test_approval_and_idempotent_execution_after_llm_success(llm_client):
    detail = run(llm_client, FakeProvider(VALID_OUTPUT))
    path = f"/api/recommendations/{detail['recommendation_id']}"
    approver = {"Authorization": "Bearer demo-approver"}
    assert llm_client.post(f"{path}/approve", json={"reason": "已复核 LLM 证据引用"}, headers=approver).status_code == 200
    first = llm_client.post(f"{path}/execute", headers={**approver, "Idempotency-Key": "llm-exec"})
    second = llm_client.post(f"{path}/execute", headers={**approver, "Idempotency-Key": "llm-exec"})
    assert first.status_code == second.status_code == 200
    assert first.json()["execution_id"] == second.json()["execution_id"]
    assert second.json()["replayed"] is True
    logs = llm_client.get(f"/api/audit-logs?object_id={detail['recommendation_id']}", headers={"Authorization": "Bearer demo-analyst"}).json()["items"]
    actions = {log["action"] for log in logs}
    assert {"recommendation.approve", "execution.started", "execution.executed", "execution.replayed"} <= actions
