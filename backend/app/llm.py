"""DeepSeek provider for the OpenAI-compatible chat completions protocol.

Verified against https://api-docs.deepseek.com on 2026-10-31:
- base URL: https://api.deepseek.com
- endpoint: POST /chat/completions
- model id: deepseek-flash (DeepSeek-V4.1-Flash-1017)
- JSON mode: response_format={"type": "json_object"} and the word "json" in the prompt
- native tool calling: tools=[{type: "function", function: {...}}], tool_choice
  "auto"/"none", assistant tool_calls (id/type/function.name/function.arguments),
  and role="tool" messages carrying tool_call_id. Thinking mode supports
  auto/none but rejects named/required tool_choice.
- auth: Authorization: Bearer <key>
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import httpx

from .config import Settings
from .observability import METRICS

Message = dict[str, Any]

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, status_code: int | None = None, details: Any = None):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.details = details
        self.attempts = 0
        self.latency_ms: float | None = None
        super().__init__(message)


@dataclass
class LLMToolCall:
    """A model-generated function call. Arguments remain the raw JSON string."""

    id: str
    name: str
    arguments: str

    def as_message(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass
class LLMResult:
    content: str
    model: str
    latency_ms: float
    usage: dict[str, int] | None
    attempts: int
    request_id: str | None = None
    finish_reason: str | None = None
    tool_calls: list[LLMToolCall] = field(default_factory=list)


@dataclass
class LLMJsonResult:
    data: dict[str, Any]
    meta: LLMResult


def parse_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating accidental markdown fences."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("llm_invalid_json", "模型输出不是合法 JSON") from None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            raise LLMError("llm_invalid_json", "模型输出不是合法 JSON") from None
    if not isinstance(data, dict):
        raise LLMError("llm_invalid_json", "模型输出的 JSON 顶层必须是对象")
    return data


def json_result_from(meta: LLMResult) -> LLMJsonResult:
    """Parse a JSON-mode response and attach transport metadata to failures."""
    try:
        data = parse_json_object(meta.content)
    except LLMError as exc:
        exc.attempts = meta.attempts
        exc.latency_ms = meta.latency_ms
        if meta.finish_reason:
            exc.details = {**(exc.details or {}), "finish_reason": meta.finish_reason}
            if meta.finish_reason == "length":
                exc.message = "模型输出达到 max_tokens 被截断，不是合法 JSON"
        raise
    return LLMJsonResult(data=data, meta=meta)


def extract_usage(payload: dict[str, Any]) -> dict[str, int] | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    values: dict[str, int] = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            values[field] = value
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = details.get("cached_tokens")
        if isinstance(cached, int) and not isinstance(cached, bool):
            values["cached_tokens"] = cached
    return values or None


def parse_tool_calls(raw: Any) -> list[LLMToolCall]:
    """Validate the OpenAI-compatible assistant ``tool_calls`` array.

    The model controls this payload, so malformed entries are rejected before
    any tool can be invoked. Arguments are kept as text and parsed by the
    bounded loop, where schema and scope checks happen.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LLMError("llm_invalid_tool_call", "tool_calls 必须是数组")
    calls: list[LLMToolCall] = []
    for item in raw:
        if not isinstance(item, dict):
            raise LLMError("llm_invalid_tool_call", "tool_call 必须是对象")
        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id.strip() or len(call_id) > 200:
            raise LLMError("llm_invalid_tool_call", "tool_call 缺少有效 id 或 id 过长")
        if item.get("type") != "function":
            raise LLMError("llm_invalid_tool_call", "当前只支持 function 类型工具")
        function = item.get("function")
        if not isinstance(function, dict):
            raise LLMError("llm_invalid_tool_call", "tool_call 缺少 function 对象")
        name = function.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise LLMError("llm_invalid_tool_call", "tool_call 缺少函数名或函数名过长")
        arguments = function.get("arguments")
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        if not isinstance(arguments, str):
            raise LLMError("llm_invalid_tool_call", "tool_call arguments 必须是 JSON 字符串")
        calls.append(LLMToolCall(id=call_id, name=name, arguments=arguments))
    return calls


