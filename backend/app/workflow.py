import logging
from dataclasses import replace
from time import perf_counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from sqlalchemy import select, update

from .audit import record
from .config import Settings
from .errors import DomainError
from .knowledge import build_knowledge_query, merge_knowledge_hits
from .llm import LLMError
from .llm_analysis import (
    SYSTEM_PROMPT, build_llm_payload, build_user_prompt,
    validate_evidence_references, validate_knowledge_references,
)
from .llm_tools import ToolLoopResult, run_tool_loop
from .metrics import verify_evidence
from .models import Analysis, utcnow
from .schemas import (
    DriverReport, KnowledgeSearchOutput, LLMAnalysisOutput, LLMErrorInfo, LLMRun,
    LLMUsage, MetricsInput, MetricsReport,
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
    knowledge: dict | None
    knowledge_error: dict | None
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
    "verify_evidence": "验证证据与完整性", "retrieve_knowledge": "检索运营知识库",
    "llm_root_cause_analysis": "DeepSeek 根因分析",
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
    if "knowledge" in update and "llm" not in update:
        knowledge = update.get("knowledge")
        if update.get("knowledge_error"):
            return f"知识库检索失败（{update['knowledge_error'].get('code')}），继续无知识分析"
        if knowledge and knowledge.get("hits"):
            return f"检索到 {len(knowledge['hits'])} 个知识片段；no_answer={knowledge.get('no_answer')}"
        return "知识库没有匹配片段，继续分析"
    if "llm" in update and update["llm"].get("status") != "not_requested":
        run = update["llm"]
        if run.get("status") == "llm_success":
            tools = len(run.get("tool_calls") or [])
            suffix = f"，{tools} 次工具调用" if run.get("native_tools") else ""
            return f"{run.get('model')} 完成根因分析（{run.get('latency_ms')} ms{suffix}）"
        if run.get("status") == "deterministic_fallback":
            return f"LLM 未参与（{run.get('reason') or '未配置'}），使用确定性规则"
        return "LLM 分析失败"
    if "recommendation_id" in update:
        return f"已创建建议 {update['recommendation_id']}"
    return LABELS[name]


def usage_model(usage: dict[str, int] | None) -> LLMUsage | None:
    if not usage:
        return None
    allowed = {
        key: value for key, value in usage.items()
        if key in {"prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"}
    }
    return LLMUsage(**allowed) if allowed else None


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
            "knowledge_references": run.output.knowledge_references if run.output else None,
            "confidence": run.output.confidence if run.output else None,
            "native_tools": run.native_tools, "termination_reason": run.termination_reason,
            "rounds": [item.model_dump(mode="json") for item in run.rounds],
            "tool_calls": [item.model_dump(mode="json") for item in run.tool_calls],
        }
        with factory.begin() as session:
            record(session, actor, action, "analysis", analysis_id, request_id, outcome, details)

    def persist_llm_run(run: LLMRun):
        with factory.begin() as session:
            row = session.get(Analysis, analysis_id)
            result = dict(row.result or {})
            result["llm"] = run.model_dump(mode="json")
            row.result = result

    def llm_failure(exc: LLMError, loop: ToolLoopResult | None = None):
        error = LLMErrorInfo(code=exc.code, message=exc.message, retryable=exc.retryable)
        attempts = loop.attempts if loop is not None else exc.attempts
        latency_ms = loop.latency_ms if loop is not None else exc.latency_ms
        model = loop.model if loop is not None else settings.llm_model
        trace = {
            "native_tools": loop is not None,
            "termination_reason": loop.termination_reason if loop is not None else None,
            "rounds": loop.rounds if loop is not None else [],
            "tool_calls": loop.tool_calls if loop is not None else [],
            "usage": usage_model(loop.usage) if loop is not None else None,
        }
        if settings.llm_fallback_policy == "fail":
            failed = LLMRun(
                status="llm_failed", configured=True, provider=settings.llm_provider, model=model,
                reason=exc.code, attempts=attempts, latency_ms=latency_ms, error=error, **trace,
            )
            audit_llm(failed, "analysis.llm_failed", "failure")
            persist_llm_run(failed)
            raise DomainError("llm_failed", f"LLM 调用失败且降级策略为 fail：{exc.message}", 502, {"llm_code": exc.code, "attempts": attempts})
        fallback = LLMRun(
            status="deterministic_fallback", configured=True, provider=settings.llm_provider, model=model,
            reason=exc.code, attempts=attempts, latency_ms=latency_ms, error=error, **trace,
        )
        audit_llm(fallback, "analysis.llm_fallback", "degraded")
        return {"llm": fallback.model_dump(mode="json")}

    def retrieve(state):
        report = MetricsReport.model_validate(state["report"])
        findings = DriverReport.model_validate(state["findings"])
        query = build_knowledge_query(report, findings)
        try:
            result = tool_data(
                registry, "search_knowledge",
                {"query": query, "top_k": settings.knowledge_top_k}, context,
            )
        except DomainError as exc:
            return {
                "knowledge": None,
                "knowledge_error": {"code": exc.code, "message": exc.message},
            }
        return {"knowledge": result, "knowledge_error": None}

    def llm_analyze(state):
        report = MetricsReport.model_validate(state["report"])
        findings = DriverReport.model_validate(state["findings"])
        plan = plan_action(report, findings)
        pre_knowledge = None
        if state.get("knowledge"):
            try:
                pre_knowledge = KnowledgeSearchOutput.model_validate(state["knowledge"])
            except ValidationError:
                pre_knowledge = None
        if llm is None:
            fallback = LLMRun(
                status="deterministic_fallback", configured=False, provider=settings.llm_provider,
                model=settings.llm_model, reason="not_configured",
            )
            audit_llm(fallback, "analysis.llm_fallback", "degraded")
            return {"llm": fallback.model_dump(mode="json")}
        payload = build_llm_payload(
            report, findings, plan.action.model_dump(mode="json"), knowledge=pre_knowledge,
        )

        if settings.llm_native_tools and callable(getattr(llm, "chat", None)):
            try:
                scope = MetricsInput.model_validate(state["scope"])
            except ValidationError as exc:
                return llm_failure(LLMError(
                    "llm_invalid_scope", "分析范围无法用于工具调用",
                    details={"errors": exc.errors(include_context=False)[:3]},
                ))
            loop = run_tool_loop(
                provider=llm, registry=registry, context=replace(context, scope=scope),
                payload=payload, settings=settings, scope=scope,
            )
            merged = merge_knowledge_hits(pre_knowledge, context.knowledge_hits)
            knowledge_update = {
                "knowledge": merged.model_dump(mode="json") if merged is not None else None,
            }
            if loop.error is not None:
                return {**llm_failure(loop.error, loop=loop), **knowledge_update}
            assert loop.output is not None
            run = LLMRun(
                status="llm_success", configured=True, provider=settings.llm_provider, model=loop.model,
                attempts=loop.attempts, latency_ms=loop.latency_ms, usage=usage_model(loop.usage),
                output=loop.output, native_tools=True, termination_reason=loop.termination_reason,
                rounds=loop.rounds, tool_calls=loop.tool_calls,
            )
            audit_llm(run, "analysis.llm_succeeded", "success")
            return {"llm": run.model_dump(mode="json"), **knowledge_update}

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
        run = LLMRun(
            status="llm_success", configured=True, provider=settings.llm_provider, model=result.meta.model,
            attempts=result.meta.attempts, latency_ms=result.meta.latency_ms,
            usage=usage_model(result.meta.usage), output=output,
        )
        audit_llm(run, "analysis.llm_succeeded", "success")
        return {"llm": run.model_dump(mode="json")}

    def verify_llm(state):
        run = LLMRun.model_validate(state["llm"])
        if run.status != "llm_success":
            return {}
        assert run.output is not None
        knowledge = None
        if state.get("knowledge"):
            try:
                knowledge = KnowledgeSearchOutput.model_validate(state["knowledge"])
            except ValidationError:
                knowledge = None
        unknown_evidence = validate_evidence_references(run.output, state["findings"]["evidence"])
        unknown_knowledge = validate_knowledge_references(run.output, knowledge)
        if not unknown_evidence and not unknown_knowledge:
            return {}
        if unknown_evidence:
            code = "invalid_evidence_reference"
            message = f"LLM 引用了不存在的证据：{', '.join(unknown_evidence[:5])}"
            details = {"unknown_evidence_ids": unknown_evidence}
        else:
            code = "invalid_knowledge_reference"
            message = f"LLM 引用了不存在的知识片段：{', '.join(unknown_knowledge[:5])}"
            details = {"unknown_knowledge_ids": unknown_knowledge}
        error = LLMErrorInfo(code=code, message=message)
        if settings.llm_fallback_policy == "fail":
            failed = run.model_copy(update={"status": "llm_failed", "reason": code, "error": error, "output": None})
            audit_llm(failed, "analysis.llm_failed", "failure")
            persist_llm_run(failed)
            raise DomainError("llm_failed", "LLM 引用无效，且降级策略为 fail", 502, details)
        fallback = run.model_copy(update={"status": "deterministic_fallback", "reason": code, "error": error, "output": None})
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
                        row.result = {
                            "metrics": merged["report"], "findings": merged.get("findings"),
                            "knowledge": merged.get("knowledge"),
                            "knowledge_error": merged.get("knowledge_error"),
                            "llm": llm_meta, "engine": engine,
                        }
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
        "retrieve_knowledge": retrieve,
        "llm_root_cause_analysis": llm_analyze,
        "verify_llm_evidence": verify_llm,
        "create_recommendation": lambda state: {"recommendation_id": tool_data(registry, "create_recommendation", {"analysis_id": analysis_id, "report": state["report"], "findings": state["findings"], "llm": state.get("llm"), "knowledge": state.get("knowledge")}, context)["recommendation_id"]},
        "wait_for_approval": wait,
    }
    for name, handler in handlers.items():
        graph.add_node(name, tracked(name, handler, check_timeout=name != "llm_root_cause_analysis"))
    chain = [START, "receive_request", "get_sales_metrics", "calculate_profit", "analyze_profit_drivers", "verify_evidence"]
    for before, after in zip(chain, chain[1:]):
        graph.add_edge(before, after)
    graph.add_conditional_edges("verify_evidence", lambda state: "knowledge" if state["evidence_valid"] and state["findings"]["anomaly"] else "finish", {"knowledge": "retrieve_knowledge", "finish": "wait_for_approval"})
    graph.add_edge("retrieve_knowledge", "llm_root_cause_analysis")
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
