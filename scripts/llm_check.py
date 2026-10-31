"""One-shot live DeepSeek verification against an isolated database.

Run from the project root with LLM_API_KEY set in the environment or .env:
    .venv/Scripts/python.exe scripts/llm_check.py

The script never prints the API key. It uses a temporary database and the same
LangGraph workflow as the API, so the output is real model output, not a fixture.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-fallback", action="store_true", help="Exit 0 even when the LLM call degrades")
    options = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    os.chdir(ROOT)
    settings = Settings()
    if not settings.llm_configured:
        print(json.dumps({
            "status": "skipped",
            "reason": "LLM_API_KEY 未配置",
            "hint": "在项目根目录 .env 或进程环境中设置 LLM_API_KEY 后重试；未配置时应用仍使用确定性规则。",
        }, ensure_ascii=False, indent=2))
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Settings(database_url=f"sqlite:///{Path(tmp) / 'llm-check.db'}", auto_seed=True))
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {settings.analyst_token}"}
            created = client.post("/api/analyses", json={}, headers=headers)
            if created.status_code != 202:
                print(json.dumps({"status": "request_failed", "http_status": created.status_code, "body": created.json()}, ensure_ascii=False, indent=2))
                return 1
            detail = client.get(f"/api/analyses/{created.json()['id']}", headers=headers).json()
            recommendation = None
            if detail.get("recommendation_id"):
                recommendation = client.get(f"/api/recommendations/{detail['recommendation_id']}", headers=headers).json()
    llm = (detail.get("result") or {}).get("llm") or {}
    known = {item["id"] for item in (recommendation or {}).get("body", {}).get("evidence", [])}
    output = llm.get("output") or {}
    referenced = set(output.get("evidence_references") or [])
    for cause in output.get("root_causes") or []:
        referenced.update(cause.get("evidence_ids") or [])
    for item in output.get("recommendations") or []:
        referenced.update(item.get("evidence_ids") or [])
    report = {
        "status": detail.get("status"),
        "engine": (detail.get("result") or {}).get("engine"),
        "analysis_id": detail.get("id"),
        "recommendation_id": detail.get("recommendation_id"),
        "llm": {
            "status": llm.get("status"),
            "configured": llm.get("configured"),
            "provider": llm.get("provider"),
            "model": llm.get("model"),
            "latency_ms": llm.get("latency_ms"),
            "attempts": llm.get("attempts"),
            "usage": llm.get("usage"),
            "reason": llm.get("reason"),
            "error": llm.get("error"),
            "output": output or None,
        },
        "evidence_ids": sorted(known),
        "all_references_valid": referenced <= known,
        "recommendation": {
            "problem": (recommendation or {}).get("body", {}).get("problem"),
            "engine": (recommendation or {}).get("body", {}).get("engine"),
        } if recommendation else None,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if llm.get("status") != "llm_success" and not options.allow_fallback:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
