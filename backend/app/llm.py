"""DeepSeek provider for the OpenAI-compatible chat completions protocol.

Verified against https://api-docs.deepseek.com on 2026-10-31:
- base URL: https://api.deepseek.com
- endpoint: POST /chat/completions
- model id: deepseek-flash (DeepSeek-V4.1-Flash-1017)
- JSON mode: response_format={"type": "json_object"} and the word "json" in the prompt
- auth: Authorization: Bearer <key>
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

from .config import Settings

Message = dict[str, str]

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
class LLMResult:
    content: str
    model: str
    latency_ms: float
    usage: dict[str, int] | None
    attempts: int
    request_id: str | None = None
    finish_reason: str | None = None


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
        if not isinstance(content, str) or not content.strip():
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

    def generate_json(self, *, system: str, user: str, max_tokens: int = 1400) -> LLMJsonResult:
        meta = self.generate(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )
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


def build_llm_provider(settings: Settings) -> DeepSeekProvider | None:
    """Return None when the deployment has no key, so the rules engine stays usable."""
    if not settings.llm_configured:
        return None
    return DeepSeekProvider(
        api_key=settings.llm_api_key.get_secret_value().strip(),
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout,
        max_retries=settings.llm_max_retries,
    )
