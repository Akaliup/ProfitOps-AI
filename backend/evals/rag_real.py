"""Real semantic retrieval comparison: BGE-M3 (Ollama) vs hashing baseline.

The six SOP documents are the synthetic seed corpus from ``app.knowledge``.
Queries and labels are fixed in ``rag_queries.json`` before any embedding is
generated. Each provider runs against its own disposable SQLite database, so
the comparison never touches ``data/profitops.db``.

BGE-M3 is used through the existing OpenAI-compatible embedding provider
(``POST {base_url}/embeddings``). No random vector or mock is ever labelled as
BGE-M3. If Ollama or the model is unavailable, the report records ``BLOCKED``
with the real error and still runs the hashing baseline.

Run from the project root:
  .venv/Scripts/python.exe backend/evals/rag_real.py --provider both \
      --json docs/rag-real-embedding-2026-11-02.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import build_engine, initialize_database, make_session_factory  # noqa: E402
from app.knowledge import (  # noqa: E402
    HashingEmbeddingProvider, OpenAIEmbeddingProvider, search_knowledge, seed_knowledge,
)
from app.models import KnowledgeChunk  # noqa: E402
from app.schemas import KnowledgeSearchInput  # noqa: E402

QUERY_FILE = BACKEND / "evals" / "rag_queries.json"
REPORT_DIR = BACKEND / "evals" / "reports"
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
TOP_K = 3
MIN_SCORE = 0.15
CANDIDATE_K = 20


class TimingEmbeddingProvider:
    """Measure embedding work without changing the production provider."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.embed_seconds = 0.0
        self.embed_calls = 0
        self.vector_count = 0
        self.last_seconds = 0.0

    @property
    def model(self) -> str:
        return str(self.delegate.model)

    @property
    def dimensions(self) -> int | None:
        return getattr(self.delegate, "dimensions", None)

    def embed(self, texts: list[str]) -> list[list[float]]:
        started = perf_counter()
        vectors = self.delegate.embed(texts)
        elapsed = perf_counter() - started
        self.last_seconds = elapsed
        self.embed_seconds += elapsed
        self.embed_calls += 1
        self.vector_count += len(texts)
        return vectors


def load_queries(path: Path = QUERY_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def _latency_stats(values: list[float]) -> dict[str, float | None]:
    return {
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "mean_ms": round(sum(values) / len(values), 3) if values else None,
    }


def _provider_settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        auto_seed=False,
        knowledge_embedding_provider="hashing",
        knowledge_embedding_model="hashing-v1",
        knowledge_top_k=TOP_K,
        knowledge_candidate_k=CANDIDATE_K,
        knowledge_min_score=MIN_SCORE,
    )


