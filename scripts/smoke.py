"""Live HTTP acceptance test. Uses only demo endpoints and simulation actions."""
import argparse
import json
import time
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

ORDER_HEADER = (
    "order_id,store_id,sku,date,quantity,sales,discounts,refunds,cogs,"
    "fulfillment,platform_fees,other_costs"
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    args = parser.parse_args()
    checks = []
    def call(path, token=None, body=None, key=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if key:
            headers["Idempotency-Key"] = key
        request = Request(args.base_url + path, json.dumps(body).encode() if body is not None else None, headers)
        try:
            with urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())
    def check(name, predicate):
        checks.append({"name": name, "passed": bool(predicate)})
        assert predicate, name
    def call_csv(path, token, body, filename):
        headers = {
            "Content-Type": "text/csv; charset=utf-8",
            "X-Filename": filename,
            "Authorization": f"Bearer {token}",
        }
        request = Request(args.base_url + path, body.encode("utf-8"), headers)
        try:
            with urlopen(request, timeout=20) as response:
                return response.status, response.read().decode("utf-8")
        except HTTPError as error:
            return error.code, error.read().decode("utf-8")
    check("health", call("/health")[0] == 200)
    status, config = call("/api/demo")
    analyst = next(item["token"] for item in config["identities"] if item["role"] == "analyst")
    approver = next(item["token"] for item in config["identities"] if item["role"] == "approver")
    check("seed", call("/api/demo/seed", analyst, {})[0] == 200)
    _, report = call("/api/dashboard/summary", analyst)
    check("anomaly_and_bridge", report["anomaly"] and report["bridge_verified"] and report["data_quality"]["complete"])
    status, analysis = call("/api/analyses", analyst, {}, str(uuid4()))
    check("analysis_accepted", status == 202)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        _, analysis = call(f"/api/analyses/{analysis['id']}", analyst)
        if analysis["status"] not in {"queued", "running"}:
            break
        time.sleep(0.1)
    check("analysis_waiting_approval", analysis["status"] == "waiting_approval")
    rec_id = analysis["recommendation_id"]
    path = f"/api/recommendations/{rec_id}"
    check("unapproved_blocked", call(path + "/execute", approver, {}, str(uuid4()))[0] == 409)
    check("role_enforced", call(path + "/approve", analyst, {"reason": "smoke unauthorized"})[0] == 403)
    check("approval", call(path + "/approve", approver, {"reason": "smoke: 复核证据并批准模拟"})[0] == 200)
    key = str(uuid4())
    status, executed = call(path + "/execute", approver, {}, key)
    check("simulation_executed", status == 200 and executed["status"] == "executed" and executed["simulation"] and executed["result"]["external_calls"] == 0)
    _, repeated = call(path + "/execute", approver, {}, key)
    check("idempotency", repeated["replayed"] and repeated["execution_id"] == executed["execution_id"])
    _, logs = call(f"/api/audit-logs?object_id={rec_id}", analyst)
    actions = {row["action"] for row in logs["items"]}
    check("audit_complete", {"recommendation.approve", "execution.started", "execution.executed", "execution.replayed", "request.execute.denied"}.issubset(actions))
    _, documents = call("/api/knowledge/documents", analyst)
    check("knowledge_seeded", len(documents["items"]) >= 6)
    status, knowledge = call("/api/knowledge/search", analyst, {"query": "广告预算下调审批", "top_k": 3})
    check("knowledge_search", status == 200 and not knowledge["no_answer"] and bool(knowledge["hits"]))
    _, no_answer = call("/api/knowledge/search", analyst, {"query": "如何预订会议室午餐", "top_k": 3})
    check("knowledge_no_answer", no_answer["no_answer"] and not no_answer["hits"])
    check("knowledge_role_enforced", call("/api/knowledge/documents", analyst, {"title": "smoke doc", "source": "synthetic://smoke", "content": "# smoke\n\nsmoke content"})[0] == 403)
    request = Request(
        args.base_url + "/api/imports/template/orders",
        headers={"Authorization": f"Bearer {analyst}"},
    )
    with urlopen(request, timeout=20) as response:
        template = response.read().decode("utf-8-sig")
    check("import_template", template.splitlines()[0] == ORDER_HEADER)
    order_id = f"SMOKE-{uuid4().hex[:10]}"
    business_date = datetime.now(timezone.utc).date().isoformat()
    order_csv = (
        ORDER_HEADER + "\n"
        f"{order_id},us-store,TRAVEL-BAG-01,{business_date},1,49.00,3.92,0,16.00,6.50,7.35,0.90\n"
    )
    status, raw = call_csv("/api/imports/orders/preview", analyst, order_csv, "smoke-orders.csv")
    preview = json.loads(raw)
    check("import_preview", status == 200 and preview["can_commit"] and preview["accepted_rows"] == 1)
    status, raw = call_csv("/api/imports/orders", approver, order_csv, "smoke-orders.csv")
    committed = json.loads(raw)
    check("import_committed", status == 200 and committed["status"] == "committed" and any(
        item["code"] == "db_roundtrip" and item["ok"] for item in committed["checks"]
    ))
    status, raw = call_csv("/api/imports/orders", approver, order_csv, "smoke-orders.csv")
    check("import_idempotent", status == 200 and json.loads(raw)["status"] == "noop")
    status, raw = call_csv(
        "/api/imports/orders", approver, order_csv.replace(",49.00,3.92", ",50.00,3.92"),
        "smoke-orders.csv",
    )
    check("import_conflict_blocked", status == 409 and json.loads(raw)["error"]["code"] == "import_rejected")
    check("import_role_enforced", call_csv("/api/imports/orders", analyst, order_csv, "smoke-orders.csv")[0] == 403)
    print(json.dumps({"passed": len(checks), "failed": 0, "checks": checks, "analysis_id": analysis["id"], "recommendation_id": rec_id, "execution_id": executed["execution_id"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
