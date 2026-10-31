"""Exercise the ProfitOps MCP server over a real stdio transport.

Uses a temporary seeded SQLite database; never touches data/profitops.db.
The child process runs with LLM_PROVIDER=disabled and an empty LLM_API_KEY.
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
from app.seed import seed_demo  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

PERIOD = {"start_date": "2026-10-01", "end_date": "2026-10-30"}


async def run() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        database_url = f"sqlite:///{Path(tmp) / 'mcp-check.db'}"
        settings = Settings(
            _env_file=None,
            database_url=database_url,
            auto_seed=False,
            llm_provider="disabled",
            llm_api_key="",
        )
        engine = build_engine(settings.database_url)
        initialize_database(engine)
        seed_demo(make_session_factory(engine))
        engine.dispose()

        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(ROOT / "backend"),
                "PROFITOPS_DATABASE_URL": database_url,
                "PROFITOPS_AUTO_SEED": "false",
                "LLM_PROVIDER": "disabled",
                "LLM_API_KEY": "",
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

                metrics = await session.call_tool("get_sales_metrics", {"period": PERIOD})
                metrics_payload = json.loads(metrics.content[0].text)

                calculated = await session.call_tool(
                    "calculate_profit", {"metrics": metrics_payload["data"]}
                )
                calculated_payload = json.loads(calculated.content[0].text)

                ask = await session.call_tool(
                    "simulate_execute_approved_action",
                    {"recommendation_id": "not-exposed", "idempotency_key": "check"},
                )
                ask_payload = json.loads(ask.content[0].text)

                return {
                    "server": "profitops-mcp",
                    "tools": names,
                    "get_sales_metrics": {
                        "ok": metrics_payload.get("ok"),
                        "currency": metrics_payload.get("data", {}).get("currency"),
                        "orders": metrics_payload.get("data", {}).get("current", {}).get("orders"),
                    },
                    "calculate_profit": {
                        "ok": calculated_payload.get("ok"),
                        "currency": calculated_payload.get("data", {}).get("currency"),
                        "net_sales": calculated_payload.get("data", {}).get("current", {}).get("net_sales"),
                        "net_profit": calculated_payload.get("data", {}).get("current", {}).get("net_profit"),
                        "bridge_verified": calculated_payload.get("data", {}).get("bridge_verified"),
                    },
                    "ask_tool_exposed": "simulate_execute_approved_action" in names,
                    "ask_tool_call_error": ask_payload.get("error", {}).get("code"),
                    "llm_configured": False,
                }


def main() -> int:
    try:
        report = asyncio.run(run())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 1
    ok = (
        report["get_sales_metrics"]["ok"] is True
        and report["calculate_profit"]["ok"] is True
        and report["calculate_profit"]["net_sales"] > 0
        and report["calculate_profit"]["bridge_verified"] is True
        and report["ask_tool_exposed"] is False
        and report["ask_tool_call_error"] == "unknown_tool"
    )
    print(json.dumps({"ok": ok, **report}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
