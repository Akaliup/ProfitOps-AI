from dataclasses import dataclass
from math import ceil
from typing import Literal

from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .audit import record
from .errors import DomainError, LeaseLost
from .metrics import verify_evidence
from .models import Analysis, Execution, Recommendation, new_id, utcnow
from .schemas import (
    DriverReport, ExecuteOutput, KnowledgeCitation, KnowledgeContext, MetricsReport,
    RecommendationInput, RecommendationOutput, StrictModel,
)
from .security import Actor, require_role, scoped_idempotency_key


class SimulationAction(StrictModel):
    type: Literal["adjust_ad_budget", "create_review_task"]
    simulation: Literal[True] = True
    product_id: str
    sku: str
    adjustment_pct: int = Field(default=15, ge=1, le=30)
    baseline_daily_budget: int = Field(ge=0)
    proposed_daily_budget: int = Field(ge=0)
    source_period: dict


@dataclass
class ActionPlan:
    action: SimulationAction
    target: dict
    days: int
    is_budget: bool
    potential: int


def plan_action(report: MetricsReport, findings: DriverReport) -> ActionPlan:
    """Deterministically choose the simulated action; the LLM never computes amounts."""
    primary = findings.primary_driver
    if primary is None:
        raise DomainError("no_primary_driver", "没有可执行的主要贡献项", 409)
    candidates = report.sku_breakdown
    if not candidates:
        raise DomainError("no_sku_data", "缺少 SKU 明细，无法规划动作", 409)
    target = max(candidates, key=lambda row: row["ad_spend_delta"]) if primary.metric == "ad_spend" else min(candidates, key=lambda row: row["profit_delta"])
    days = (report.period.end_date - report.period.start_date).days + 1
    daily_budget = ceil(target["current"]["ad_spend"] / days)
    is_budget = primary.metric == "ad_spend"
    proposed = daily_budget * 85 // 100 if is_budget else daily_budget
    potential = (daily_budget - proposed) * days if is_budget else 0
    action = SimulationAction(
        type="adjust_ad_budget" if is_budget else "create_review_task", product_id=target["product_id"], sku=target["sku"],
        baseline_daily_budget=daily_budget, proposed_daily_budget=proposed,
        source_period=report.period.model_dump(mode="json"),
    )
    return ActionPlan(action=action, target=target, days=days, is_budget=is_budget, potential=potential)


