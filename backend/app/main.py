import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import date
from time import perf_counter
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from .audit import record, record_separately
from .config import Settings, get_settings
from .db import build_engine, initialize_database, make_session_factory
from .errors import DomainError
from .metrics import build_report, fetch_metrics, resolve_scope
from .models import Analysis, AuditLog, DailyOperation, Execution, Recommendation, Store, new_id
from .schemas import AnalysisRequest, DecisionRequest
from .security import Actor, get_actor, require_role
from .seed import seed_demo
from .services import decide
from .tools import build_registry
from .views import analysis_view, audit_view, recommendation_view
from .workflow import recover_interrupted, run_analysis, run_execution_graph


class JsonFormatter(logging.Formatter):
    def format(self, item):
        value = {"timestamp": self.formatTime(item), "level": item.levelname, "logger": item.name, "message": item.getMessage()}
        for field in ("request_id", "method", "path", "status", "duration_ms", "analysis_id"):
            if hasattr(item, field):
                value[field] = getattr(item, field)
        if item.exc_info:
            value["exception"] = self.formatException(item.exc_info)
        return json.dumps(value, ensure_ascii=False)


logger = logging.getLogger("profitops")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def validate_key(key: str | None, required=False):
    if key is None and not required:
        return str(uuid4())
    if key is None or not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", key):
        raise DomainError("invalid_idempotency_key", "请提供 1–100 位字母、数字、点、下划线、冒号或连字符组成的 Idempotency-Key", 422)
    return key


