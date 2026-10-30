import logging
from time import perf_counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select, update

from .audit import record
from .errors import DomainError
from .metrics import verify_evidence
from .models import Analysis, utcnow
from .schemas import DriverReport, MetricsReport
from .security import Actor
from .tools import ToolContext

logger = logging.getLogger("profitops.workflow")


class ProfitState(TypedDict, total=False):
    analysis_id: str
    scope: dict
    raw: dict
    report: dict
    findings: dict
    evidence_valid: bool
    recommendation_id: str
    status: str
    error: dict
    approval: dict
    execution: dict
    idempotency_key: str


LABELS = {
    "receive_request": "接收分析请求", "get_sales_metrics": "读取经营数据",
    "calculate_profit": "计算利润与环比", "analyze_profit_drivers": "分析异常与贡献项",
    "verify_evidence": "验证证据与完整性", "create_recommendation": "创建待审批建议",
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
    if "recommendation_id" in update:
        return f"已创建建议 {update['recommendation_id']}"
    return LABELS[name]


def run_analysis(factory, registry, analysis_id: str, timeout_seconds: float):
    with factory.begin() as session:
        claim = session.execute(update(Analysis).where(Analysis.id == analysis_id, Analysis.status == "queued").values(status="running"))
        if claim.rowcount != 1:
            return
        analysis = session.get(Analysis, analysis_id)
        actor = Actor(analysis.actor, "analyst")
        request_id = analysis.request_id
        initial = ProfitState(analysis_id=analysis_id, scope=analysis.request, status="running")
    context = ToolContext(factory, actor, request_id)

    def tracked(name, handler):
        def node(state):
            start = perf_counter()
            step = {"name": name, "label": LABELS[name], "status": "running", "started_at": utcnow().isoformat(), "request_id": request_id, "phase": "analysis"}
            with factory.begin() as session:
                row = session.get(Analysis, analysis_id)
                row.steps = [*row.steps, step]
            try:
                updates = handler(state)
                duration = round((perf_counter() - start) * 1000, 3)
                if duration > timeout_seconds * 1000:
                    raise DomainError("step_timeout", f"步骤 {name} 超过 {timeout_seconds}s 预算", 504)
                step = {**step, "status": "completed", "duration_ms": duration, "summary": result_summary(name, updates)}
                merged = {**state, **updates}
                with factory.begin() as session:
                    row = session.get(Analysis, analysis_id)
                    row.steps = [*row.steps[:-1], step]
                    row.snapshot = merged
                    row.status = merged.get("status", "running")
                    if "report" in merged:
                        row.result = {"metrics": merged["report"], "findings": merged.get("findings"), "engine": "deterministic-rules-v1"}
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
        if not report.data_quality.complete:
            return {"evidence_valid": False, "status": "insufficient_data", "error": {"code": "insufficient_data", "message": "数据不完整，已停止建议生成", "details": report.data_quality.model_dump()}}
        verify_evidence(report, findings)
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
        "create_recommendation": lambda state: {"recommendation_id": tool_data(registry, "create_recommendation", {"analysis_id": analysis_id, "report": state["report"], "findings": state["findings"]}, context)["recommendation_id"]},
        "wait_for_approval": wait,
    }
    for name, handler in handlers.items():
        graph.add_node(name, tracked(name, handler))
    chain = [START, "receive_request", "get_sales_metrics", "calculate_profit", "analyze_profit_drivers", "verify_evidence"]
    for before, after in zip(chain, chain[1:]):
        graph.add_edge(before, after)
    graph.add_conditional_edges("verify_evidence", lambda state: "create" if state["evidence_valid"] and state["findings"]["anomaly"] else "finish", {"create": "create_recommendation", "finish": "wait_for_approval"})
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
