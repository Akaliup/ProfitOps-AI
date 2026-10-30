from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROFITOPS_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/profitops.db"
    demo_mode: bool = True
    auto_seed: bool = True
    analyst_token: str = "demo-analyst"
    approver_token: str = "demo-approver"
    step_timeout_seconds: float = Field(default=15, gt=0, le=120)

    @model_validator(mode="after")
    def distinct_tokens(self):
        if not self.analyst_token or not self.approver_token or self.analyst_token == self.approver_token:
            raise ValueError("演示角色必须使用不同且非空的 token")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
