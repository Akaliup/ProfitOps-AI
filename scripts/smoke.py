"""Live HTTP acceptance test. Uses only demo endpoints and simulation actions."""
import argparse
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


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
    print(json.dumps({"passed": len(checks), "failed": 0, "checks": checks, "analysis_id": analysis["id"], "recommendation_id": rec_id, "execution_id": executed["execution_id"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
