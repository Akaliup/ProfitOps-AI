from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from .errors import DomainError
from .metrics import analyze_drivers, build_report, fetch_metrics
from .models import Recommendation
from .schemas import (
    ApprovalInput, ApprovalOutput, CalculateInput, DriverReport, DriversInput,
    ExecuteInput, ExecuteOutput, MetricsInput, MetricsReport, ProfitAnalysisOutput,
    ProfitSnapshot, RawMetrics, RecommendationInput, RecommendationOutput,
    SkuBreakdownOutput, SkuBreakdownRow, ToolFailure, ToolResult,
)
from .security import Actor, require_role
from .services import create_recommendation, execute_once


@dataclass
class ToolContext:
    factory: Callable
    actor: Actor
    request_id: str
    session: Session | None = None
    # Set for model-driven calls: the LLM may only query the analysis scope it
    # was given, never an arbitrary period or store.
    scope: MetricsInput | None = None


Policy = Literal["safe", "ask", "deny"]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    roles: tuple[str, ...]
    effect: str
    policy: Policy
    handler: Callable


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec):
        if spec.name in self.tools:
            raise ValueError(f"Duplicate tool: {spec.name}")
        self.tools[spec.name] = spec

    def invoke(self, name: str, arguments: dict, context: ToolContext) -> ToolResult:
        spec = self.tools.get(name)
        if spec is None:
            return ToolResult(ok=False, error=ToolFailure(code="unknown_tool", message="工具不存在"))
        if spec.policy == "deny":
            return ToolResult(ok=False, error=ToolFailure(code="policy_denied", message="该工具被安全策略禁止调用"))
        try:
            require_role(context.actor, *spec.roles)
            payload = spec.input_model.model_validate(arguments)
        except ValidationError as exc:
            return ToolResult(ok=False, error=ToolFailure(code="invalid_tool_input", message="工具参数校验失败", details=exc.errors(include_context=False)))
        except DomainError as exc:
            return ToolResult(ok=False, error=ToolFailure(code=exc.code, message=exc.message, details=exc.details))
        try:
            output = spec.output_model.model_validate(spec.handler(payload, context))
            return ToolResult(ok=True, data=output.model_dump(mode="json"))
        except DomainError as exc:
            return ToolResult(ok=False, error=ToolFailure(code=exc.code, message=exc.message, details=exc.details))
        except Exception as exc:
            return ToolResult(ok=False, error=ToolFailure(code="tool_execution_failed", message=str(exc), details={"exception_type": type(exc).__name__}))

    def catalog(self):
        return [{
            "name": spec.name, "description": spec.description, "roles": list(spec.roles),
            "effect": spec.effect, "policy": spec.policy,
            "input_schema": spec.input_model.model_json_schema(), "output_schema": spec.output_model.model_json_schema(),
        } for spec in self.tools.values()]


def get_metrics(payload, context):
    with context.factory() as session:
        return fetch_metrics(session, payload)


def profit_analysis(payload, context):
    """Return a compact trusted report plus deterministic findings.

    The model supplies only a period/store selector. Every number in the result
    comes from ``fetch_metrics``/``build_report``/``analyze_drivers``.
    """
    with context.factory() as session:
        report = build_report(fetch_metrics(session, payload))
    snapshot = ProfitSnapshot(
        period=report.period, previous_period=report.previous_period,
        store_id=report.store_id, currency=report.currency,
        current=report.current, previous=report.previous, changes=report.changes,
        drivers=report.drivers, anomaly=report.anomaly,
        bridge_verified=report.bridge_verified, data_quality=report.data_quality,
    )
    return ProfitAnalysisOutput(report=snapshot, findings=analyze_drivers(report))


def sku_breakdown(payload, context):
    with context.factory() as session:
        report = build_report(fetch_metrics(session, payload))
    rows = [
        SkuBreakdownRow(
            product_id=row["product_id"], sku=row["sku"], name=row["name"],
            net_profit_current=row["current"]["net_profit"],
            net_profit_previous=row["previous"]["net_profit"],
            profit_delta=row["profit_delta"], ad_spend_delta=row["ad_spend_delta"],
            inventory=row["inventory"], inbound_inventory=row["inbound_inventory"],
        )
        for row in report.sku_breakdown
    ]
    return SkuBreakdownOutput(period=report.period, store_id=report.store_id, rows=rows)


def create(payload, context):
    with context.factory.begin() as session:
        return create_recommendation(session, payload, context.actor, context.request_id)


def approval(payload, context):
    def read(session):
        row = session.get(Recommendation, payload.recommendation_id)
        if row is None:
            raise DomainError("recommendation_not_found", "建议不存在", 404)
        return ApprovalOutput(recommendation_id=row.id, status=row.status, approved_by=row.approved_by)
    if context.session is not None:
        return read(context.session)
    with context.factory() as session:
        return read(session)


def execute(payload, context):
    if context.session is None:
        raise DomainError("transaction_required", "执行工具必须处于受控事务中", 500)
    return execute_once(context.session, payload.recommendation_id, payload.idempotency_key, context.actor, context.request_id)


def build_registry():
    registry = ToolRegistry()
    specs = [
        ToolSpec(name="get_sales_metrics", description="读取订单与每日经营记录，报告数据覆盖", input_model=MetricsInput, output_model=RawMetrics, roles=("analyst", "approver"), effect="read", policy="safe", handler=get_metrics),
        ToolSpec(name="calculate_profit", description="按整数美分计算利润、环比与贡献桥", input_model=CalculateInput, output_model=MetricsReport, roles=("analyst", "approver"), effect="pure", policy="safe", handler=lambda p, c: build_report(p.metrics)),
        ToolSpec(name="analyze_profit_drivers", description="识别利润异常及可追溯会计贡献项", input_model=DriversInput, output_model=DriverReport, roles=("analyst", "approver"), effect="pure", policy="safe", handler=lambda p, c: analyze_drivers(p.report)),
        ToolSpec(name="get_profit_analysis", description="读取指定周期的服务端可信利润报告、驱动与证据；模型只能提供周期与店铺，不能提交原始指标", input_model=MetricsInput, output_model=ProfitAnalysisOutput, roles=("analyst", "approver"), effect="read", policy="safe", handler=profit_analysis),
        ToolSpec(name="get_sku_breakdown", description="读取指定周期的服务端可信 SKU 利润与广告变化明细", input_model=MetricsInput, output_model=SkuBreakdownOutput, roles=("analyst", "approver"), effect="read", policy="safe", handler=sku_breakdown),
        ToolSpec(name="create_recommendation", description="验证证据并创建待审批建议", input_model=RecommendationInput, output_model=RecommendationOutput, roles=("analyst", "approver"), effect="database_write", policy="safe", handler=create),
        ToolSpec(name="get_approval_status", description="读取服务端审批状态", input_model=ApprovalInput, output_model=ApprovalOutput, roles=("analyst", "approver"), effect="read", policy="safe", handler=approval),
        ToolSpec(name="simulate_execute_approved_action", description="事务内幂等地模拟已批准动作，无外部调用", input_model=ExecuteInput, output_model=ExecuteOutput, roles=("approver",), effect="simulation_only", policy="ask", handler=execute),
    ]
    for spec in specs:
        registry.register(spec)
    return registry
