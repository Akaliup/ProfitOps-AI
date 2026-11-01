from datetime import date, datetime
from typing import Annotated, Any, Literal

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


class ProfitSnapshot(StrictModel):
    """Compact, server-computed profit report returned to an LLM tool caller.

    It intentionally omits the 60-day trend and the full SKU rows so a tool
    result stays small; the numbers are still produced by the deterministic
    metrics functions, never by the model.
    """

    period: Period
    previous_period: Period
    store_id: str | None
    currency: Literal["USD"] = "USD"
    current: Totals
    previous: Totals
    changes: dict[str, Change]
    drivers: list[Driver]
    anomaly: bool
    bridge_verified: bool
    data_quality: DataQuality


class ProfitAnalysisOutput(StrictModel):
    report: ProfitSnapshot
    findings: DriverReport


class SkuBreakdownRow(StrictModel):
    product_id: str
    sku: str
    name: str
    net_profit_current: int
    net_profit_previous: int
    profit_delta: int
    ad_spend_delta: int
    inventory: int
    inbound_inventory: int


class SkuBreakdownOutput(StrictModel):
    period: Period
    store_id: str | None
    rows: list[SkuBreakdownRow]


ShortText = Annotated[str, Field(min_length=2, max_length=300)]
LongText = Annotated[str, Field(min_length=2, max_length=800)]


class LLMRootCause(StrictModel):
    cause: ShortText
    reasoning: LongText
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class LLMRecommendation(StrictModel):
    action: ShortText
    rationale: LongText
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    priority: Literal["high", "medium", "low"]


class LLMExpectedImpact(StrictModel):
    direction: Literal["improve", "neutral", "uncertain"]
    horizon_days: int = Field(ge=1, le=90)
    explanation: LongText


class LLMAnalysisOutput(StrictModel):
    summary: LongText
    root_causes: list[LLMRootCause] = Field(min_length=1, max_length=5)
    evidence_references: list[str] = Field(min_length=1, max_length=8)
    knowledge_references: list[str] = Field(default_factory=list, max_length=8)
    recommendations: list[LLMRecommendation] = Field(min_length=1, max_length=5)
    expected_impact: LLMExpectedImpact
    risks: list[ShortText] = Field(max_length=6)
    missing_information: list[ShortText] = Field(max_length=6)
    confidence: float = Field(ge=0, le=1)


class LLMUsage(StrictModel):
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)


class LLMErrorInfo(StrictModel):
    code: str
    message: str
    retryable: bool = False


class LLMToolCallTrace(StrictModel):
    """One model-requested tool call, including rejected calls."""

    round: int = Field(ge=1, le=20)
    tool_call_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool
    result_summary: str = Field(min_length=1, max_length=500)
    error_code: str | None = Field(default=None, max_length=80)
    latency_ms: float = Field(ge=0)


class LLMToolRound(StrictModel):
    round: int = Field(ge=1, le=20)
    finish_reason: str | None = Field(default=None, max_length=80)
    tool_call_count: int = Field(ge=0, le=20)
    latency_ms: float = Field(ge=0)
    usage: LLMUsage | None = None


class LLMRun(StrictModel):
    status: Literal["llm_success", "deterministic_fallback", "llm_failed", "not_requested"]
    configured: bool = False
    provider: str = "deepseek"
    model: str = "deepseek-flash"
    reason: str | None = None
    attempts: int = Field(default=0, ge=0, le=64)
    latency_ms: float | None = Field(default=None, ge=0)
    usage: LLMUsage | None = None
    output: LLMAnalysisOutput | None = None
    error: LLMErrorInfo | None = None
    native_tools: bool = False
    termination_reason: str | None = Field(default=None, max_length=80)
    rounds: list[LLMToolRound] = Field(default_factory=list, max_length=20)
    tool_calls: list[LLMToolCallTrace] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def validate_status(self):
        if self.status == "llm_success" and self.output is None:
            raise ValueError("llm_success 必须包含结构化输出")
        if self.status == "llm_success" and self.error is not None:
            raise ValueError("llm_success 不能同时包含错误")
        if self.status == "llm_failed" and self.error is None:
            raise ValueError("llm_failed 必须包含错误信息")
        return self


class KnowledgeDocumentInput(StrictModel):
    title: str = Field(min_length=2, max_length=200)
    source: str = Field(min_length=2, max_length=300)
    version: str = Field(default="v1", min_length=1, max_length=40)
    effective_date: date | None = None
    content: str = Field(min_length=10, max_length=20000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeDocumentOutput(StrictModel):
    id: str
    title: str
    source: str
    version: str
    effective_date: date | None
    active: bool
    chunk_count: int = Field(ge=0)
    created: bool = False
    created_at: datetime


class KnowledgeHit(StrictModel):
    chunk_id: str
    document_id: str
    title: str
    source: str
    version: str
    heading: str | None
    content: str
    score: float = Field(ge=0, le=1.1)
    rank: int = Field(ge=1, le=100)


class KnowledgeSearchInput(StrictModel):
    query: str = Field(min_length=2, max_length=300)
    top_k: int = Field(default=5, ge=1, le=10)


class KnowledgeSearchOutput(StrictModel):
    query: str
    hits: list[KnowledgeHit] = Field(default_factory=list, max_length=10)
    no_answer: bool = False
    embedding_model: str
    retrieval_ms: float = Field(ge=0)


class KnowledgeCitation(KnowledgeHit):
    cited: bool = False


class KnowledgeContext(StrictModel):
    query: str
    hits: list[KnowledgeCitation] = Field(default_factory=list, max_length=20)
    no_answer: bool = False
    embedding_model: str
    retrieval_ms: float = Field(ge=0)


class RecommendationInput(StrictModel):
    analysis_id: str
    report: MetricsReport
    findings: DriverReport
    llm: LLMRun | None = None
    knowledge: KnowledgeSearchOutput | None = None


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
