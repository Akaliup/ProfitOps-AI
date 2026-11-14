"""Exercise the ProfitOps MCP server over a real stdio transport.

Uses a temporary seeded SQLite database; never touches data/profitops.db.
The child process runs with LLM_PROVIDER=disabled and an empty LLM_API_KEY,
and uses the offline hashing embedding provider.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings  # noqa: E402
from app.db import build_engine, initialize_database, make_session_factory  # noqa: E402
from app.knowledge import build_embedding_provider, seed_knowledge  # noqa: E402
from app.seed import seed_demo  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

PERIOD = {"start_date": "2026-10-01", "end_date": "2026-10-30"}
EXPECTED_TOOLS = {"get_weekly_intelligence", "get_inventory_risks",
    "get_profit_analysis",
    "get_sku_breakdown",
    "search_knowledge",
    "get_approval_status",
}


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


async def run() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        database_url = f"sqlite:///{Path(tmp) / 'mcp-check.db'}"
        settings = Settings(
            _env_file=None,
            database_url=database_url,
            auto_seed=False,
            llm_provider="disabled",
            llm_api_key="",
            knowledge_embedding_provider="hashing",
            knowledge_embedding_model="hashing-v1",
        )
        engine = build_engine(settings.database_url)
        initialize_database(engine)
        factory = make_session_factory(engine)
        seed_demo(factory)
        seed_knowledge(factory, build_embedding_provider(settings), settings, tenant_id="demo")
        engine.dispose()

        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(ROOT / "backend"),
                "PROFITOPS_DATABASE_URL": database_url,
                "PROFITOPS_AUTO_SEED": "false",
                "PROFITOPS_MCP_ROLE": "analyst",
                "LLM_PROVIDER": "disabled",
                "LLM_API_KEY": "",
                "KNOWLEDGE_EMBEDDING_PROVIDER": "hashing",
                "KNOWLEDGE_EMBEDDING_MODEL": "hashing-v1",
                "KNOWLEDGE_MIN_SCORE": "0.15",
            }
        )
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            env=env,
            cwd=str(ROOT),
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = [tool.name for tool in listed.tools]

                analysis = await session.call_tool(
                    "get_profit_analysis", {"period": PERIOD}
                )
                analysis_payload = _payload(analysis)

                sku = await session.call_tool(
                    "get_sku_breakdown", {"period": PERIOD}
                )
                sku_payload = _payload(sku)

                knowledge = await session.call_tool(
                    "search_knowledge", {"query": "广告预算下调审批", "top_k": 3}
                )
                knowledge_payload = _payload(knowledge)

                write = await session.call_tool(
                    "create_recommendation", {"analysis_id": "not-exposed"}
                )
                write_payload = _payload(write)
                ask = await session.call_tool(
                    "simulate_execute_approved_action",
                    {"recommendation_id": "not-exposed", "idempotency_key": "check"},
                )
                ask_payload = _payload(ask)

                analysis_data = analysis_payload.get("data") or {}
                report = analysis_data.get("report") or {}
                current = report.get("current") or {}
                findings = analysis_data.get("findings") or {}
                sku_data = sku_payload.get("data") or {}
                knowledge_data = knowledge_payload.get("data") or {}
                hits = knowledge_data.get("hits") or []

                return {
                    "server": "profitops-mcp",
                    "tools": names,
                    "get_profit_analysis": {
                        "ok": analysis_payload.get("ok"),
                        "currency": report.get("currency"),
                        "net_profit": current.get("net_profit"),
                        "bridge_verified": report.get("bridge_verified"),
                        "evidence_count": len(findings.get("evidence") or []),
                    },
                    "get_sku_breakdown": {
                        "ok": sku_payload.get("ok"),
                        "rows": len(sku_data.get("rows") or []),
                    },
                    "search_knowledge": {
                        "ok": knowledge_payload.get("ok"),
                        "hits": len(hits),
                        "no_answer": knowledge_data.get("no_answer"),
                        "first_chunk_id": hits[0].get("chunk_id") if hits else None,
                        "embedding_model": knowledge_data.get("embedding_model"),
                    },
                    "write_tool_exposed": "create_recommendation" in names,
                    "write_tool_error": write_payload.get("error", {}).get("code"),
                    "ask_tool_exposed": "simulate_execute_approved_action" in names,
                    "ask_tool_error": ask_payload.get("error", {}).get("code"),
                    "llm_configured": False,
                }


def main() -> int:
    try:
        report = asyncio.run(run())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 1
    analysis = report["get_profit_analysis"]
    knowledge = report["search_knowledge"]
    ok = (
        set(report["tools"]) == EXPECTED_TOOLS
        and analysis["ok"] is True
        and analysis["currency"] == "USD"
        and isinstance(analysis["net_profit"], int)
        and analysis["bridge_verified"] is True
        and analysis["evidence_count"] > 0
        and report["get_sku_breakdown"]["ok"] is True
        and report["get_sku_breakdown"]["rows"] > 0
        and knowledge["ok"] is True
        and knowledge["hits"] > 0
        and knowledge["no_answer"] is False
        and knowledge["first_chunk_id"]
        and knowledge["embedding_model"] == "hashing-v1"
        and report["write_tool_exposed"] is False
        and report["write_tool_error"] == "unknown_tool"
        and report["ask_tool_exposed"] is False
        and report["ask_tool_error"] == "unknown_tool"
    )
    print(json.dumps({"ok": ok, **report}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
