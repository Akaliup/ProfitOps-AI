"""Build the synthetic recorded-response fixture used by the replay eval.

This does not call a model. It materializes the same deterministic
``build_fake_output`` used by the offline native eval and writes it as a fixed
recording, so CI can verify the replay/parse/evidence path without network.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from evals.graders import build_fake_output, build_oracle, check_integrity, load_cases  # noqa: E402

CASE_DIR = BACKEND / "evals" / "llm_cases"
DEFAULT_OUTPUT = BACKEND / "evals" / "recordings" / "synthetic_native_v1.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build synthetic LLM replay recording")
    parser.add_argument("--case-dir", type=Path, default=CASE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    cases = []
    for case in load_cases(args.case_dir):
        integrity = check_integrity(case)
        if integrity.raw is None:
            raise SystemExit(f"{case['case_id']}: integrity failed: {integrity.as_dict()}")
        oracle = build_oracle(integrity.raw)
        if oracle.error_code is not None or not oracle.findings.anomaly:
            cases.append({
                "case_id": case["case_id"],
                "expected_status": "not_requested",
                "expected_reason": oracle.labels["llm_reason"],
                "turns": [],
            })
            continue
        content = json.dumps(build_fake_output(case, oracle), ensure_ascii=False)
        cases.append({
            "case_id": case["case_id"],
            "expected_status": "llm_success",
            "expected_reason": None,
            "turns": [{
                "content": content,
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 1400, "completion_tokens": 260, "total_tokens": 1660},
            }],
        })

    recording = {
        "recording_id": "synthetic-native-replay-v1",
        "source_type": "synthetic",
        "note": "Not a real model transcript. Generated from deterministic eval fixtures to exercise the replay boundary offline.",
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(recording, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output} ({len(cases)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
