"""Read-only MCP server over stdio.

Only safe tools that do not write business data are exposed. The
``create_recommendation`` tool is excluded because it writes a proposal, and
``simulate_execute_approved_action`` is excluded because it is an ``ask`` tool
that must go through the HTTP approval flow with a human approver.

Run with::

    PYTHONPATH=backend python -m app.mcp_server

The server writes JSON-RPC to stdout, so it must not print logs to stdout.
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
from .security import Actor
from .tools import ToolContext, ToolRegistry, build_registry

# Explicit allowlist. Every name must be a safe tool with no business writes.
EXPOSED_TOOL_NAMES: tuple[str, ...] = (
    "get_sales_metrics",
    "calculate_profit",
    "analyze_profit_drivers",
    "get_approval_status",
)


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


def build_mcp_server(
    settings: Settings | None = None,
    registry: ToolRegistry | None = None,
    actor: Actor | None = None,
) -> MCPServer:
    settings = settings or get_settings()
    registry = registry or build_registry()
    engine = build_engine(settings.database_url)
    factory = make_session_factory(engine)
    actor = actor or Actor(id=f"mcp-{settings.mcp_role}", role=settings.mcp_role)
    specs = [registry.tools[name] for name in EXPOSED_TOOL_NAMES]

    server = Server(
        "profitops-mcp",
        version="1.0.0",
        instructions=(
            "ProfitOps AI 只读 MCP Server。仅暴露 safe 且不写业务数据的工具；"
            "审批与模拟执行必须通过 HTTP 完成，且需要人工审批。"
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
                    readOnlyHint=spec.effect in {"read", "pure"},
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
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