class DeepSeekProvider:
    """Small, synchronous client with bounded retries and explicit error codes."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-flash",
        base_url: str = "https://api.deepseek.com",
        timeout: float = 20.0,
        max_retries: int = 1,
        retry_backoff: float = 0.5,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("DeepSeekProvider 需要非空 API Key")
        if max_retries < 0 or max_retries > 3:
            raise ValueError("max_retries 必须在 0–3 之间")
        base = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = max(0.0, retry_backoff)
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _parse_response(self, response: httpx.Response) -> LLMResult:
        if response.status_code >= 400:
            code, retryable = self._status_error(response.status_code)
            body = (response.text or "")[:300]
            raise LLMError(
                code, f"DeepSeek 返回 HTTP {response.status_code}", retryable=retryable,
                status_code=response.status_code, details={"body": body},
            )
        try:
            payload = response.json()
        except ValueError:
            raise LLMError("llm_invalid_response", "DeepSeek 响应不是合法 JSON", retryable=True) from None
        choices = payload.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        content = message.get("content") if isinstance(message, dict) else None
        finish_reason = choices[0].get("finish_reason") if isinstance(choices, list) and choices else None
        tool_calls = parse_tool_calls(message.get("tool_calls") if isinstance(message, dict) else None)
        if not isinstance(content, str):
            content = ""
        if not content.strip() and not tool_calls:
            raise LLMError(
                "llm_empty_response", "DeepSeek 返回了空内容", retryable=True,
                details={"finish_reason": finish_reason},
            )
        return LLMResult(
            content=content,
            model=str(payload.get("model") or self.model),
            latency_ms=0.0,
            usage=extract_usage(payload),
            attempts=0,
            request_id=payload.get("id") if isinstance(payload.get("id"), str) else None,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            tool_calls=tool_calls,
        )

    @staticmethod
    def _status_error(status: int) -> tuple[str, bool]:
        if status in (401, 403):
            return "llm_auth_failed", False
        if status == 402:
            return "llm_insufficient_balance", False
        if status == 429:
            return "llm_rate_limited", True
        if status in (500, 502, 503, 504):
            return "llm_server_error", True
        return "llm_http_error", status in RETRYABLE_STATUS

    def _request(self, payload: dict[str, Any]) -> LLMResult:
        started = perf_counter()
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            for attempt in range(1, self.max_retries + 2):
                try:
                    response = client.post(self.url, json=payload, headers=self._headers())
                    result = self._parse_response(response)
                    result.attempts = attempt
                    result.latency_ms = round((perf_counter() - started) * 1000, 3)
                    return result
                except LLMError as exc:
                    error = exc
                except httpx.TimeoutException:
                    error = LLMError("llm_timeout", f"DeepSeek 请求超过 {self.timeout}s", retryable=True)
                except httpx.HTTPError as exc:
                    error = LLMError("llm_network_error", f"无法连接 DeepSeek：{type(exc).__name__}", retryable=True)
                error.attempts = attempt
                error.latency_ms = round((perf_counter() - started) * 1000, 3)
                if not error.retryable or attempt > self.max_retries:
                    raise error
                if self.retry_backoff:
                    time.sleep(self.retry_backoff * attempt)
        raise LLMError("llm_unknown_error", "LLM 调用未返回结果")

    def generate(
        self,
        messages: list[Message],
        *,
        response_format: dict[str, str] | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.2,
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format
        return self._request(payload)

    def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict[str, str] | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.2,
    ) -> LLMResult:
        """Call the chat endpoint with native function definitions.

        ``tool_choice`` is intentionally limited to ``auto``/``none`` in the
        agent loop: DeepSeek rejects named/required choices in thinking mode.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        if response_format:
            payload["response_format"] = response_format
        return self._request(payload)

    def generate_json(self, *, system: str, user: str, max_tokens: int = 1400) -> LLMJsonResult:
        meta = self.generate(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )
        return json_result_from(meta)


