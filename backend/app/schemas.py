from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Period(StrictModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_period(self):
        if self.end_date < self.start_date:
            raise ValueError("结束日期不能早于开始日期")
        if (self.end_date - self.start_date).days >= 90:
            raise ValueError("单个周期最多 90 天")
        return self


class AnalysisRequest(StrictModel):
    start_date: date | None = None
    end_date: date | None = None
    store_id: str | None = Field(default=None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_dates(self):
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("必须同时提供开始和结束日期")
        if self.start_date is not None:
            Period(start_date=self.start_date, end_date=self.end_date)
        return self


class MetricsInput(StrictModel):
    period: Period
    store_id: str | None = None


class Totals(StrictModel):
    sales: int = 0
    discounts: int = 0
    refunds: int = 0
    cogs: int = 0
    ad_spend: int = 0
    fulfillment: int = 0
    platform_fees: int = 0
    other_costs: int = 0
    orders: int = 0
    units: int = 0
    net_sales: int = 0
    net_profit: int = 0
    margin_pct: float | None = None


class Change(StrictModel):
    absolute: int | float
    pct: float | None


class DataQuality(StrictModel):
    complete: bool
    expected_records: int
    observed_records: int
    missing_records: int
    incomplete_records: int
    missing_examples: list[str]
    issues: list[str]


class Driver(StrictModel):
    metric: str
    label: str
    previous: int
    current: int
    contribution: int
    source: str


class RawMetrics(StrictModel):
    period: Period
    previous_period: Period
    store_id: str | None
    currency: Literal["USD"] = "USD"
    current: Totals
    previous: Totals
    trend: list[dict[str, Any]]
    sku_breakdown: list[dict[str, Any]]
    data_quality: DataQuality
    source: str = "orders + daily_operations / schema v1"


class MetricsReport(RawMetrics):
    changes: dict[str, Change]
    drivers: list[Driver]
    anomaly: bool
    bridge_verified: bool


class CalculateInput(StrictModel):
    metrics: RawMetrics


class DriversInput(StrictModel):
    report: MetricsReport


class DriverReport(StrictModel):
    anomaly: bool
    conclusion: str
    primary_driver: Driver | None
    drivers: list[Driver]
    evidence: list[dict[str, Any]]
    bridge_total: int


class RecommendationInput(StrictModel):
    analysis_id: str
    report: MetricsReport
    findings: DriverReport


class RecommendationOutput(StrictModel):
    recommendation_id: str
    status: str


class ApprovalInput(StrictModel):
    recommendation_id: str


class ApprovalOutput(StrictModel):
    recommendation_id: str
    status: str
    approved_by: str | None


class ExecuteInput(StrictModel):
    recommendation_id: str
    idempotency_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")


class ExecuteOutput(StrictModel):
    execution_id: str
    recommendation_id: str
    status: str
    simulation: bool = True
    replayed: bool = False
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class DecisionRequest(StrictModel):
    reason: str = Field(min_length=2, max_length=500)


class ToolFailure(StrictModel):
    code: str
    message: str
    details: Any = None


class ToolResult(StrictModel):
    ok: bool
    data: dict[str, Any] | None = None
    error: ToolFailure | None = None
