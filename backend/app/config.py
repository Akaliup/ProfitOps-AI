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
    llm_native_tools: bool = Field(
        default=True, validation_alias=AliasChoices("LLM_NATIVE_TOOLS", "PROFITOPS_LLM_NATIVE_TOOLS", "llm_native_tools")
    )
    llm_tool_max_rounds: int = Field(
        default=4, ge=2, le=8,
        validation_alias=AliasChoices("LLM_TOOL_MAX_ROUNDS", "PROFITOPS_LLM_TOOL_MAX_ROUNDS", "llm_tool_max_rounds"),
    )
    llm_tool_max_calls: int = Field(
        default=6, ge=1, le=12,
        validation_alias=AliasChoices("LLM_TOOL_MAX_CALLS", "PROFITOPS_LLM_TOOL_MAX_CALLS", "llm_tool_max_calls"),
    )
    llm_tool_max_repeats: int = Field(
        default=2, ge=1, le=5,
        validation_alias=AliasChoices("LLM_TOOL_MAX_REPEATS", "PROFITOPS_LLM_TOOL_MAX_REPEATS", "llm_tool_max_repeats"),
    )
    llm_tool_budget_seconds: float = Field(
        default=90.0, gt=0, le=300,
        validation_alias=AliasChoices("LLM_TOOL_BUDGET_SECONDS", "PROFITOPS_LLM_TOOL_BUDGET_SECONDS", "llm_tool_budget_seconds"),
    )
    llm_tool_result_max_bytes: int = Field(
        default=16384, ge=512, le=65536,
        validation_alias=AliasChoices("LLM_TOOL_RESULT_MAX_BYTES", "PROFITOPS_LLM_TOOL_RESULT_MAX_BYTES", "llm_tool_result_max_bytes"),
    )

    # Knowledge base retrieval. Hashing is the offline default; openai means any
    # OpenAI-compatible /embeddings endpoint (Ollama, vLLM, a private gateway).
    knowledge_embedding_provider: Literal["hashing", "openai"] = Field(
        default="hashing",
        validation_alias=AliasChoices("KNOWLEDGE_EMBEDDING_PROVIDER", "PROFITOPS_KNOWLEDGE_EMBEDDING_PROVIDER", "knowledge_embedding_provider"),
    )
    knowledge_embedding_model: str = Field(
        default="hashing-v1", min_length=1, max_length=80,
        validation_alias=AliasChoices("KNOWLEDGE_EMBEDDING_MODEL", "PROFITOPS_KNOWLEDGE_EMBEDDING_MODEL", "knowledge_embedding_model"),
    )
    knowledge_embedding_base_url: str = Field(
        default="", max_length=200,
        validation_alias=AliasChoices("KNOWLEDGE_EMBEDDING_BASE_URL", "PROFITOPS_KNOWLEDGE_EMBEDDING_BASE_URL", "knowledge_embedding_base_url"),
    )
    knowledge_embedding_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("KNOWLEDGE_EMBEDDING_API_KEY", "PROFITOPS_KNOWLEDGE_EMBEDDING_API_KEY", "knowledge_embedding_api_key"),
    )
    knowledge_embedding_dimensions: int = Field(
        default=256, ge=32, le=1024,
        validation_alias=AliasChoices("KNOWLEDGE_EMBEDDING_DIMENSIONS", "PROFITOPS_KNOWLEDGE_EMBEDDING_DIMENSIONS", "knowledge_embedding_dimensions"),
    )
    knowledge_chunk_chars: int = Field(
        default=600, ge=200, le=2000,
        validation_alias=AliasChoices("KNOWLEDGE_CHUNK_CHARS", "PROFITOPS_KNOWLEDGE_CHUNK_CHARS", "knowledge_chunk_chars"),
    )
    knowledge_chunk_overlap: int = Field(
        default=100, ge=0, le=500,
        validation_alias=AliasChoices("KNOWLEDGE_CHUNK_OVERLAP", "PROFITOPS_KNOWLEDGE_CHUNK_OVERLAP", "knowledge_chunk_overlap"),
    )
    knowledge_top_k: int = Field(
        default=5, ge=1, le=10,
        validation_alias=AliasChoices("KNOWLEDGE_TOP_K", "PROFITOPS_KNOWLEDGE_TOP_K", "knowledge_top_k"),
    )
    knowledge_candidate_k: int = Field(
        default=20, ge=1, le=100,
        validation_alias=AliasChoices("KNOWLEDGE_CANDIDATE_K", "PROFITOPS_KNOWLEDGE_CANDIDATE_K", "knowledge_candidate_k"),
    )
    knowledge_min_score: float = Field(
        default=0.15, ge=0, le=1,
        validation_alias=AliasChoices("KNOWLEDGE_MIN_SCORE", "PROFITOPS_KNOWLEDGE_MIN_SCORE", "knowledge_min_score"),
    )

    @property
    def llm_configured(self) -> bool:
        return self.llm_provider == "deepseek" and bool(self.llm_api_key.get_secret_value().strip())

    @property
    def knowledge_embedding_remote(self) -> bool:
        return self.knowledge_embedding_provider == "openai"

    @model_validator(mode="after")
    def distinct_tokens(self):
        if not self.analyst_token or not self.approver_token or self.analyst_token == self.approver_token:
            raise ValueError("演示角色必须使用不同且非空的 token")
        if self.llm_base_url and not self.llm_base_url.startswith(("http://", "https://")):
            raise ValueError("LLM_BASE_URL 必须以 http:// 或 https:// 开头")
        if self.knowledge_embedding_provider == "openai":
            if not self.knowledge_embedding_base_url:
                raise ValueError("KNOWLEDGE_EMBEDDING_BASE_URL 在使用 openai Provider 时不能为空")
            if not self.knowledge_embedding_base_url.startswith(("http://", "https://")):
                raise ValueError("KNOWLEDGE_EMBEDDING_BASE_URL 必须以 http:// 或 https:// 开头")
        if self.knowledge_chunk_overlap >= self.knowledge_chunk_chars:
            raise ValueError("KNOWLEDGE_CHUNK_OVERLAP 必须小于 KNOWLEDGE_CHUNK_CHARS")
        if self.knowledge_candidate_k < self.knowledge_top_k:
            raise ValueError("KNOWLEDGE_CANDIDATE_K 不能小于 KNOWLEDGE_TOP_K")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