def create_app(settings: Settings | None = None):
    settings = settings or get_settings()
    engine = build_engine(settings.database_url)
    factory = make_session_factory(engine)
    registry = build_registry()

    @asynccontextmanager
    async def lifespan(app):
        initialize_database(engine)
        if settings.demo_mode and settings.auto_seed:
            seed_demo(factory)
        recover_interrupted(factory)
        yield
        engine.dispose()

    app = FastAPI(
        title="ProfitOps AI", version="1.0.0", lifespan=lifespan,
        description="确定性利润分析与 LangGraph 工作流；本地演示认证；所有执行仅为 simulation。金额单位 USD 美分。",
    )
    app.state.settings, app.state.engine, app.state.factory, app.state.registry = settings, engine, factory, registry

    @app.middleware("http")
    async def request_context(request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request.state.request_id = incoming if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", incoming) else str(uuid4())
        started = perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info("http_request", extra={"request_id": request.state.request_id, "method": request.method, "path": request.url.path, "status": response.status_code, "duration_ms": round((perf_counter() - started) * 1000, 2)})
        return response

    def error_response(request, code, message, status, details=None):
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details}, "request_id": getattr(request.state, "request_id", "unknown")})

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        if request.method == "POST":
            record_separately(factory, getattr(request.state, "actor", "anonymous"),
                              f"request.{request.url.path.rsplit('/', 1)[-1]}.denied", "recommendation" if "recommendations" in request.url.path else "request",
                              request.path_params.get("recommendation_id", request.url.path), request.state.request_id, "denied", {"code": exc.code, "message": exc.message})
        return error_response(request, exc.code, exc.message, exc.status, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        details = [{"location": list(item["loc"]), "message": item["msg"], "type": item["type"]} for item in exc.errors()]
        return error_response(request, "validation_error", "请求参数不符合接口 Schema", 422, details)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.exception("unhandled_error", extra={"request_id": getattr(request.state, "request_id", "unknown")})
        return error_response(request, "internal_error", "内部错误，请使用请求 ID 查询日志", 500)

    def identity(request: Request, actor: Actor = Depends(get_actor)):
        request.state.actor = actor.id
        return actor

    @app.get("/health", tags=["system"])
    def health():
        with factory() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected", "simulation": True, "engine": "deterministic-rules-v1"}

    @app.get("/api/demo", tags=["demo"])
    def demo():
        from sqlalchemy import func
        with factory() as session:
            minimum, maximum = session.execute(select(func.min(DailyOperation.date), func.max(DailyOperation.date))).one()
            stores = session.scalars(select(Store).order_by(Store.id)).all()
        return {
            "demo_mode": settings.demo_mode, "simulation": True, "currency": "USD",
            "identities": [{"id": "demo-analyst", "name": "经营分析师", "role": "analyst", "token": settings.analyst_token}, {"id": "demo-approver", "name": "审批负责人", "role": "approver", "token": settings.approver_token}] if settings.demo_mode else [],
            "stores": [{"id": store.id, "name": store.name, "marketplace": store.marketplace} for store in stores],
            "data_range": {"start_date": str(minimum), "end_date": str(maximum)} if minimum else None,
        }

    @app.post("/api/demo/seed", tags=["demo"])
    def seed(request: Request, actor: Actor = Depends(identity)):
        if not settings.demo_mode:
            raise DomainError("demo_disabled", "演示模式已关闭", 403)
        outcome = seed_demo(factory)
        record_separately(factory, actor, "demo.seed", "dataset", "demo-60d-v1", request.state.request_id, details=outcome)
        return outcome

    def metrics_request(start_date: date | None, end_date: date | None, store_id: str | None):
        try:
            body = AnalysisRequest(start_date=start_date, end_date=end_date, store_id=store_id)
        except ValueError as exc:
            raise DomainError("invalid_period", str(exc), 422) from exc
        with factory() as session:
            scope = resolve_scope(session, body)
            return build_report(fetch_metrics(session, scope)).model_dump(mode="json")

    @app.get("/api/dashboard/summary", tags=["metrics"])
    @app.get("/api/metrics", tags=["metrics"])
    def metrics(start_date: date | None = None, end_date: date | None = None, store_id: str | None = None, actor: Actor = Depends(identity)):
        return metrics_request(start_date, end_date, store_id)

    @app.get("/api/tools", tags=["agent"])
    def tools(actor: Actor = Depends(identity)):
        return {"items": registry.catalog()}

    @app.post("/api/analyses", status_code=202, tags=["agent"])
    def analyse(body: AnalysisRequest, request: Request, background_tasks: BackgroundTasks, idempotency_key: str | None = Header(default=None), actor: Actor = Depends(identity)):
        key = validate_key(idempotency_key)
        with factory() as session:
            scope = resolve_scope(session, body).model_dump(mode="json")
        try:
            with factory.begin() as session:
                row = Analysis(id=new_id(), idempotency_key=key, request=scope, actor=actor.id, request_id=request.state.request_id, status="queued")
                session.add(row)
                session.flush()
                response = analysis_view(row)
                record(session, actor, "analysis.requested", "analysis", row.id, request.state.request_id, details={"scope": scope})
        except IntegrityError:
            with factory() as session:
                row = session.scalar(select(Analysis).where(Analysis.idempotency_key == key))
                if row is None:
                    raise
                if row.request != scope or row.actor != actor.id:
                    raise DomainError("idempotency_conflict", "该请求键已绑定不同的分析或身份", 409)
                return analysis_view(row, replayed=True)
        background_tasks.add_task(run_analysis, factory, registry, response["id"], settings.step_timeout_seconds)
        return response

    @app.get("/api/analyses", tags=["agent"])
    def analyses(limit: int = Query(default=20, ge=1, le=100), actor: Actor = Depends(identity)):
        with factory() as session:
            rows = session.scalars(select(Analysis).order_by(Analysis.created_at.desc()).limit(limit)).all()
            return {"items": [analysis_view(row) for row in rows]}

    @app.get("/api/analyses/{analysis_id}", tags=["agent"])
    def analysis_detail(analysis_id: str, actor: Actor = Depends(identity)):
        with factory() as session:
            row = session.get(Analysis, analysis_id)
            if row is None:
                raise DomainError("analysis_not_found", "分析不存在", 404)
            return analysis_view(row)

    @app.get("/api/recommendations", tags=["recommendations"])
    def recommendations(limit: int = Query(default=50, ge=1, le=100), actor: Actor = Depends(identity)):
        with factory() as session:
            rows = session.scalars(select(Recommendation).order_by(Recommendation.created_at.desc()).limit(limit)).all()
            executions = {row.recommendation_id: row for row in session.scalars(select(Execution).where(Execution.recommendation_id.in_([item.id for item in rows]))).all()}
            return {"items": [recommendation_view(row, executions.get(row.id)) for row in rows]}

    @app.get("/api/recommendations/{recommendation_id}", tags=["recommendations"])
    def recommendation_detail(recommendation_id: str, actor: Actor = Depends(identity)):
        with factory() as session:
            row = session.get(Recommendation, recommendation_id)
            if row is None:
                raise DomainError("recommendation_not_found", "建议不存在", 404)
            execution = session.scalar(select(Execution).where(Execution.recommendation_id == row.id))
            return recommendation_view(row, execution)

    def decision(recommendation_id, body, request, actor, verb):
        with factory.begin() as session:
            row = decide(session, recommendation_id, verb, body.reason, actor, request.state.request_id)
            return recommendation_view(row)

    @app.post("/api/recommendations/{recommendation_id}/approve", tags=["approval"])
    def approve(recommendation_id: str, body: DecisionRequest, request: Request, actor: Actor = Depends(identity)):
        return decision(recommendation_id, body, request, actor, "approve")

    @app.post("/api/recommendations/{recommendation_id}/reject", tags=["approval"])
    def reject(recommendation_id: str, body: DecisionRequest, request: Request, actor: Actor = Depends(identity)):
        return decision(recommendation_id, body, request, actor, "reject")

    @app.post("/api/recommendations/{recommendation_id}/execute", tags=["execution"])
    def execute(recommendation_id: str, request: Request, idempotency_key: str | None = Header(default=None), actor: Actor = Depends(identity)):
        require_role(actor, "approver")
        key = validate_key(idempotency_key, required=True)
        with factory.begin() as session:
            if engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(Recommendation, recommendation_id)
            if row is None:
                raise DomainError("recommendation_not_found", "建议不存在", 404)
            result = run_execution_graph(factory, registry, session, row, key, actor, request.state.request_id)
        return JSONResponse(status_code=500 if result["status"] == "execution_failed" else 200, content=result)

    @app.get("/api/audit-logs", tags=["audit"])
    def audit_logs(limit: int = Query(default=100, ge=1, le=200), object_id: str | None = None, request_id: str | None = Query(default=None, max_length=64), actor: Actor = Depends(identity)):
        with factory() as session:
            query = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
            if object_id:
                query = query.where(AuditLog.object_id == object_id)
            if request_id:
                query = query.where(AuditLog.request_id == request_id)
            return {"items": [audit_view(row) for row in session.scalars(query).all()]}

    return app


app = create_app()
