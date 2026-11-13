"""Read-only MCP server over stdio.

The server reuses the same ``ToolRegistry`` as the HTTP API and the native
function-calling loop. Only high-level read/pure tools are exposed; proposal
writes, approval and simulated execution stay in the HTTP workflow behind the
server-side role check and human approval.

The RAG tool is exposed here as well: ``search_knowledge`` reads the same
knowledge chunks and returns the same stable ``chunk_id`` values as the HTTP
API. Knowledge content is untrusted data and must not be treated as
instructions by a client.

Run with::

    PYTHONPATH=backend python -m app.mcp_server

The process writes JSON-RPC to stdout, so it must not log to stdout. The
database must already be initialized and seeded; this server never creates
tables or writes business data.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from sqlalchemy.engine import Engine

from .config import Settings, get_settings
from .db import build_engine, make_session_factory
from .knowledge import build_embedding_provider
from .security import Actor
from .tools import ToolContext, ToolRegistry, build_registry

# Explicit allowlist. Every name must exist in the registry, be read/pure and
# use policy=safe. This is a security boundary, not a convenience list.
EXPOSED_TOOL_NAMES: tuple[str, ...] = (
    "get_weekly_intelligence",
    "get_inventory_risks",
    "get_profit_analysis",
    "get_sku_breakdown",
    "search_knowledge",
    "get_approval_status",
)
ALLOWED_EFFECTS = frozenset({"read", "pure"})
ALLOWED_POLICIES = frozenset({"safe"})


@dataclass
class MCPServer:
    server: Server
    engine: Engine
    registry: ToolRegistry
    settings: Settings
    actor: Actor

    def close(self) -> None:
        self.engine.dispose()


def _tool_result(payload: dict[str, Any], *, is_error: bool) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structuredContent=payload,
        isError=is_error,
    )


def _validated_specs(registry: ToolRegistry):
    missing = [name for name in EXPOSED_TOOL_NAMES if name not in registry.tools]
    if missing:
        raise RuntimeError(f"MCP 工具未在 Tool Registry 注册：{', '.join(missing)}")
    specs = [registry.tools[name] for name in EXPOSED_TOOL_NAMES]
    for spec in specs:
        if spec.effect not in ALLOWED_EFFECTS or spec.policy not in ALLOWED_POLICIES:
            raise RuntimeError(
                f"MCP 工具 {spec.name} 的 effect/policy 不允许暴露："
                f"{spec.effect}/{spec.policy}"
            )
    return specs


def build_mcp_server(
    settings: Settings | None = None,
    registry: ToolRegistry | None = None,
    actor: Actor | None = None,
) -> MCPServer:
    settings = settings or get_settings()
    embeddings = build_embedding_provider(settings)
    registry = registry or build_registry(embeddings, settings)
    engine = build_engine(settings.database_url)
    factory = make_session_factory(engine)
    actor = actor or Actor(id=f"mcp-{settings.mcp_role}", role=settings.mcp_role)
    specs = _validated_specs(registry)

    server = Server(
        "profitops-mcp",
        version="1.0.0",
        instructions=(
            "ProfitOps AI 只读 MCP Server。仅暴露 safe 且不写业务数据的工具，"
            "包括利润分析、SKU 明细、知识库检索和审批状态查询；"
            "建议写入、审批与模拟执行必须通过 HTTP 完成，并需要人工审批。"
        ),
    )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.input_model.model_json_schema(),
                annotations=types.ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    # RAG retrieval only leaves the process when a remote
                    # embedding gateway is explicitly configured.
                    openWorldHint=(
                        spec.name == "search_knowledge"
                        and settings.knowledge_embedding_remote
                    ),
                ),
            )
            for spec in specs
        ]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        if name not in EXPOSED_TOOL_NAMES:
            return _tool_result(
                {
                    "ok": False,
                    "error": {
                        "code": "unknown_tool",
                        "message": "工具不存在或未通过 MCP 暴露",
                        "details": {"exposed": list(EXPOSED_TOOL_NAMES)},
                    },
                },
                is_error=True,
            )
        context = ToolContext(
            factory=factory,
            actor=actor,
            request_id=f"mcp-{uuid4()}",
        )
        result = await asyncio.to_thread(registry.invoke, name, arguments or {}, context)
        return _tool_result(result.model_dump(mode="json"), is_error=not result.ok)

    return MCPServer(server=server, engine=engine, registry=registry, settings=settings, actor=actor)


async def _serve(mcp: MCPServer) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await mcp.server.run(read_stream, write_stream, mcp.server.create_initialization_options())


def main() -> None:
    mcp = build_mcp_server()
    try:
        asyncio.run(_serve(mcp))
    finally:
        mcp.close()


if __name__ == "__main__":
    main()