class CircuitBreakerProvider:
    """Metered provider wrapper with a consecutive-failure circuit breaker.

    Only retryable transport/provider failures trip the breaker; auth errors and
    invalid model output are deterministic and should not hide the real cause.
    The wrapper delegates every provider method, including ``chat`` used by the
    native tool loop, and exposes the wrapped provider's attributes.
    """

    STATE_VALUES = {"closed": 0, "half_open": 1, "open": 2}

    def __init__(
        self,
        provider,
        *,
        enabled: bool = True,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        clock=time.monotonic,
    ):
        self.provider = provider
        self.enabled = enabled
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at = 0.0
        self._state = "closed"
        self._trial_in_flight = False
        METRICS.set_gauge("llm_circuit_state", self.STATE_VALUES[self._state])

    def __getattr__(self, name: str):
        return getattr(self.provider, name)

    def _set_state(self, state: str) -> None:
        self._state = state
        METRICS.set_gauge("llm_circuit_state", self.STATE_VALUES[state])

    def _before_call(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            now = self._clock()
            if self._state == "open":
                remaining = self.cooldown_seconds - (now - self._opened_at)
                if remaining <= 0:
                    self._set_state("half_open")
                    self._trial_in_flight = True
                    return
                raise LLMError(
                    "llm_circuit_open",
                    f"LLM 连续失败，熔断中，约 {max(1, int(remaining))}s 后重试",
                    retryable=False,
                )
            if self._state == "half_open":
                if self._trial_in_flight:
                    raise LLMError("llm_circuit_open", "LLM 熔断半开，已有探测请求在执行", retryable=False)
                self._trial_in_flight = True

    def _record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._trial_in_flight = False
            if self._state != "closed":
                self._set_state("closed")

    def _record_failure(self, error: LLMError) -> None:
        if not self.enabled:
            return
        with self._lock:
            # A half-open probe must never leave the breaker stuck when the
            # failure is deterministic (for example an auth error).
            self._trial_in_flight = False
            if not error.retryable:
                return
            self._failures += 1
            if self._state == "half_open" or self._failures >= self.failure_threshold:
                self._opened_at = self._clock()
                self._set_state("open")

    def _call(self, method: str, *args, **kwargs):
        try:
            self._before_call()
        except LLMError as exc:
            METRICS.inc("llm_requests_total", outcome="rejected", code=exc.code)
            raise
        started = perf_counter()
        try:
            result = getattr(self.provider, method)(*args, **kwargs)
        except LLMError as exc:
            self._record_failure(exc)
            METRICS.inc("llm_requests_total", outcome="failure", code=exc.code)
            METRICS.observe(
                "llm_request_duration_seconds", perf_counter() - started,
                outcome="failure", code=exc.code,
            )
            raise
        except Exception:
            # Release a half-open probe without counting a provider bug as a
            # transport outage; otherwise the breaker can stay half-open forever.
            self._record_failure(LLMError("llm_unknown_error", "Provider 异常", retryable=False))
            METRICS.inc("llm_requests_total", outcome="failure", code="llm_unknown_error")
            METRICS.observe(
                "llm_request_duration_seconds", perf_counter() - started,
                outcome="failure", code="llm_unknown_error",
            )
            raise
        self._record_success()
        METRICS.inc("llm_requests_total", outcome="success", code="ok")
        METRICS.observe("llm_request_duration_seconds", perf_counter() - started, outcome="success", code="ok")
        return result

    def generate(self, messages, *, response_format=None, max_tokens: int = 1200, temperature: float = 0.2):
        return self._call(
            "generate", messages,
            response_format=response_format, max_tokens=max_tokens, temperature=temperature,
        )

    def chat(self, messages, *, tools=None, tool_choice=None, response_format=None, max_tokens: int = 1200, temperature: float = 0.2):
        return self._call(
            "chat", messages,
            tools=tools, tool_choice=tool_choice, response_format=response_format,
            max_tokens=max_tokens, temperature=temperature,
        )

    def generate_json(self, *, system: str, user: str, max_tokens: int = 1400) -> LLMJsonResult:
        meta = self.generate(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )
        return json_result_from(meta)


def build_llm_provider(settings: Settings) -> CircuitBreakerProvider | None:
    """Return None when the deployment has no key, so the rules engine stays usable."""
    if not settings.llm_configured:
        return None
    provider = DeepSeekProvider(
        api_key=settings.llm_api_key.get_secret_value().strip(),
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,
    )
    return CircuitBreakerProvider(
        provider,
        enabled=settings.circuit_breaker_enabled,
        failure_threshold=settings.circuit_breaker_failures,
        cooldown_seconds=settings.circuit_breaker_cooldown_seconds,
    )
