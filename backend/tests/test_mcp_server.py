import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.config import Settings
from app.db import build_engine, initialize_database, make_session_factory
from app.knowledge import build_embedding_provider, seed_knowledge
from app.main import create_app
from app.mcp_server import EXPOSED_TOOL_NAMES, build_mcp_server
from app.models import Analysis, AuditLog, Execution, Recommendation
from app.seed import seed_demo
from app.tools import build_registry
from mcp.shared.memory import create_connected_server_and_client_session

PERIOD = {"start_date": "2026-10-01", "end_date": "2026-10-30"}


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'mcp.db'}",
        auto_seed=False,
        llm_provider="disabled",
        llm_api_key="",
        analysis_worker_enabled=False,
        rate_limit_enabled=False,
    )


def _seed(settings: Settings) -> None:
    engine = build_engine(settings.database_url)
    initialize_database(engine)
    factory = make_session_factory(engine)
    seed_demo(factory)
    seed_knowledge(factory, build_embedding_provider(settings), settings, tenant_id="demo")
    engine.dispose()


async def _with_session(mcp, scenario):
    async with create_connected_server_and_client_session(mcp.server) as session:
        await session.initialize()
        return await scenario(session)


def _payload(result) -> dict:
    assert result.content, "tool result must contain text content"
    return json.loads(result.content[0].text)