def create_recommendation(
    session: Session, payload: RecommendationInput, actor: Actor, request_id: str,
    worker_id: str | None = None, expected_attempt: int | None = None,
):
    require_role(actor, "analyst", "approver")
    verify_evidence(payload.report, payload.findings)
    if not payload.findings.anomaly:
        raise DomainError("no_anomaly", "没有待处理的利润异常", 409)
    analysis = session.scalar(select(Analysis).where(
        Analysis.id == payload.analysis_id, Analysis.tenant_id == actor.tenant_id,
    ))
    if analysis is None:
        raise DomainError("analysis_not_found", "分析不存在", 404)
    if worker_id is not None:
        if expected_attempt is None:
            raise LeaseLost("analysis lease attempt missing")
        # Lock the analysis row in this same transaction before writing. A
        # worker whose lease was reclaimed fails here, so it cannot insert a
        # recommendation first and discover the lost lease afterwards.
        owned = session.execute(
            update(Analysis)
            .where(
                Analysis.id == analysis.id,
                Analysis.status == "running",
                Analysis.worker_id == worker_id,
                Analysis.attempts == expected_attempt,
            )
            .values(updated_at=utcnow())
        )
        if owned.rowcount != 1:
            raise LeaseLost("analysis lease lost")
    existing = session.scalar(select(Recommendation).where(
        Recommendation.analysis_id == analysis.id,
        Recommendation.tenant_id == actor.tenant_id,
    ))
    if existing:
        return RecommendationOutput(recommendation_id=existing.id, status=existing.status)
    report, findings = payload.report, payload.findings
    primary = findings.primary_driver
    plan = plan_action(report, findings)
    target, action, days, is_budget, potential = plan.target, plan.action, plan.days, plan.is_budget, plan.potential
    llm_output = payload.llm.output if payload.llm and payload.llm.status == "llm_success" else None
    risks = ["降低广告预算可能减少流量和转化，实际利润可能不升反降。", "会计贡献项只解释利润变化，不能替代因果实验。"]
    missing = ["广告活动级 ROAS、边际获客成本与转化弹性", "自然流量占比与库存补货周期"]
    if llm_output:
        risks = list(dict.fromkeys([*risks, *llm_output.risks]))
        missing = list(dict.fromkeys([*missing, *llm_output.missing_information]))
    knowledge_context = None
    if payload.knowledge is not None:
        cited = set(llm_output.knowledge_references) if llm_output else set()
        knowledge_context = KnowledgeContext(
            query=payload.knowledge.query,
            hits=[
                KnowledgeCitation(**hit.model_dump(), cited=hit.chunk_id in cited)
                for hit in payload.knowledge.hits
            ],
            no_answer=payload.knowledge.no_answer,
            embedding_model=payload.knowledge.embedding_model,
            retrieval_ms=payload.knowledge.retrieval_ms,
        ).model_dump(mode="json")
    body = {
        "problem": llm_output.summary if llm_output else findings.conclusion,
        "period": report.period.model_dump(mode="json"),
        "previous_period": report.previous_period.model_dump(mode="json"),
        "store_id": report.store_id, "currency": "USD",
        "engine": "deepseek-llm-v1" if llm_output else "deterministic-rules-v1",
        "llm": payload.llm.model_dump(mode="json") if payload.llm else None,
        "knowledge": knowledge_context,
        "evidence": findings.evidence, "primary_driver": primary.model_dump(),
        "recommendation": f"针对 {target['sku']} 将参考日广告预算下调 15%，先进行受控模拟并在 7 天后复核。" if is_budget else f"为 {target['sku']} 创建成本核查任务，核对 {primary.label} 原始账单。",
        "expected_impact": {
            "kind": "conditional_scenario", "horizon_days": days,
            "profit_improvement_min": potential // 2, "profit_improvement_max": potential,
            "explanation": "假设未来日均广告花费等于本周期均值、预算完全用尽且收入与其他成本不变，节省额兑现 50%–100%。这是情景测算，未证明广告弹性，也不是已实现利润。" if is_budget else "核查任务不直接承诺利润改善。",
        },
        "risks": risks,
        "missing_information": missing,
        "assumptions": ["USD 单币种；日期按 UTC 业务日统计", "参考日预算来自周期广告花费均值，并非平台已配置预算"],
        "source": report.source,
    }
    recommendation = Recommendation(
        id=new_id(), tenant_id=actor.tenant_id, analysis_id=analysis.id, status="proposed",
        title=f"{target['sku']} · 广告预算优化模拟" if is_budget else f"{target['sku']} · 成本核查模拟",
        body=body, action=action.model_dump(mode="json"),
    )
    session.add(recommendation)
    session.flush()
    record(session, actor, "recommendation.proposed", "recommendation", recommendation.id, request_id)
    recommendation.status = "pending_approval"
    record(session, actor, "recommendation.pending_approval", "recommendation", recommendation.id, request_id)
    return RecommendationOutput(recommendation_id=recommendation.id, status=recommendation.status)


def decide(session: Session, recommendation_id: str, decision: str, reason: str, actor: Actor, request_id: str):
    require_role(actor, "approver")
    status = "approved" if decision == "approve" else "rejected"
    changed = session.execute(update(Recommendation).where(
        Recommendation.id == recommendation_id, Recommendation.status == "pending_approval",
        Recommendation.tenant_id == actor.tenant_id,
    ).values(status=status, approved_by=actor.id if status == "approved" else None, approval_reason=reason))
    if changed.rowcount != 1:
        if session.scalar(select(Recommendation).where(
            Recommendation.id == recommendation_id, Recommendation.tenant_id == actor.tenant_id,
        )) is None:
            raise DomainError("recommendation_not_found", "建议不存在", 404)
        raise DomainError("invalid_transition", "只有待审批建议可以批准或拒绝", 409)
    recommendation = session.get(Recommendation, recommendation_id)
    session.refresh(recommendation)
    analysis = session.scalar(select(Analysis).where(
        Analysis.id == recommendation.analysis_id, Analysis.tenant_id == actor.tenant_id,
    ))
    if analysis is None:
        raise DomainError("analysis_not_found", "分析不存在", 404)
    analysis.status = "approved" if status == "approved" else "rejected"
    record(session, actor, f"recommendation.{decision}", "recommendation", recommendation_id, request_id, details={"reason": reason, "status": status})
    return recommendation