def _run_provider(
    *, label: str, provider: Any, queries: list[dict[str, Any]], settings: Settings,
    preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timing = TimingEmbeddingProvider(provider)
    with tempfile.TemporaryDirectory(prefix=f"profitops-rag-{label}-") as temp:
        database = Path(temp) / "rag.db"
        engine = build_engine(f"sqlite:///{database}")
        initialize_database(engine)
        factory = make_session_factory(engine)
        try:
            seed_started = perf_counter()
            seeded = seed_knowledge(factory, timing, settings)
            seed_wall_ms = round((perf_counter() - seed_started) * 1000, 3)
            with factory() as session:
                valid_chunk_ids = {
                    chunk.id for chunk in session.scalars(select(KnowledgeChunk)).all()
                }
                results: list[dict[str, Any]] = []
                for query in queries:
                    output = search_knowledge(
                        session,
                        KnowledgeSearchInput(query=query["query"], top_k=TOP_K),
                        timing,
                        candidate_k=CANDIDATE_K,
                        min_score=MIN_SCORE,
                    )
                    hits = [hit.model_dump(mode="json") for hit in output.hits]
                    first_correct_rank = next(
                        (
                            rank for rank, hit in enumerate(hits, start=1)
                            if hit["title"] == query["expected_title"]
                        ),
                        None,
                    )
                    results.append({
                        **query,
                        "no_answer": output.no_answer,
                        "hits": hits,
                        "retrieval_ms": output.retrieval_ms,
                        "query_embedding_ms": round(timing.last_seconds * 1000, 3),
                        "first_correct_rank": first_correct_rank,
                        "hit_at_1": bool(
                            first_correct_rank == 1 and not query["expect_no_answer"]
                        ),
                        "hit_at_3": bool(
                            first_correct_rank is not None and first_correct_rank <= 3
                            and not query["expect_no_answer"]
                        ),
                    })
        finally:
            engine.dispose()

    answerable = [item for item in results if not item["expect_no_answer"]]
    unanswerable = [item for item in results if item["expect_no_answer"]]
    returned_ids = [hit["chunk_id"] for item in results for hit in item["hits"]]
    citation_validity = (
        sum(1 for chunk_id in returned_ids if chunk_id in valid_chunk_ids) / len(returned_ids)
        if returned_ids else 1.0
    )
    failures: list[dict[str, Any]] = []
    for item in results:
        if item["expect_no_answer"] and not item["no_answer"]:
            failures.append({
                "id": item["id"], "query": item["query"], "type": "false_answer",
                "expected": "no_answer",
                "top_title": item["hits"][0]["title"] if item["hits"] else None,
                "top_score": item["hits"][0]["score"] if item["hits"] else None,
            })
        elif not item["expect_no_answer"] and item["no_answer"]:
            failures.append({
                "id": item["id"], "query": item["query"], "type": "false_no_answer",
                "expected_title": item["expected_title"],
            })
        elif not item["expect_no_answer"] and not item["hit_at_3"]:
            failures.append({
                "id": item["id"], "query": item["query"], "type": "miss_at_3",
                "expected_title": item["expected_title"],
                "top_title": item["hits"][0]["title"] if item["hits"] else None,
                "top_score": item["hits"][0]["score"] if item["hits"] else None,
            })
        elif not item["expect_no_answer"] and not item["hit_at_1"]:
            failures.append({
                "id": item["id"], "query": item["query"], "type": "rank_miss_at_1",
                "expected_title": item["expected_title"],
                "first_correct_rank": item["first_correct_rank"],
                "top_title": item["hits"][0]["title"] if item["hits"] else None,
            })

    mrr = (
        sum(1.0 / item["first_correct_rank"] for item in answerable if item["first_correct_rank"])
        / len(answerable)
        if answerable else 0.0
    )
    retrieval_values = [float(item["retrieval_ms"]) for item in results]
    embedding_values = [float(item["query_embedding_ms"]) for item in results]
    return {
        "provider": label,
        "model": timing.model,
        "status": "COMPLETED",
        "preflight": preflight,
        "seeded": seeded,
        "documents": 6,
        "chunks": len(valid_chunk_ids),
        "dimensions": timing.dimensions,
        "top_k": TOP_K,
        "min_score": MIN_SCORE,
        "candidate_k": CANDIDATE_K,
        "queries": len(results),
        "metrics": {
            "hit_at_1": round(sum(1 for item in answerable if item["hit_at_1"]) / len(answerable), 4)
            if answerable else 0.0,
            "hit_at_3": round(sum(1 for item in answerable if item["hit_at_3"]) / len(answerable), 4)
            if answerable else 0.0,
            "mrr": round(mrr, 4),
            "no_answer_accuracy": round(
                sum(1 for item in unanswerable if item["no_answer"] and not item["hits"])
                / len(unanswerable), 4,
            ) if unanswerable else 1.0,
            "answerable_false_no_answer": sum(
                1 for item in answerable if item["no_answer"]
            ),
            "citation_validity": round(citation_validity, 4),
            "retrieval_latency": _latency_stats(retrieval_values),
            "query_embedding_latency": _latency_stats(embedding_values),
            "embedding_generation": {
                "seed_wall_ms": seed_wall_ms,
                "embed_seconds": round(timing.embed_seconds, 3),
                "embed_calls": timing.embed_calls,
                "vectors": timing.vector_count,
                "ms_per_vector": round(
                    timing.embed_seconds * 1000 / timing.vector_count, 3
                ) if timing.vector_count else None,
            },
        },
        "failures": failures,
        "results": results,
    }


def _blocked(label: str, model: str, base_url: str, error: Exception) -> dict[str, Any]:
    return {
        "provider": label,
        "model": model,
        "status": "BLOCKED",
        "blocked_reason": f"{type(error).__name__}: {error}",
        "endpoint": base_url,
        "metrics": None,
        "failures": [],
        "results": [],
    }


def run(*, include_bge: bool = True, ollama_base_url: str = OLLAMA_BASE_URL) -> dict[str, Any]:
    dataset = load_queries()
    queries = dataset["queries"]
    settings = _provider_settings()

    hashing = HashingEmbeddingProvider(
        dimensions=settings.knowledge_embedding_dimensions,
        model="hashing-v1",
    )
    hashing_report = _run_provider(
        label="hashing", provider=hashing, queries=queries, settings=settings,
        preflight={"status": "not_required", "detail": "deterministic local hashing"},
    )

    bge_report: dict[str, Any] | None = None
    if include_bge:
        delegate = OpenAIEmbeddingProvider(
            base_url=ollama_base_url,
            api_key="",
            model="bge-m3",
            timeout=30.0,
        )
        try:
            started = perf_counter()
            vectors = delegate.embed(["连通性检查"])
            preflight = {
                "status": "PASS",
                "http_status": 200,
                "latency_ms": round((perf_counter() - started) * 1000, 3),
                "dimensions": len(vectors[0]) if vectors else None,
            }
            bge_report = _run_provider(
                label="bge-m3", provider=delegate, queries=queries, settings=settings,
                preflight=preflight,
            )
        except Exception as error:  # noqa: BLE001 - report the real environment failure
            bge_report = _blocked("bge-m3", "bge-m3", ollama_base_url, error)

    comparison: dict[str, Any] = {
        "hashing_status": hashing_report["status"],
        "bge_m3_status": bge_report["status"] if bge_report else "NOT_RUN",
    }
    if bge_report and bge_report["status"] == "COMPLETED":
        for metric in ("hit_at_1", "hit_at_3", "mrr", "no_answer_accuracy", "citation_validity"):
            comparison[f"delta_{metric}"] = round(
                bge_report["metrics"][metric] - hashing_report["metrics"][metric], 4
            )
        comparison["delta_retrieval_p50_ms"] = round(
            (bge_report["metrics"]["retrieval_latency"]["p50_ms"] or 0.0)
            - (hashing_report["metrics"]["retrieval_latency"]["p50_ms"] or 0.0), 3,
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "real_semantic_retrieval_comparison",
        "dataset": {
            "dataset_id": dataset["dataset_id"],
            "label_version": dataset["label_version"],
            "fixed_before_run": dataset["fixed_before_run"],
            "queries": len(queries),
            "answerable": sum(1 for query in queries if not query["expect_no_answer"]),
            "unanswerable": sum(1 for query in queries if query["expect_no_answer"]),
            "notes": dataset["notes"],
        },
        "corpus": {
            "source": "backend/app/knowledge.py SEED_DOCUMENTS",
            "documents": 6,
            "synthetic": True,
            "database": "disposable temporary SQLite per provider",
        },
        "thresholds": {
            "top_k": TOP_K, "candidate_k": CANDIDATE_K, "min_score": MIN_SCORE,
            "note": "Same threshold for both providers; no threshold tuning after seeing results.",
        },
        "hashing": hashing_report,
        "bge_m3": bge_report,
        "comparison": comparison,
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="BGE-M3 vs hashing retrieval evaluation")
    parser.add_argument("--provider", choices=("both", "hashing", "bge-m3"), default="both")
    parser.add_argument("--ollama-base-url", default=OLLAMA_BASE_URL)
    parser.add_argument("--json", type=Path, default=REPORT_DIR / "rag-real-latest.json")
    parser.add_argument("--require-bge-m3", action="store_true")
    args = parser.parse_args(argv)
    report = run(
        include_bge=args.provider in ("both", "bge-m3"),
        ollama_base_url=args.ollama_base_url,
    )
    if args.provider == "bge-m3":
        report.pop("hashing", None)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    hashing = report.get("hashing")
    if hashing:
        metrics = hashing["metrics"]
        print(
            f"hashing: hit@1={metrics['hit_at_1']:.2%} hit@3={metrics['hit_at_3']:.2%} "
            f"MRR={metrics['mrr']:.4f} no_answer={metrics['no_answer_accuracy']:.2%} "
            f"p50={metrics['retrieval_latency']['p50_ms']}ms"
        )
    bge = report.get("bge_m3")
    if bge:
        if bge["status"] == "COMPLETED":
            metrics = bge["metrics"]
            print(
                f"bge-m3: hit@1={metrics['hit_at_1']:.2%} hit@3={metrics['hit_at_3']:.2%} "
                f"MRR={metrics['mrr']:.4f} no_answer={metrics['no_answer_accuracy']:.2%} "
                f"p50={metrics['retrieval_latency']['p50_ms']}ms"
            )
        else:
            print(f"bge-m3: BLOCKED ({bge['blocked_reason']})")
    print(f"report={args.json}")
    if args.require_bge_m3 and (not bge or bge["status"] != "COMPLETED"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