def test_mcp_lists_only_read_only_tools(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    mcp = build_mcp_server(settings)
    try:
        result = asyncio.run(_with_session(mcp, lambda session: session.list_tools()))
    finally:
        mcp.close()

    names = {tool.name for tool in result.tools}
    assert names == set(EXPOSED_TOOL_NAMES)
    assert "simulate_execute_approved_action" not in names
    assert "create_recommendation" not in names
    for tool in result.tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False
        assert tool.inputSchema["type"] == "object"


def test_mcp_exposed_tools_are_safe_registry_entries(tmp_path):
    settings = _settings(tmp_path)
    registry = build_registry(build_embedding_provider(settings), settings)
    for name in EXPOSED_TOOL_NAMES:
        spec = registry.tools[name]
        assert spec.effect in {"read", "pure"}
        assert spec.policy == "safe"


def test_mcp_profit_analysis_returns_integer_cents(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    mcp = build_mcp_server(settings)

    async def scenario(session):
        return await session.call_tool("get_profit_analysis", {"period": PERIOD})

    try:
        result = asyncio.run(_with_session(mcp, scenario))
    finally:
        mcp.close()

    assert result.isError is False
    payload = _payload(result)
    assert payload["ok"] is True
    report = payload["data"]["report"]
    assert report["currency"] == "USD"
    assert isinstance(report["current"]["net_profit"], int)
    assert isinstance(report["current"]["net_sales"], int)
    assert report["bridge_verified"] is True
    assert isinstance(payload["data"]["findings"]["evidence"], list)


def test_mcp_sku_breakdown_returns_rows(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    mcp = build_mcp_server(settings)

    async def scenario(session):
        return await session.call_tool("get_sku_breakdown", {"period": PERIOD})

    try:
        result = asyncio.run(_with_session(mcp, scenario))
    finally:
        mcp.close()

    assert result.isError is False
    payload = _payload(result)
    assert payload["ok"] is True
    assert payload["data"]["period"] == PERIOD
    assert len(payload["data"]["rows"]) > 0
    assert isinstance(payload["data"]["rows"][0]["net_profit_current"], int)


def test_mcp_search_knowledge_returns_citations(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    mcp = build_mcp_server(settings)

    async def scenario(session):
        return await session.call_tool(
            "search_knowledge", {"query": "广告预算下调审批", "top_k": 3}
        )

    try:
        result = asyncio.run(_with_session(mcp, scenario))
    finally:
        mcp.close()

    assert result.isError is False
    payload = _payload(result)
    assert payload["ok"] is True
    data = payload["data"]
    assert data["no_answer"] is False
    assert data["embedding_model"] == "hashing-v1"
    assert data["hits"], "RAG query should return at least one chunk"
    chunk_ids = [hit["chunk_id"] for hit in data["hits"]]
    assert len(chunk_ids) == len(set(chunk_ids))
    for hit in data["hits"]:
        assert hit["chunk_id"]
        assert hit["title"]
        assert hit["content"]
        assert 0 <= hit["score"] <= 1.1


def test_mcp_reads_http_created_recommendation_status(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    headers = {"Authorization": "Bearer demo-analyst"}
    with TestClient(create_app(settings, inline_jobs=True)) as http:
        created = http.post("/api/analyses", headers=headers, json={})
        assert created.status_code == 202, created.text
        detail = http.get(f"/api/analyses/{created.json()['id']}", headers=headers).json()
        assert detail["status"] == "waiting_approval", detail
        recommendation_id = detail["recommendation_id"]

    mcp = build_mcp_server(settings)
    try:
        result = asyncio.run(
            _with_session(
                mcp,
                lambda session: session.call_tool(
                    "get_approval_status", {"recommendation_id": recommendation_id}
                ),
            )
        )
    finally:
        mcp.close()

    assert result.isError is False
    payload = _payload(result)
    assert payload["ok"] is True
    assert payload["data"]["recommendation_id"] == recommendation_id
    assert payload["data"]["status"] == "pending_approval"


def test_mcp_rejects_unknown_write_ask_and_invalid_arguments(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    mcp = build_mcp_server(settings)

    async def scenario(session):
        unknown = await session.call_tool("delete_everything", {})
        ask = await session.call_tool(
            "simulate_execute_approved_action",
            {"recommendation_id": "x", "idempotency_key": "k"},
        )
        write = await session.call_tool(
            "create_recommendation",
            {"analysis_id": "x"},
        )
        bad_period = await session.call_tool(
            "get_profit_analysis",
            {"period": {"start_date": "2026-10-30", "end_date": "2026-10-01"}},
        )
        role_injection = await session.call_tool(
            "get_profit_analysis", {"period": PERIOD, "role": "approver"}
        )
        return unknown, ask, write, bad_period, role_injection

    try:
        unknown, ask, write, bad_period, role_injection = asyncio.run(
            _with_session(mcp, scenario)
        )
    finally:
        mcp.close()

    for result in (unknown, ask, write):
        assert result.isError is True
        assert _payload(result)["error"]["code"] == "unknown_tool"
    for result in (bad_period, role_injection):
        assert result.isError is True
        assert _payload(result)["error"]["code"] == "invalid_tool_input"


def test_mcp_calls_do_not_write_business_tables(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    engine = build_engine(settings.database_url)
    factory = make_session_factory(engine)
    models = (Analysis, Recommendation, Execution, AuditLog)

    def counts():
        with factory() as session:
            return {
                model.__tablename__: session.scalar(select(func.count()).select_from(model))
                for model in models
            }

    before = counts()
    mcp = build_mcp_server(settings)

    async def scenario(session):
        await session.call_tool("get_profit_analysis", {"period": PERIOD})
        await session.call_tool("get_sku_breakdown", {"period": PERIOD})
        await session.call_tool("search_knowledge", {"query": "广告预算审批"})
        await session.call_tool("get_approval_status", {"recommendation_id": "missing"})

    try:
        asyncio.run(_with_session(mcp, scenario))
    finally:
        mcp.close()

    after = counts()
    engine.dispose()
    assert before == after


def test_mcp_role_comes_from_server_settings(tmp_path):
    settings = _settings(tmp_path).model_copy(update={"mcp_role": "approver"})
    _seed(settings)
    mcp = build_mcp_server(settings)
    try:
        assert mcp.actor.role == "approver"
        result = asyncio.run(
            _with_session(
                mcp,
                lambda session: session.call_tool(
                    "get_profit_analysis", {"period": PERIOD}
                ),
            )
        )
    finally:
        mcp.close()

    assert result.isError is False
    with pytest.raises(ValidationError):
        Settings(_env_file=None, mcp_role="admin")
