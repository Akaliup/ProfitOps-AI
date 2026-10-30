import logging
from time import perf_counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from sqlalchemy import select, update

from .audit import record
from .config import Settings
from .errors import DomainError
from .llm import LLMError
from .llm_analysis import SYSTEM_PROMPT, build_llm_payload, build_user_prompt, validate_evidence_references
from .metrics import verify_evidence
from .models import Analysis, utcnow
from .schemas import (
    DriverReport, LLMAnalysisOutput, LLMErrorInfo, LLMRun, LLMUsage, MetricsReport,
)
from .security import Actor
from .services import plan_action
from .tools import ToolContext

logger = logging.getLogger("profitops.workflow")


class ProfitState(TypedDict, total=False):
    analysis_id: str
    scope: dict
    raw: dict
    report: dict
    findings: dict
    evidence_valid: bool
    llm: dict
    recommendation_id: str
    status: str
    error: dict
    approval: dict
    execution: dict
    idempotency_key: str


LABELS = {
    "receive_request": "接收分析请求", "get_sales_metrics": "读取经营数据",
    "calculate_profit": "计算利润与环比", "analyze_profit_drivers": "分析异常与贡献项",
    "verify_evidence": "验证证据与完整性", "llm_root_cause_analysis": "DeepSeek 根因分析",
    "verify_llm_evidence": "验证 LLM 证据引用", "create_recommendation": "创建待审批建议",
    "wait_for_approval": "等待人工审批", "get_approval_status": "检查审批状态",
    "simulate_execute_approved_action": "受控模拟执行", "save_execution_result": "保存结果与审计",
}


def tool_data(registry, name, arguments, context):
    result = registry.invoke(name, arguments, context)
    if not result.ok:
        error = result.error
        raise DomainError(error.code, error.message, 409 if error.code in {"not_approved", "idempotency_conflict"} else 500, error.details)
    return result.data


def result_summary(name, update):
    if "raw" in update:
        return f"读取 {update['raw']['data_quality']['observed_records']} 条每日经营记录"
    if "report" in update:
        return f"净利润 {update['report']['current']['net_profit']} 美分；利润桥核对 {update['report']['bridge_verified']}"
    if "findings" in update:
        return update["findings"]["conclusion"]
    if "llm" in update and update["llm"].get("status") != "not_requested":
        run = update["llm"]
        if run.get("status") == "llm_success":
            return f"{run.get('model')} 完成根因分析（{run.get('latency_ms')} ms）"
        if run.get("status") == "deterministic_fallback":
            return f"LLM 未参与（{run.get('reason') or '未配置'}），使用确定性规则"
        return "LLM 分析失败"
    if "recommendation_id" in update:
        return f"已创建建议 {update['recommendation_id']}"
    return LABELS[name]


