from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROFITOPS_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/profitops.db"
    demo_mode: bool = True
    auto_seed: bool = True
    analyst_token: str = "demo-analyst"
    approver_token: str = "demo-approver"
    step_timeout_seconds: float = Field(default=15, gt=0, le=120)

    # LLM settings intentionally use the plain names the deployment platform exposes.
    # PROFITOPS_-prefixed variants remain accepted for backward compatibility.
    llm_provider: Literal["deepseek", "disabled"] = Field(
        default="deepseek", validation_alias=AliasChoices("LLM_PROVIDER", "PROFITOPS_LLM_PROVIDER", "llm_provider")
    )
    llm_model: str = Field(
        default="deepseek-flash", min_length=1, max_length=80,
        validation_alias=AliasChoices("LLM_MODEL", "PROFITOPS_LLM_MODEL", "llm_model"),
    )
    llm_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("LLM_API_KEY", "PROFITOPS_LLM_API_KEY", "llm_api_key")
    )
    llm_base_url: str = Field(
        default="https://api.deepseek.com", min_length=1, max_length=200,
        validation_alias=AliasChoices("LLM_BASE_URL", "PROFITOPS_LLM_BASE_URL", "llm_base_url"),
    )
    llm_timeout: float = Field(
        default=20.0, gt=0, le=120,
        validation_alias=AliasChoices("LLM_TIMEOUT", "PROFITOPS_LLM_TIMEOUT", "llm_timeout"),
    )
    llm_max_tokens: int = Field(
        default=4000, ge=256, le=8192,
        validation_alias=AliasChoices("LLM_MAX_TOKENS", "PROFITOPS_LLM_MAX_TOKENS", "llm_max_tokens"),
    )
    llm_max_retries: int = Field(
        default=1, ge=0, le=3,
        validation_alias=AliasChoices("LLM_MAX_RETRIES", "PROFITOPS_LLM_MAX_RETRIES", "llm_max_retries"),
    )
    llm_fallback_policy: Literal["rules", "fail"] = Field(
        default="rules", validation_alias=AliasChoices("LLM_FALLBACK_POLICY", "PROFITOPS_LLM_FALLBACK_POLICY", "llm_fallback_policy")
    )

    @property
    def llm_configured(self) -> bool:
        return self.llm_provider == "deepseek" and bool(self.llm_api_key.get_secret_value().strip())

    @model_validator(mode="after")
    def distinct_tokens(self):
        if not self.analyst_token or not self.approver_token or self.analyst_token == self.approver_token:
            raise ValueError("演示角色必须使用不同且非空的 token")
        if self.llm_base_url and not self.llm_base_url.startswith(("http://", "https://")):
            raise ValueError("LLM_BASE_URL 必须以 http:// 或 https:// 开头")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
