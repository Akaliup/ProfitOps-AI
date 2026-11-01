"""Offline RAG retrieval evaluation.

This evaluates the deterministic knowledge base, not an LLM. It seeds the six
synthetic SOP documents into a temporary SQLite database, runs a small labelled
query set, and reports hit@1/hit@3, no-answer accuracy and citation validity.

Run from the project root:
  .venv/Scripts/python.exe backend/evals/rag.py
  .venv/Scripts/python.exe backend/evals/rag.py --json backend/evals/reports/rag.json

No network call is made: the hashing embedding provider is forced.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import build_engine, initialize_database, make_session_factory  # noqa: E402
from app.knowledge import (  # noqa: E402
    HashingEmbeddingProvider, search_knowledge, seed_knowledge,
)
from app.models import KnowledgeChunk, KnowledgeDocument  # noqa: E402
from app.schemas import KnowledgeSearchInput  # noqa: E402

REPORT_DIR = BACKEND / "evals" / "reports"

CASES: list[dict] = [
    {
        "id": "ad-budget-approval",
        "query": "广告预算下调 15% 需要谁审批",
        "expected_title": "广告预算调整与审批规则",
        "expect_no_answer": False,
    },
    {
        "id": "refund-triage",
        "query": "退款率异常怎么排查",
        "expected_title": "退款率异常排查手册",
        "expect_no_answer": False,
    },
    {
        "id": "fulfillment-cost",
        "query": "履约成本上涨要检查哪些费用",
        "expected_title": "履约成本异常排查",
        "expect_no_answer": False,
    },
    {
        "id": "platform-fees",
        "query": "平台手续费和结算差异怎么核对",
        "expected_title": "平台手续费与结算核对",
        "expect_no_answer": False,
    },
    {
        "id": "data-quality",
        "query": "每日经营记录缺失怎么处理",
        "expected_title": "数据完整性与缺失处理",
        "expect_no_answer": False,
    },
    {
        "id": "approval-idempotency",
        "query": "执行幂等键重复请求怎么办",
        "expected_title": "审批、执行与幂等规则",
        "expect_no_answer": False,
    },
    {
        "id": "unrelated-lunch",
        "query": "如何预订会议室午餐",
        "expected_title": None,
        "expect_no_answer": True,
    },
    {
        "id": "unrelated-weather",
        "query": "今天天气怎么样",
        "expected_title": None,
        "expect_no_answer": True,
    },
]


def run_evaluation(*, top_k: int = 3, min_score: float = 0.15) -> dict:
    settings = Settings(
        database_url="sqlite:///:memory:",
        auto_seed=False,
        knowledge_embedding_provider="hashing",
        knowledge_embedding_model="hashing-v1",
        knowledge_top_k=top_k,
        knowledge_candidate_k=20,
        knowledge_min_score=min_score,
    )
    embeddings = HashingEmbeddingProvider(
        dimensions=settings.knowledge_embedding_dimensions,
        model=settings.knowledge_embedding_model,
    )
    engine = build_engine(settings.database_url)
    initialize_database(engine)
    factory = make_session_factory(engine)
    try:
        seeded = seed_knowledge(factory, embeddings, settings)
        with factory() as session:
            document_count = len(session.scalars(
                select(KnowledgeDocument).where(KnowledgeDocument.active.is_(True))
            ).all())
            valid_chunk_ids = {
                chunk.id
                for chunk in session.scalars(select(KnowledgeChunk)).all()
            }
            results = []
            for case in CASES:
                started = perf_counter()
                output = search_knowledge(
                    session, KnowledgeSearchInput(query=case["query"], top_k=top_k),
                    embeddings, candidate_k=settings.knowledge_candidate_k,
                    min_score=min_score,
                )
                results.append({
                    "id": case["id"], "query": case["query"],
                    "expected_title": case["expected_title"],
                    "expect_no_answer": case["expect_no_answer"],
                    "no_answer": output.no_answer,
                    "hits": [hit.model_dump(mode="json") for hit in output.hits],
                    "latency_ms": round((perf_counter() - started) * 1000, 3),
                })
    finally:
        engine.dispose()

    answerable = [item for item in results if not item["expect_no_answer"]]
    unanswerable = [item for item in results if item["expect_no_answer"]]
    hit_at_1 = sum(
        1 for item in answerable
        if item["hits"] and item["hits"][0]["title"] == item["expected_title"]
    )
    hit_at_3 = sum(
        1 for item in answerable
        if any(hit["title"] == item["expected_title"] for hit in item["hits"][:3])
    )
    no_answer_correct = sum(
        1 for item in unanswerable if item["no_answer"] and not item["hits"]
    )
    answerable_false_no_answer = sum(
        1 for item in answerable if item["no_answer"]
    )
    returned_chunk_ids = [
        hit["chunk_id"] for item in results for hit in item["hits"]
    ]
    citation_validity = (
        sum(1 for chunk_id in returned_chunk_ids if chunk_id in valid_chunk_ids)
        / len(returned_chunk_ids)
        if returned_chunk_ids else 1.0
    )
    metrics = {
        "hit_at_1": round(hit_at_1 / len(answerable), 4) if answerable else 0.0,
        "hit_at_3": round(hit_at_3 / len(answerable), 4) if answerable else 0.0,
        "no_answer_accuracy": round(no_answer_correct / len(unanswerable), 4) if unanswerable else 1.0,
        "answerable_false_no_answer": answerable_false_no_answer,
        "citation_validity": round(citation_validity, 4),
        "mean_latency_ms": round(
            sum(item["latency_ms"] for item in results) / len(results), 3
        ) if results else 0.0,
    }
    passed = (
        hit_at_3 == len(answerable)
        and no_answer_correct == len(unanswerable)
        and citation_validity == 1.0
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "offline_hashing_retrieval",
        "network_calls": 0,
        "seeded": seeded,
        "documents": document_count,
        "embedding_model": embeddings.model,
        "top_k": top_k,
        "min_score": min_score,
        "cases": results,
        "metrics": metrics,
        "passed": passed,
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="ProfitOps offline RAG retrieval evaluation")
    parser.add_argument("--json", type=Path, default=None, help="write the JSON report here")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=0.15)
    args = parser.parse_args(argv)
    report = run_evaluation(top_k=args.top_k, min_score=args.min_score)
    output = args.json or REPORT_DIR / "rag-latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    metrics = report["metrics"]
    print("ProfitOps RAG retrieval evaluation (offline, hashing embeddings)")
    print(f"documents={report['documents']} cases={len(report['cases'])} network_calls=0")
    print(
        f"hit@1={metrics['hit_at_1']:.2%} hit@3={metrics['hit_at_3']:.2%} "
        f"no_answer_accuracy={metrics['no_answer_accuracy']:.2%} "
        f"citation_validity={metrics['citation_validity']:.2%}"
    )
    for item in report["cases"]:
        top = item["hits"][0]["title"] if item["hits"] else "无可靠答案"
        mark = "PASS" if (
            item["no_answer"] if item["expect_no_answer"]
            else any(hit["title"] == item["expected_title"] for hit in item["hits"][:3])
        ) else "FAIL"
        print(f"  {mark} {item['id']}: {top} ({item['latency_ms']:.1f} ms)")
    print(f"report={output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