def run_analysis(factory, registry, analysis_id: str, settings: Settings, llm=None):
    with factory.begin() as session:
        claim = session.execute(update(Analysis).where(Analysis.id == analysis_id, Analysis.status == "queued").values(status="running"))
        if claim.rowcount != 1:
            return
        analysis = session.get(Analysis, analysis_id)
        actor = Actor(analysis.actor, "analyst")
        request_id = analysis.request_id
        initial = ProfitState(analysis_id=analysis_id, scope=analysis.request, status="running")
    context = ToolContext(factory, actor, request_id)
    timeout_seconds = settings.step_timeout_seconds

    def audit_llm(run: LLMRun, action: str, outcome: str):
        details = {
            "status": run.status, "configured": run.configured, "provider": run.provider, "model": run.model,
            "reason": run.reason, "attempts": run.attempts, "latency_ms": run.latency_ms,
            "usage": run.usage.model_dump() if run.usage else None,
            "error": run.error.model_dump() if run.error else None,
            "evidence_references": run.output.evidence_references if run.output else None,
            "confidence": run.output.confidence if run.output else None,
        }
        with factory.begin() as session:
            record(session, actor, action, "analysis", analysis_id, request_id, outcome, details)

    def persist_llm_run(run: LLMRun):
        with factory.begin() as session:
            row = session.get(Analysis, analysis_id)
            result = dict(row.result or {})
            result["llm"] = run.model_dump(mode="json")
            row.result = result

    def llm_failure(exc: LLMError):
        error = LLMErrorInfo(code=exc.code, message=exc.message, retryable=exc.retryable)
        if settings.llm_fallback_policy == "fail":
            failed = LLMRun(
                status="llm_failed", configured=True, provider=settings.llm_provider, model=settings.llm_model,
                reason=exc.code, attempts=exc.attempts, latency_ms=exc.latency_ms, error=error,
            )
            audit_llm(failed, "analysis.llm_failed", "failure")
            persist_llm_run(failed)
            raise DomainError("llm_failed", f"LLM 调用失败且降级策略为 fail：{exc.message}", 502, {"llm_code": exc.code, "attempts": exc.attempts})
        fallback = LLMRun(
            status="deterministic_fallback", configured=True, provider=settings.llm_provider, model=settings.llm_model,
            reason=exc.code, attempts=exc.attempts, latency_ms=exc.latency_ms, error=error,
        )
        audit_llm(fallback, "analysis.llm_fallback", "degraded")
        return {"llm": fallback.model_dump(mode="json")}

    def llm_analyze(state):
        report = MetricsReport.model_validate(state["report"])
        findings = DriverReport.model_validate(state["findings"])
        plan = plan_action(report, findings)
        if llm is None:
            fallback = LLMRun(
                status="deterministic_fallback", configured=False, provider=settings.llm_provider,
                model=settings.llm_model, reason="not_configured",
            )
            audit_llm(fallback, "analysis.llm_fallback", "degraded")
            return {"llm": fallback.model_dump(mode="json")}
        payload = build_llm_payload(report, findings, plan.action.model_dump(mode="json"))
        try:
            result = llm.generate_json(
                system=SYSTEM_PROMPT, user=build_user_prompt(payload), max_tokens=settings.llm_max_tokens
            )
            output = LLMAnalysisOutput.model_validate(result.data)
        except LLMError as exc:
            return llm_failure(exc)
        except ValidationError as exc:
            error = LLMError(
                "llm_schema_invalid", "LLM 输出不符合结构化 Schema",
                details={"errors": exc.errors(include_context=False)[:5]},
            )
            return llm_failure(error)
        usage = None
        if result.meta.usage:
            allowed = {key: value for key, value in result.meta.usage.items() if key in {"prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"}}
            usage = LLMUsage(**allowed) if allowed else None
        run = LLMRun(
            status="llm_success", configured=True, provider=settings.llm_provider, model=result.meta.model,
            attempts=result.meta.attempts, latency_ms=result.meta.latency_ms, usage=usage, output=output,
        )
        audit_llm(run, "analysis.llm_succeeded", "success")
        return {"llm": run.model_dump(mode="json")}

    def verify_llm(state):
        run = LLMRun.model_validate(state["llm"])
        if run.status != "llm_success":
            return {}
        assert run.output is not None
        unknown = validate_evidence_references(run.output, state["findings"]["evidence"])
        if not unknown:
            return {}
        error = LLMErrorInfo(
            code="invalid_evidence_reference",
            message=f"LLM 引用了不存在的证据：{', '.join(unknown[:5])}",
        )
        if settings.llm_fallback_policy == "fail":
            failed = run.model_copy(update={"status": "llm_failed", "reason": "invalid_evidence_reference", "error": error, "output": None})
            audit_llm(failed, "analysis.llm_failed", "failure")
            persist_llm_run(failed)
            raise DomainError("llm_failed", "LLM 证据引用无效，且降级策略为 fail", 502, {"unknown_evidence_ids": unknown})
        fallback = run.model_copy(update={"status": "deterministic_fallback", "reason": "invalid_evidence_reference", "error": error, "output": None})
        audit_llm(fallback, "analysis.llm_fallback", "degraded")
        return {"llm": fallback.model_dump(mode="json")}

    def tracked(name, handler, check_timeout=True):
        def node(state):
            start = perf_counter()
            step = {"name": name, "label": LABELS[name], "status": "running", "started_at": utcnow().isoformat(), "request_id": request_id, "phase": "analysis"}
            with factory.begin() as session:
                row = session.get(Analysis, analysis_id)
                row.steps = [*row.steps, step]
            try:
                updates = handler(state)
                duration = round((perf_counter() - start) * 1000, 3)
                if check_timeout and duration > timeout_seconds * 1000:
                    raise DomainError("step_timeout", f"步骤 {name} 超过 {timeout_seconds}s 预算", 504)
                step = {**step, "status": "completed", "duration_ms": duration, "summary": result_summary(name, updates)}
                merged = {**state, **updates}
                with factory.begin() as session:
                    row = session.get(Analysis, analysis_id)
                    row.steps = [*row.steps[:-1], step]
                    row.snapshot = merged
                    row.status = merged.get("status", "running")
                    if "report" in merged:
                        llm_meta = merged.get("llm")
                        engine = "deepseek-llm-v1" if llm_meta and llm_meta.get("status") == "llm_success" else "deterministic-rules-v1"
                        row.result = {"metrics": merged["report"], "findings": merged.get("findings"), "llm": llm_meta, "engine": engine}
                    row.recommendation_id = merged.get("recommendation_id")
                    row.error = merged.get("error")
                return updates
            except Exception as exc:
                with factory.begin() as session:
                    row = session.get(Analysis, analysis_id)
                    row.steps = [*row.steps[:-1], {**step, "status": "failed", "duration_ms": round((perf_counter() - start) * 1000, 3), "summary": str(exc)}]
                raise
        return node

    def verify(state):
        report = MetricsReport.model_validate(state["report"])
        findings = DriverReport.model_validate(state["findings"])
        not_requested = LLMRun(
            status="not_requested", configured=llm is not None,
            provider=settings.llm_provider, model=settings.llm_model,
        )
        if not report.data_quality.complete:
            return {
                "evidence_valid": False, "status": "insufficient_data",
                "llm": not_requested.model_copy(update={"reason": "insufficient_data"}).model_dump(mode="json"),
                "error": {"code": "insufficient_data", "message": "数据不完整，已停止建议生成", "details": report.data_quality.model_dump()},
            }
        verify_evidence(report, findings)
        if not findings.anomaly:
            return {"evidence_valid": True, "llm": not_requested.model_copy(update={"reason": "no_anomaly"}).model_dump(mode="json")}
        return {"evidence_valid": True}

    def wait(state):
        status = "waiting_approval" if state.get("recommendation_id") else state.get("status", "completed")
        if status == "running":
            status = "completed"
        with factory.begin() as session:
            record(session, actor, "analysis.finished", "analysis", analysis_id, request_id,
                   "insufficient_data" if status == "insufficient_data" else "success", {"status": status})
        return {"status": status}

    graph = StateGraph(ProfitState)
    handlers = {
        "receive_request": lambda state: {},
        "get_sales_metrics": lambda state: {"raw": tool_data(registry, "get_sales_metrics", state["scope"], context)},
        "calculate_profit": lambda state: {"report": tool_data(registry, "calculate_profit", {"metrics": state["raw"]}, context)},
        "analyze_profit_drivers": lambda state: {"findings": tool_data(registry, "analyze_profit_drivers", {"report": state["report"]}, context)},
        "verify_evidence": verify,
        "llm_root_cause_analysis": llm_analyze,
        "verify_llm_evidence": verify_llm,
        "create_recommendation": lambda state: {"recommendation_id": tool_data(registry, "create_recommendation", {"analysis_id": analysis_id, "report": state["report"], "findings": state["findings"], "llm": state.get("llm")}, context)["recommendation_id"]},
        "wait_for_approval": wait,
    }
    for name, handler in handlers.items():
        graph.add_node(name, tracked(name, handler, check_timeout=name != "llm_root_cause_analysis"))
    chain = [START, "receive_request", "get_sales_metrics", "calculate_profit", "analyze_profit_drivers", "verify_evidence"]
    for before, after in zip(chain, chain[1:]):
        graph.add_edge(before, after)
    graph.add_conditional_edges("verify_evidence", lambda state: "llm" if state["evidence_valid"] and state["findings"]["anomaly"] else "finish", {"llm": "llm_root_cause_analysis", "finish": "wait_for_approval"})
    graph.add_edge("llm_root_cause_analysis", "verify_llm_evidence")
    graph.add_edge("verify_llm_evidence", "create_recommendation")
    graph.add_edge("create_recommendation", "wait_for_approval")
    graph.add_edge("wait_for_approval", END)
    try:
        graph.compile().invoke(initial, {"recursion_limit": 20})
    except Exception as exc:
        error = {"code": exc.code if isinstance(exc, DomainError) else "workflow_failed", "message": str(exc), "exception_type": type(exc).__name__}
        with factory.begin() as session:
            row = session.get(Analysis, analysis_id)
            row.status, row.error = "failed", error
            record(session, actor, "analysis.failed", "analysis", analysis_id, request_id, "failure", error)
        logger.exception("analysis_failed", extra={"analysis_id": analysis_id, "request_id": request_id})


