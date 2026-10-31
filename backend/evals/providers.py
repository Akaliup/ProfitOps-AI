"""Providers used by the evaluation runner.

``FakeProvider`` is offline and deterministic. Real DeepSeek calls are made only
by the explicit ``real`` mode of the runner, with a hard call budget.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from app.llm import LLMError, LLMJsonResult, LLMResult


class FakeProvider:
    provider_type = "fake"

    def __init__(
        self,
        *,
        data: dict[str, Any] | None = None,
        error: LLMError | None = None,
        model: str = "fake-deepseek-flash",
        latency_ms: float = 12.5,
        attempts: int = 1,
        finish_reason: str | None = "stop",
        usage: dict[str, int] | None = None,
    ):
        self.data = copy.deepcopy(data)
        self.error = error
        self.model = model
        self.latency_ms = latency_ms
        self.attempts = attempts
        self.finish_reason = finish_reason
        self.usage = usage if usage is not None else {"prompt_tokens": 1200, "completion_tokens": 240, "total_tokens": 1440}
        self.calls: list[dict[str, Any]] = []

    def generate_json(self, *, system: str, user: str, max_tokens: int = 1400) -> LLMJsonResult:
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if self.error is not None:
            error = self.error
            error.attempts = self.attempts
            error.latency_ms = self.latency_ms
            raise error
        if self.data is None:
            raise LLMError("llm_empty_response", "FakeProvider 未配置响应", retryable=False)
        content = json.dumps(self.data, ensure_ascii=False)
        meta = LLMResult(
            content=content,
            model=self.model,
            latency_ms=self.latency_ms,
            usage=self.usage,
            attempts=self.attempts,
            request_id="fake-request",
            finish_reason=self.finish_reason,
        )
        return LLMJsonResult(data=copy.deepcopy(self.data), meta=meta)


def provider_label(provider: Any) -> str:
    if provider is None:
        return "none"
    if provider.__class__.__name__ == "DeepSeekProvider":
        return "deepseek"
    return str(getattr(provider, "provider_type", provider.__class__.__name__.lower()))
