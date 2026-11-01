import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import CircuitBreakerProvider, LLMError, LLMResult
from app.main import create_app
from app.observability import METRICS


def make_client(tmp_path, **overrides):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        auto_seed=True,
        llm_api_key="",
        analysis_worker_enabled=False,
        analysis_max_attempts=1,
        **overrides,
    )
    return TestClient(create_app(settings))


class FakeProvider:
    model = "fake-model"
    url = "https://example.invalid/chat/completions"

    def __init__(self, error: LLMError | None = None, content: str = '{"answer": 1}'):
        self.error = error
        self.content = content
        self.calls = 0

    def generate(self, messages, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return LLMResult(
            content=self.content, model=self.model, latency_ms=1.0,
            usage=None, attempts=1,
        )


def test_metrics_endpoint_reports_http_analysis_and_tool_activity(client, analyst):
    assert client.get("/api/metrics", headers=analyst).status_code == 200
    created = client.post("/api/analyses", headers=analyst, json={})
    assert created.status_code == 202

    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "profitops_http_requests_total" in body
    assert "profitops_http_request_duration_seconds_bucket" in body
    assert 'profitops_analysis_jobs_total{event="finished"' in body
    assert "profitops_tool_calls_total" in body
    assert "profitops_llm_requests_total" not in body


def test_metrics_endpoint_can_be_disabled(tmp_path):
    with make_client(tmp_path, metrics_enabled=False) as http:
        assert http.get("/health").status_code == 200
        assert http.get("/metrics").status_code == 404


def test_login_rate_limit_returns_429_with_retry_after(tmp_path):
    with make_client(
        tmp_path, rate_limit_enabled=True, rate_limit_login_requests=2,
        rate_limit_window_seconds=60,
    ) as http:
        body = {"email": "analyst@demo.local", "password": "wrong-password"}
        assert http.post("/api/auth/login", json=body).status_code == 401
        assert http.post("/api/auth/login", json=body).status_code == 401
        blocked = http.post("/api/auth/login", json=body)
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "rate_limited"
        assert blocked.json()["error"]["details"]["scope"] == "login"
        assert int(blocked.headers["Retry-After"]) >= 1
        assert blocked.headers["X-Request-ID"]

        metrics = http.get("/metrics").text
        assert 'profitops_rate_limited_total{scope="login"} 1' in metrics
        assert 'profitops_http_requests_total{method="POST",path="/api/auth/login",status="429"} 1' in metrics


def test_login_rate_limit_ignores_unverified_bearer_tokens(tmp_path):
    with make_client(
        tmp_path, rate_limit_enabled=True, rate_limit_login_requests=2,
        rate_limit_window_seconds=60,
    ) as http:
        body = {"email": "analyst@demo.local", "password": "wrong-password"}
        # Rotating arbitrary bearer values must not hand out fresh login buckets.
        assert http.post(
            "/api/auth/login", json=body, headers={"Authorization": "Bearer arbitrary-1"},
        ).status_code == 401
        assert http.post(
            "/api/auth/login", json=body, headers={"Authorization": "Bearer arbitrary-2"},
        ).status_code == 401
        blocked = http.post(
            "/api/auth/login", json=body, headers={"Authorization": "Bearer arbitrary-3"},
        )
        assert blocked.status_code == 429
        assert blocked.json()["error"]["details"]["scope"] == "login"


def test_integer_gauges_render_as_integers():
    METRICS.set_gauge("analysis_queue_depth", 3, state="queued")
    METRICS.set_gauge("llm_circuit_state", 2)
    body = METRICS.render()
    assert 'profitops_analysis_queue_depth{state="queued"} 3' in body
    assert "profitops_llm_circuit_state 2" in body


def test_default_rate_limit_is_per_bearer_token(tmp_path):
    with make_client(
        tmp_path, rate_limit_enabled=True, rate_limit_requests=2,
        rate_limit_window_seconds=60,
    ) as http:
        analyst = {"Authorization": "Bearer demo-analyst"}
        approver = {"Authorization": "Bearer demo-approver"}
        assert http.get("/api/analyses", headers=analyst).status_code == 200
        assert http.get("/api/analyses", headers=analyst).status_code == 200
        assert http.get("/api/analyses", headers=analyst).status_code == 429
        # A different identity has its own bucket.
        assert http.get("/api/analyses", headers=approver).status_code == 200


def test_circuit_opens_after_consecutive_retryable_failures():
    clock = [0.0]
    provider = FakeProvider(LLMError("llm_server_error", "down", retryable=True))
    breaker = CircuitBreakerProvider(
        provider, failure_threshold=2, cooldown_seconds=30, clock=lambda: clock[0],
    )
    for _ in range(2):
        with pytest.raises(LLMError) as error:
            breaker.generate([])
        assert error.value.code == "llm_server_error"

    with pytest.raises(LLMError) as error:
        breaker.generate([])
    assert error.value.code == "llm_circuit_open"
    assert provider.calls == 2
    assert METRICS.gauge_value("llm_circuit_state") == 2
    assert METRICS.counter_value(
        "llm_requests_total", outcome="rejected", code="llm_circuit_open",
    ) == 1


def test_circuit_half_open_closes_after_success():
    clock = [0.0]
    provider = FakeProvider(LLMError("llm_server_error", "down", retryable=True))
    breaker = CircuitBreakerProvider(
        provider, failure_threshold=1, cooldown_seconds=30, clock=lambda: clock[0],
    )
    with pytest.raises(LLMError):
        breaker.generate([])
    assert METRICS.gauge_value("llm_circuit_state") == 2

    clock[0] = 31
    provider.error = None
    result = breaker.generate([])
    assert result.content == '{"answer": 1}'
    assert provider.calls == 2
    assert METRICS.gauge_value("llm_circuit_state") == 0


def test_non_retryable_errors_do_not_trip_the_circuit():
    provider = FakeProvider(LLMError("llm_auth_failed", "bad key", retryable=False))
    breaker = CircuitBreakerProvider(provider, failure_threshold=2, cooldown_seconds=30)
    for _ in range(5):
        with pytest.raises(LLMError) as error:
            breaker.generate([])
        assert error.value.code == "llm_auth_failed"
    assert provider.calls == 5
    assert METRICS.gauge_value("llm_circuit_state") == 0


def test_half_open_probe_is_released_after_non_retryable_error():
    clock = [0.0]
    provider = FakeProvider(LLMError("llm_server_error", "down", retryable=True))
    breaker = CircuitBreakerProvider(
        provider, failure_threshold=1, cooldown_seconds=0, clock=lambda: clock[0],
    )
    with pytest.raises(LLMError):
        breaker.generate([])

    clock[0] = 1
    provider.error = LLMError("llm_auth_failed", "bad key", retryable=False)
    with pytest.raises(LLMError) as error:
        breaker.generate([])
    assert error.value.code == "llm_auth_failed"

    clock[0] = 2
    provider.error = None
    assert breaker.generate([]).content == '{"answer": 1}'
    assert provider.calls == 3
    assert METRICS.gauge_value("llm_circuit_state") == 0


def test_half_open_probe_is_released_after_provider_exception():
    clock = [0.0]
    provider = FakeProvider(LLMError("llm_server_error", "down", retryable=True))
    breaker = CircuitBreakerProvider(
        provider, failure_threshold=1, cooldown_seconds=0, clock=lambda: clock[0],
    )
    with pytest.raises(LLMError):
        breaker.generate([])

    clock[0] = 1
    provider.error = RuntimeError("provider bug")
    with pytest.raises(RuntimeError):
        breaker.generate([])

    clock[0] = 2
    provider.error = None
    assert breaker.generate([]).content == '{"answer": 1}'
    assert provider.calls == 3


def test_generate_json_is_metered_once():
    provider = FakeProvider(content='{"answer": 1}')
    breaker = CircuitBreakerProvider(provider, failure_threshold=2, cooldown_seconds=30)
    result = breaker.generate_json(system="system", user="user")
    assert result.data == {"answer": 1}
    assert provider.calls == 1
    assert METRICS.counter_value("llm_requests_total", outcome="success", code="ok") == 1


def test_disabled_breaker_delegates_every_call():
    provider = FakeProvider(LLMError("llm_server_error", "down", retryable=True))
    breaker = CircuitBreakerProvider(provider, enabled=False, failure_threshold=1)
    for _ in range(3):
        with pytest.raises(LLMError) as error:
            breaker.generate([])
        assert error.value.code == "llm_server_error"
    assert provider.calls == 3
    assert METRICS.gauge_value("llm_circuit_state") == 0