def run_execution_graph(factory, registry, session, recommendation, key, actor, request_id):
    analysis = session.get(Analysis, recommendation.analysis_id)
    initial = ProfitState(**{**analysis.snapshot, "analysis_id": analysis.id, "recommendation_id": recommendation.id, "idempotency_key": key})
    context = ToolContext(factory, actor, request_id, session)
    steps = []

    def tracked(name, handler):
        def node(state):
            start = perf_counter()
            update_value = handler(state)
            steps.append({"name": name, "label": LABELS[name], "phase": "execution", "status": "completed", "started_at": utcnow().isoformat(), "duration_ms": round((perf_counter() - start) * 1000, 3), "summary": LABELS[name], "request_id": request_id})
            return update_value
        return node

    graph = StateGraph(ProfitState)
    graph.add_node("get_approval_status", tracked("get_approval_status", lambda s: {"approval": tool_data(registry, "get_approval_status", {"recommendation_id": recommendation.id}, context)}))
    graph.add_node("simulate_execute_approved_action", tracked("simulate_execute_approved_action", lambda s: {"execution": tool_data(registry, "simulate_execute_approved_action", {"recommendation_id": recommendation.id, "idempotency_key": key}, context)}))
    graph.add_node("save_execution_result", tracked("save_execution_result", lambda s: {"status": s["execution"]["status"]}))
    graph.add_edge(START, "get_approval_status")
    graph.add_edge("get_approval_status", "simulate_execute_approved_action")
    graph.add_edge("simulate_execute_approved_action", "save_execution_result")
    graph.add_edge("save_execution_result", END)
    result = graph.compile().invoke(initial)
    session.refresh(analysis)
    analysis.snapshot = result
    analysis.steps = [*analysis.steps, *steps]
    analysis.status = result["status"]
    return result["execution"]


def recover_interrupted(factory):
    with factory.begin() as session:
        rows = session.scalars(select(Analysis).where(Analysis.status.in_(["queued", "running"]))).all()
        for row in rows:
            row.status = "failed"
            row.error = {"code": "restart_interrupted", "message": "服务重启中断了分析，请用新的请求键重新发起"}
            record(session, "system", "analysis.interrupted", "analysis", row.id, row.request_id, "failure", row.error)