def simulate_action(action: SimulationAction) -> dict:
    if action.type == "adjust_ad_budget":
        if action.proposed_daily_budget > action.baseline_daily_budget:
            raise ValueError("模拟预算不能高于参考预算")
        return {
            "simulation": True, "message": "模拟执行完成：已生成预算变更预览。真实广告平台未发生变更。",
            "action_type": action.type, "sku": action.sku,
            "before": {"daily_budget": action.baseline_daily_budget},
            "after": {"daily_budget": action.proposed_daily_budget}, "currency": "USD",
            "side_effect_count": 1, "external_calls": 0,
        }
    return {"simulation": True, "message": "模拟执行完成：已生成成本核查任务预览。", "sku": action.sku, "action_type": action.type, "side_effect_count": 1, "external_calls": 0}


def execution_output(execution: Execution, replayed=False):
    return ExecuteOutput(
        execution_id=execution.id, recommendation_id=execution.recommendation_id,
        status=execution.status, simulation=execution.simulation,
        result=execution.result, error=execution.error, replayed=replayed,
    )


def execute_once(session: Session, recommendation_id: str, key: str, actor: Actor, request_id: str) -> ExecuteOutput:
    require_role(actor, "approver")
    key = scoped_idempotency_key(actor.tenant_id, key)
    # First write serializes competing transactions on SQLite and locks the row on PostgreSQL.
    claimed = session.execute(update(Recommendation).where(
        Recommendation.id == recommendation_id, Recommendation.status == "approved",
        Recommendation.tenant_id == actor.tenant_id,
    ).values(status="executing"))
    occupied = session.scalar(select(Execution).where(
        Execution.idempotency_key == key, Execution.tenant_id == actor.tenant_id,
    ))
    if occupied and occupied.recommendation_id != recommendation_id:
        raise DomainError("idempotency_conflict", "该执行请求键已用于其他建议", 409)
    if claimed.rowcount != 1:
        existing = session.scalar(select(Execution).where(
            Execution.recommendation_id == recommendation_id,
            Execution.tenant_id == actor.tenant_id,
        ))
        if existing:
            record(session, actor, "execution.replayed", "recommendation", recommendation_id, request_id, "replayed", {"execution_id": existing.id})
            return execution_output(existing, replayed=True)
        recommendation = session.scalar(select(Recommendation).where(
            Recommendation.id == recommendation_id, Recommendation.tenant_id == actor.tenant_id,
        ))
        if recommendation is None:
            raise DomainError("recommendation_not_found", "建议不存在", 404)
        raise DomainError("not_approved", "只有已批准的建议可以模拟执行", 409, {"status": recommendation.status})
    recommendation = session.get(Recommendation, recommendation_id)
    session.refresh(recommendation)
    execution = Execution(
        id=new_id(), tenant_id=actor.tenant_id, recommendation_id=recommendation_id, idempotency_key=key,
        status="executing", actor=actor.id, request_id=request_id, simulation=True,
    )
    session.add(execution)
    session.flush()
    record(session, actor, "execution.started", "recommendation", recommendation_id, request_id, details={"simulation": True, "execution_id": execution.id})
    try:
        action = SimulationAction.model_validate(recommendation.action)
        execution.result = simulate_action(action)
        execution.status = recommendation.status = "executed"
        record(session, actor, "execution.executed", "recommendation", recommendation_id, request_id, details=execution.result)
    except Exception as exc:
        execution.status = recommendation.status = "execution_failed"
        execution.error = {"code": "simulation_failed", "message": str(exc), "exception_type": type(exc).__name__}
        record(session, actor, "execution.failed", "recommendation", recommendation_id, request_id, "failure", execution.error)
    analysis = session.scalar(select(Analysis).where(
        Analysis.id == recommendation.analysis_id, Analysis.tenant_id == actor.tenant_id,
    ))
    if analysis is None:
        raise DomainError("analysis_not_found", "分析不存在", 404)
    analysis.status = execution.status
    session.flush()
    return execution_output(execution)
