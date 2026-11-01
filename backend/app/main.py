import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import date
from time import perf_counter
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import case, select, text, update
from sqlalchemy.exc import IntegrityError

from .audit import record, record_separately
from .config import Settings, get_settings
from .db import begin_write, build_engine, initialize_database, make_session_factory
from .errors import DomainError
from .knowledge import (
    build_embedding_provider, create_document, delete_document, list_documents,
    reindex_document, search_knowledge, seed_knowledge,
)
from .llm import build_llm_provider
from .metrics import build_report, fetch_metrics, resolve_scope
from .models import (
    Analysis, AuditLog, DailyOperation, Execution, KnowledgeDocument,
    Recommendation, Store, User, new_id, utcnow,
)
from .observability import (
    METRICS, RATE_LIMIT_EXEMPT_PATHS, TokenBucketLimiter, normalize_path,
    rate_limit_key, rate_limit_scope, retry_after_header,
)
from .schemas import (
    AnalysisRequest, DecisionRequest, KnowledgeDocumentInput, KnowledgeSearchInput,
    LoginRequest, LoginResponse,
)
from .security import (
    Actor, authenticate, create_access_token, get_actor, get_optional_actor,
    require_role, scoped_idempotency_key,
)
from .seed import seed_demo, seed_identity
from .services import decide
from .tools import build_registry
from .views import analysis_view, audit_view, recommendation_view
from .worker import AnalysisWorker, process_analysis_job
from .workflow import recover_interrupted, run_execution_graph


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
    embeddings = build_embedding_provider(settings)
    registry = build_registry(embeddings, settings)
    llm = build_llm_provider(settings)

    knowledge_seed_error: dict[str, str] | None = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal knowledge_seed_error
        knowledge_seed_error = None
        # Database setup failures must still abort startup; only the optional
        # knowledge seed may degrade so the core API stays available.
        initialize_database(engine)
        if settings.demo_mode:
            seed_identity(factory, settings)
            if settings.auto_seed:
                seed_demo(factory)
                try:
                    seed_knowledge(factory, embeddings, settings, tenant_id="demo")
                except DomainError as exc:
                    knowledge_seed_error = {"code": exc.code, "message": exc.message}
                    logger.warning(
                        "knowledge_seed_failed: %s: %s", exc.code, exc.message,
                        extra={"error_code": exc.code},
                    )
        recover_interrupted(factory, engine, settings)
        worker = None
        if settings.analysis_worker_enabled:
            worker = AnalysisWorker(factory, engine, registry, settings, llm)
            worker.start()
        app.state.worker = worker
        app.state.knowledge_seed_error = knowledge_seed_error
        try:
            yield
        finally:
            if worker is not None:
                worker.stop()
            engine.dispose()

    app = FastAPI(
        title="ProfitOps AI", version="1.0.0", lifespan=lifespan,
        description="确定性利润分析与 LangGraph 工作流；本地演示认证；所有执行仅为 simulation。金额单位 USD 美分。",
    )
    app.state.settings, app.state.engine, app.state.factory, app.state.registry = settings, engine, factory, registry
    app.state.llm, app.state.embeddings = llm, embeddings
    limiter = TokenBucketLimiter()
    app.state.rate_limiter = limiter

    def record_http(method: str, path: str, status: int, duration: float) -> None:
        if not settings.metrics_enabled:
            return
        label = normalize_path(path)
        METRICS.inc("http_requests_total", method=method, path=label, status=str(status))
        METRICS.observe("http_request_duration_seconds", duration, method=method, path=label, status=str(status))

    @app.middleware("http")
    async def request_context(request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request.state.request_id = incoming if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", incoming) else str(uuid4())
        started = perf_counter()
        path = request.url.path
        if settings.rate_limit_enabled and path not in RATE_LIMIT_EXEMPT_PATHS:
            scope = rate_limit_scope(path, request.method)
            limit = {
                "login": settings.rate_limit_login_requests,
                "analysis": settings.rate_limit_analysis_requests,
            }.get(scope, settings.rate_limit_requests)
            authorization = request.headers.get("Authorization", "")
            token = authorization[7:] if authorization.startswith("Bearer ") else None
            if scope == "login":
                # Login does not authenticate the bearer value, so it must not
                # key the brute-force bucket: rotating arbitrary tokens would
                # otherwise hand out a fresh bucket for every password attempt.
                token = None
            allowed, retry_after = limiter.allow(
                f"{scope}:{rate_limit_key(request, token)}",
                capacity=limit, window=settings.rate_limit_window_seconds,
            )
            if not allowed:
                duration = perf_counter() - started
                record_http(request.method, path, 429, duration)
                if settings.metrics_enabled:
                    METRICS.inc("rate_limited_total", scope=scope)
                response = JSONResponse(
                    status_code=429,
                    content={
                        "error": {
                            "code": "rate_limited",
                            "message": "请求过于频繁，请稍后重试",
                            "details": {"scope": scope, "retry_after_seconds": round(retry_after, 3)},
                        },
                        "request_id": request.state.request_id,
                    },
                )
                response.headers["Retry-After"] = retry_after_header(retry_after)
                response.headers["X-RateLimit-Limit"] = str(limit)
                response.headers["X-RateLimit-Remaining"] = "0"
                response.headers["X-Request-ID"] = request.state.request_id
                logger.warning(
                    "http_rate_limited",
                    extra={
                        "request_id": request.state.request_id, "method": request.method,
                        "path": path, "status": 429, "duration_ms": round(duration * 1000, 2),
                    },
                )
                return response
        try:
            response = await call_next(request)
        except Exception:
            record_http(request.method, path, 500, perf_counter() - started)
            raise
        duration = perf_counter() - started
        response.headers["X-Request-ID"] = request.state.request_id
        route = request.scope.get("route")
        record_http(request.method, getattr(route, "path", None) or path, response.status_code, duration)
        logger.info("http_request", extra={"request_id": request.state.request_id, "method": request.method, "path": path, "status": response.status_code, "duration_ms": round(duration * 1000, 2)})
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
        # Keep the full actor so denied-request audits stay in its tenant.
        request.state.actor = actor
        return actor

    def user_view(actor: Actor) -> dict:
        with factory() as session:
            user = session.get(User, actor.id)
        return {
            "id": actor.id, "role": actor.role, "tenant_id": actor.tenant_id,
            "email": user.email if user else None,
            "display_name": user.display_name if user else actor.id,
        }

    @app.post("/api/auth/login", response_model=LoginResponse, tags=["auth"])
    def login(body: LoginRequest):
        actor = authenticate(factory, body.email, body.password, body.tenant_id)
        token, expires_in = create_access_token(actor, settings)
        return LoginResponse(
            access_token=token, expires_in=expires_in, user=user_view(actor),
        )

    @app.get("/api/auth/me", tags=["auth"])
    def me(actor: Actor = Depends(identity)):
        return user_view(actor)

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics():
        if not settings.metrics_enabled:
            raise DomainError("metrics_disabled", "指标端点已关闭", 404)
        return PlainTextResponse(METRICS.render(), media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/health", tags=["system"])
    def health():
        with factory() as session:
            session.execute(text("SELECT 1"))
        return {
            "status": "ok", "database": "connected", "simulation": True,
            "engine": "deterministic-rules-v1",
            "llm": {"configured": bool(llm), "provider": settings.llm_provider, "model": settings.llm_model},
            "knowledge": {
                "provider": settings.knowledge_embedding_provider,
                "model": settings.knowledge_embedding_model,
                "remote": settings.knowledge_embedding_remote,
                "seed_error": knowledge_seed_error,
            },
        }

    @app.get("/api/demo", tags=["demo"])
    def demo(actor: Actor = Depends(get_optional_actor)):
        from sqlalchemy import func
        with factory() as session:
            minimum, maximum = session.execute(
                select(func.min(DailyOperation.date), func.max(DailyOperation.date))
                .where(DailyOperation.tenant_id == actor.tenant_id)
            ).one()
            stores = session.scalars(
                select(Store).where(Store.tenant_id == actor.tenant_id).order_by(Store.id)
            ).all()
            knowledge_documents = session.scalar(
                select(func.count()).select_from(KnowledgeDocument).where(
                    KnowledgeDocument.tenant_id == actor.tenant_id,
                    KnowledgeDocument.active.is_(True),
                )
            ) or 0
        return {
            "demo_mode": settings.demo_mode, "simulation": True, "currency": "USD",
            "knowledge": {
                "documents": knowledge_documents,
                "embedding_provider": settings.knowledge_embedding_provider,
                "embedding_model": settings.knowledge_embedding_model,
                "seed_error": knowledge_seed_error,
            },
            "identities": [{"id": "demo-analyst", "name": "经营分析师", "role": "analyst", "token": settings.analyst_token}, {"id": "demo-approver", "name": "审批负责人", "role": "approver", "token": settings.approver_token}] if settings.demo_mode else [],
            "stores": [{"id": store.id, "name": store.name, "marketplace": store.marketplace} for store in stores],
            "data_range": {"start_date": str(minimum), "end_date": str(maximum)} if minimum else None,
            "llm": {
                "configured": bool(llm), "provider": settings.llm_provider, "model": settings.llm_model,
                "fallback_policy": settings.llm_fallback_policy,
            },
        }

    @app.post("/api/demo/seed", tags=["demo"])
    def seed(request: Request, actor: Actor = Depends(identity)):
        if not settings.demo_mode:
            raise DomainError("demo_disabled", "演示模式已关闭", 403)
        if actor.tenant_id != "demo":
            raise DomainError("demo_tenant_only", "只有演示租户可以初始化演示数据", 403)
        outcome = seed_demo(factory)
        knowledge = seed_knowledge(factory, embeddings, settings, tenant_id="demo")
        record_separately(
            factory, actor, "demo.seed", "dataset", "demo-60d-v1",
            request.state.request_id, details={"demo": outcome, "knowledge": knowledge},
        )
        return {**outcome, "knowledge": knowledge}

    def metrics_request(start_date: date | None, end_date: date | None, store_id: str | None, tenant_id: str):
        try:
            body = AnalysisRequest(start_date=start_date, end_date=end_date, store_id=store_id)
        except ValueError as exc:
            raise DomainError("invalid_period", str(exc), 422) from exc
        with factory() as session:
            scope = resolve_scope(session, body, tenant_id)
            return build_report(fetch_metrics(session, scope, tenant_id)).model_dump(mode="json")

    @app.get("/api/dashboard/summary", tags=["metrics"])
    @app.get("/api/metrics", tags=["metrics"])
    def metrics(start_date: date | None = None, end_date: date | None = None, store_id: str | None = None, actor: Actor = Depends(identity)):
        return metrics_request(start_date, end_date, store_id, actor.tenant_id)

    @app.get("/api/tools", tags=["agent"])
    def tools(actor: Actor = Depends(identity)):
        return {"items": registry.catalog()}

    @app.get("/api/knowledge/documents", tags=["knowledge"])
    def knowledge_documents(actor: Actor = Depends(identity)):
        with factory() as session:
            return {"items": [item.model_dump(mode="json") for item in list_documents(session, actor.tenant_id)]}

    @app.post("/api/knowledge/documents", tags=["knowledge"])
    def add_knowledge_document(body: KnowledgeDocumentInput, request: Request, actor: Actor = Depends(identity)):
        require_role(actor, "approver")
        with factory.begin() as session:
            # Acquire SQLite's write lock before the dedupe SELECT. Otherwise two
            # concurrent uploads can both read "no document" and the second
            # write fails with SQLITE_BUSY_SNAPSHOT instead of deduping.
            begin_write(session, engine)
            item = create_document(
                session, body, embeddings,
                chunk_chars=settings.knowledge_chunk_chars,
                overlap=settings.knowledge_chunk_overlap,
                actor=actor, request_id=request.state.request_id,
                tenant_id=actor.tenant_id,
            )
        return JSONResponse(status_code=201 if item.created else 200, content=item.model_dump(mode="json"))

    @app.post("/api/knowledge/documents/{document_id}/reindex", tags=["knowledge"])
    def reindex_knowledge_document(document_id: str, request: Request, actor: Actor = Depends(identity)):
        require_role(actor, "approver")
        with factory.begin() as session:
            item = reindex_document(
                session, document_id, embeddings,
                actor=actor, request_id=request.state.request_id,
                tenant_id=actor.tenant_id,
            )
        return item.model_dump(mode="json")

    @app.delete("/api/knowledge/documents/{document_id}", tags=["knowledge"])
    def remove_knowledge_document(document_id: str, request: Request, actor: Actor = Depends(identity)):
        require_role(actor, "approver")
        with factory.begin() as session:
            item = delete_document(session, document_id, actor, request.state.request_id, actor.tenant_id)
        return item.model_dump(mode="json")

    @app.post("/api/knowledge/search", tags=["knowledge"])
    def knowledge_search(body: KnowledgeSearchInput, actor: Actor = Depends(identity)):
        with factory() as session:
            result = search_knowledge(
                session, body, embeddings,
                candidate_k=settings.knowledge_candidate_k,
                min_score=settings.knowledge_min_score,
                tenant_id=actor.tenant_id,
            )
        return result.model_dump(mode="json")

    @app.post("/api/analyses", status_code=202, tags=["agent"])
    def analyse(body: AnalysisRequest, request: Request, background_tasks: BackgroundTasks, idempotency_key: str | None = Header(default=None), actor: Actor = Depends(identity)):
        key = scoped_idempotency_key(actor.tenant_id, validate_key(idempotency_key))
        with factory() as session:
            scope = resolve_scope(session, body, actor.tenant_id).model_dump(mode="json")
        try:
            with factory.begin() as session:
                row = Analysis(
                    id=new_id(), tenant_id=actor.tenant_id, idempotency_key=key,
                    request=scope, actor=actor.id,
                    request_id=request.state.request_id, status="queued",
                    max_attempts=settings.analysis_max_attempts,
                )
                session.add(row)
                session.flush()
                response = analysis_view(row)
                record(session, actor, "analysis.requested", "analysis", row.id, request.state.request_id, details={"scope": scope})
        except IntegrityError:
            with factory() as session:
                row = session.scalar(select(Analysis).where(
                    Analysis.idempotency_key == key,
                    Analysis.tenant_id == actor.tenant_id,
                ))
                if row is None:
                    raise DomainError("idempotency_conflict", "该请求键已被使用", 409)
                if row.request != scope or row.actor != actor.id:
                    raise DomainError("idempotency_conflict", "该请求键已绑定不同的分析或身份", 409)
                return analysis_view(row, replayed=True)
        background_tasks.add_task(process_analysis_job, factory, engine, registry, response["id"], settings, app.state.llm)
        return response

    @app.get("/api/analyses", tags=["agent"])
    def analyses(
        limit: int = Query(default=20, ge=1, le=100),
        status: str | None = Query(default=None, pattern="^(queued|running|waiting_approval|completed|insufficient_data|failed)$"),
        actor: Actor = Depends(identity),
    ):
        with factory() as session:
            query = select(Analysis).where(
                Analysis.tenant_id == actor.tenant_id,
            ).order_by(Analysis.created_at.desc()).limit(limit)
            if status is not None:
                query = query.where(Analysis.status == status)
            rows = session.scalars(query).all()
            return {"items": [analysis_view(row) for row in rows]}

    @app.get("/api/analyses/{analysis_id}", tags=["agent"])
    def analysis_detail(analysis_id: str, actor: Actor = Depends(identity)):
        with factory() as session:
            row = session.scalar(select(Analysis).where(
                Analysis.id == analysis_id, Analysis.tenant_id == actor.tenant_id,
            ))
            if row is None:
                raise DomainError("analysis_not_found", "分析不存在", 404)
            return analysis_view(row)

    @app.post("/api/analyses/{analysis_id}/retry", status_code=202, tags=["agent"])
    def retry_analysis(
        analysis_id: str, request: Request, background_tasks: BackgroundTasks,
        actor: Actor = Depends(identity),
    ):
        with factory.begin() as session:
            begin_write(session, engine)
            # Only a still-failed row may move back to queued. A read followed
            # by an ORM assignment would let two retries (or a retry racing a
            # worker) overwrite a running/completed state.
            changed = session.execute(
                update(Analysis)
                .where(
                    Analysis.id == analysis_id,
                    Analysis.tenant_id == actor.tenant_id,
                    Analysis.status == "failed",
                )
                .values(
                    status="queued",
                    next_run_at=None,
                    lease_until=None,
                    worker_id=None,
                    error=None,
                    max_attempts=case(
                        (Analysis.max_attempts >= Analysis.attempts + 1, Analysis.max_attempts),
                        else_=Analysis.attempts + 1,
                    ),
                    updated_at=utcnow(),
                )
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                row = session.scalar(select(Analysis).where(
                    Analysis.id == analysis_id, Analysis.tenant_id == actor.tenant_id,
                ))
                if row is None:
                    raise DomainError("analysis_not_found", "分析不存在", 404)
                raise DomainError(
                    "analysis_not_retryable", "只有失败的分析可以手动重试", 409,
                    {"status": row.status},
                )
            row = session.get(Analysis, analysis_id)
            response = analysis_view(row)
            record(
                session, actor, "analysis.requeued", "analysis", row.id,
                request.state.request_id, "success",
                {"previous_attempts": row.attempts, "max_attempts": row.max_attempts},
            )
        background_tasks.add_task(process_analysis_job, factory, engine, registry, analysis_id, settings, app.state.llm)
        return response

    @app.get("/api/recommendations", tags=["recommendations"])
    def recommendations(limit: int = Query(default=50, ge=1, le=100), actor: Actor = Depends(identity)):
        with factory() as session:
            rows = session.scalars(select(Recommendation).where(
                Recommendation.tenant_id == actor.tenant_id,
            ).order_by(Recommendation.created_at.desc()).limit(limit)).all()
            executions = {row.recommendation_id: row for row in session.scalars(select(Execution).where(
                Execution.recommendation_id.in_([item.id for item in rows]),
                Execution.tenant_id == actor.tenant_id,
            )).all()}
            return {"items": [recommendation_view(row, executions.get(row.id)) for row in rows]}

    @app.get("/api/recommendations/{recommendation_id}", tags=["recommendations"])
    def recommendation_detail(recommendation_id: str, actor: Actor = Depends(identity)):
        with factory() as session:
            row = session.scalar(select(Recommendation).where(
                Recommendation.id == recommendation_id,
                Recommendation.tenant_id == actor.tenant_id,
            ))
            if row is None:
                raise DomainError("recommendation_not_found", "建议不存在", 404)
            execution = session.scalar(select(Execution).where(
                Execution.recommendation_id == row.id,
                Execution.tenant_id == actor.tenant_id,
            ))
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
            row = session.scalar(select(Recommendation).where(
                Recommendation.id == recommendation_id,
                Recommendation.tenant_id == actor.tenant_id,
            ))
            if row is None:
                raise DomainError("recommendation_not_found", "建议不存在", 404)
            result = run_execution_graph(factory, registry, session, row, key, actor, request.state.request_id)
        return JSONResponse(status_code=500 if result["status"] == "execution_failed" else 200, content=result)

    @app.get("/api/audit-logs", tags=["audit"])
    def audit_logs(limit: int = Query(default=100, ge=1, le=200), object_id: str | None = None, request_id: str | None = Query(default=None, max_length=64), actor: Actor = Depends(identity)):
        with factory() as session:
            query = select(AuditLog).where(
                AuditLog.tenant_id == actor.tenant_id,
            ).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
            if object_id:
                query = query.where(AuditLog.object_id == object_id)
            if request_id:
                query = query.where(AuditLog.request_id == request_id)
            return {"items": [audit_view(row) for row in session.scalars(query).all()]}

    return app


app = create_app()
