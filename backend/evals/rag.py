"""Offline RAG retrieval evaluation over isolated calibration and test splits.

This evaluates retrieval only, never an LLM. It seeds the six synthetic SOP
documents into a disposable in-memory SQLite database and runs the fixed query
sets from ``rag_calibration.json`` and ``rag_test.json`` through the production
``seed_knowledge`` and ``search_knowledge`` code. Thresholds may be inspected on
the calibration split, but the test split is evaluated with the same frozen
thresholds and is not used for tuning.

Two ranking strategies are measured on the same corpus, queries and thresholds:

* ``weighted``    - the previous single-ranking blend (0.65 cosine + 0.35 lexical)
* ``hybrid_rrf``  - dense ranking + BM25 fused with reciprocal rank fusion

Reported metrics: hit@1, hit@3, MRR, nDCG@5, abstention (no_answer) accuracy,
answerable false rejections, citation validity, retrieval latency, and a
per-query-type breakdown (keyword / paraphrase / cross_doc / unanswerable).

Run from the project root:
  .venv/Scripts/python.exe backend/evals/rag.py
  .venv/Scripts/python.exe backend/evals/rag.py --json backend/evals/reports/rag.json
  .venv/Scripts/python.exe backend/evals/rag.py --mode weighted

No network call is made: the hashing embedding provider is forced.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
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
    HashingEmbeddingProvider, OpenAIEmbeddingProvider, SEED_DOCUMENTS,
    search_knowledge, seed_knowledge,
)
from app.models import KnowledgeChunk, KnowledgeDocument  # noqa: E402
from app.schemas import KnowledgeSearchInput  # noqa: E402

CALIBRATION_FILE = BACKEND / "evals" / "rag_calibration.json"
TEST_FILE = BACKEND / "evals" / "rag_test.json"
LEGACY_QUERY_FILE = BACKEND / "evals" / "rag_queries.json"
QUERY_FILE = CALIBRATION_FILE
DATASET_FILES = {
    "calibration": CALIBRATION_FILE,
    "test": TEST_FILE,
    "legacy": LEGACY_QUERY_FILE,
}
REPORT_DIR = BACKEND / "evals" / "reports"
MODES = ("weighted", "hybrid_rrf")
PRIMARY_MODE = "hybrid_rrf"
QUERY_TYPES = ("keyword", "paraphrase", "cross_doc", "unanswerable")
HIT_K = 3
NDCG_K = 5
MIN_TEST_CROSS_DOC = 4
MIN_TEST_UNANSWERABLE = 4
MIN_TEST_ANSWERABLE = 16


def load_dataset(path: Path = QUERY_FILE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dataset_summary(data: dict[str, Any]) -> dict[str, Any]:
    queries = data["queries"]
    return {
        "dataset_id": data["dataset_id"],
        "split": data.get("split"),
        "label_version": data["label_version"],
        "type_label_version": data.get("type_label_version"),
        "fixed_before_run": data["fixed_before_run"],
        "labels_frozen_at": data.get("labels_frozen_at"),
        "queries": len(queries),
        "answerable": sum(1 for query in queries if not query["expect_no_answer"]),
        "unanswerable": sum(1 for query in queries if query["expect_no_answer"]),
        "by_type": {
            query_type: sum(1 for query in queries if query.get("type") == query_type)
            for query_type in QUERY_TYPES
        },
        "known_failures": data.get("known_failures", []),
        "notes": data["notes"],
    }


def validate_splits(calibration: dict[str, Any], test: dict[str, Any]) -> dict[str, Any]:
    """Prove that calibration and test are disjoint and that the holdout is usable."""
    calibration_queries = calibration["queries"]
    test_queries = test["queries"]
    calibration_ids = {query["id"] for query in calibration_queries}
    test_ids = {query["id"] for query in test_queries}
    calibration_texts = {query["query"].strip() for query in calibration_queries}
    test_texts = {query["query"].strip() for query in test_queries}
    corpus_titles = {document["title"] for document in SEED_DOCUMENTS}
    calibration_titles = {
        query["expected_title"] for query in calibration_queries
        if not query["expect_no_answer"]
    }
    test_titles = {
        query["expected_title"] for query in test_queries
        if not query["expect_no_answer"]
    }
    known_failures = test.get("known_failures", [])
    known_failure_ids = {item["id"] for item in known_failures}
    test_cross_doc = sum(1 for query in test_queries if query.get("type") == "cross_doc")
    test_unanswerable = sum(1 for query in test_queries if query["expect_no_answer"])
    test_answerable = len(test_queries) - test_unanswerable
    checks = {
        "ids_disjoint": not (calibration_ids & test_ids),
        "queries_disjoint": not (calibration_texts & test_texts),
        "calibration_fixed_before_run": calibration.get("fixed_before_run") is True,
        "test_fixed_before_run": test.get("fixed_before_run") is True,
        "calibration_covers_all_documents": calibration_titles == corpus_titles,
        "test_covers_all_documents": test_titles == corpus_titles,
        "test_cross_doc_minimum": test_cross_doc >= MIN_TEST_CROSS_DOC,
        "test_unanswerable_minimum": test_unanswerable >= MIN_TEST_UNANSWERABLE,
        "test_answerable_minimum": test_answerable >= MIN_TEST_ANSWERABLE,
        "known_failures_declared": bool(known_failures),
        "known_failures_in_test": known_failure_ids <= test_ids,
        "known_failure_ids_unique": len(known_failure_ids) == len(known_failures),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "calibration_ids": sorted(calibration_ids),
        "test_ids": sorted(test_ids),
        "test_counts": {
            "answerable": test_answerable,
            "cross_doc": test_cross_doc,
            "unanswerable": test_unanswerable,
        },
        "known_failure_ids": sorted(known_failure_ids),
    }


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


def _first_correct_rank(hits: list[dict[str, Any]], expected_title: str | None) -> int | None:
    if not expected_title:
        return None
    for rank, hit in enumerate(hits, start=1):
        if hit["title"] == expected_title:
            return rank
    return None


def _ndcg_at_k(rank: int | None, k: int = NDCG_K) -> float:
    """nDCG@k with binary relevance and a single relevant document per query.

    IDCG@k is therefore 1.0 (the relevant chunk at rank 1), and DCG is
    ``1 / log2(rank + 1)`` when the chunk is retrieved inside the window.
    """
    if rank is None or rank > k:
        return 0.0
    return 1.0 / math.log2(rank + 1)


def _coverage_at_k(hits: list[dict[str, Any]], titles: list[str], k: int = HIT_K) -> bool:
    seen = {hit["title"] for hit in hits[:k]}
    return all(title in seen for title in titles)


def _summarise(results: list[dict[str, Any]], *, valid_chunk_ids: set[str]) -> dict[str, Any]:
    answerable = [item for item in results if not item["expect_no_answer"]]
    unanswerable = [item for item in results if item["expect_no_answer"]]

    def ratio(hits: int, total: int) -> float:
        return round(hits / total, 4) if total else 0.0

    returned_ids = [hit["chunk_id"] for item in results for hit in item["hits"]]
    citation_validity = (
        sum(1 for chunk_id in returned_ids if chunk_id in valid_chunk_ids) / len(returned_ids)
        if returned_ids else 1.0
    )
    metrics = {
        "hit_at_1": ratio(sum(1 for item in answerable if item["hit_at_1"]), len(answerable)),
        "hit_at_3": ratio(sum(1 for item in answerable if item["hit_at_3"]), len(answerable)),
        "mrr": round(
            sum(
                1.0 / item["first_correct_rank"]
                for item in answerable if item["first_correct_rank"]
            ) / len(answerable), 4,
        ) if answerable else 0.0,
        "ndcg_at_5": round(
            sum(item["ndcg_at_5"] for item in answerable) / len(answerable), 4
        ) if answerable else 0.0,
        "no_answer_accuracy": ratio(
            sum(1 for item in unanswerable if item["no_answer"] and not item["hits"]),
            len(unanswerable),
        ),
        "answerable_false_no_answer": sum(1 for item in answerable if item["no_answer"]),
        "citation_validity": round(citation_validity, 4),
        "retrieval_latency": _latency_stats([float(item["latency_ms"]) for item in results]),
    }

    per_type: dict[str, Any] = {}
    for query_type in QUERY_TYPES:
        bucket = [item for item in results if item["type"] == query_type]
        if not bucket:
            per_type[query_type] = {"queries": 0}
            continue
        entry: dict[str, Any] = {"queries": len(bucket)}
        if query_type == "unanswerable":
            entry["no_answer_accuracy"] = ratio(
                sum(1 for item in bucket if item["no_answer"] and not item["hits"]), len(bucket)
            )
        else:
            entry.update({
                "hit_at_1": ratio(sum(1 for item in bucket if item["hit_at_1"]), len(bucket)),
                "hit_at_3": ratio(sum(1 for item in bucket if item["hit_at_3"]), len(bucket)),
                "mrr": round(
                    sum(1.0 / item["first_correct_rank"] for item in bucket if item["first_correct_rank"])
                    / len(bucket), 4,
                ),
                "ndcg_at_5": round(sum(item["ndcg_at_5"] for item in bucket) / len(bucket), 4),
            })
            if query_type == "cross_doc":
                entry["coverage_at_3"] = ratio(
                    sum(1 for item in bucket if item["coverage_at_3"]), len(bucket)
                )
        per_type[query_type] = entry

    # Threshold calibration: the abstention threshold is an absolute score, so it
    # only transfers across embedding models if the two distributions stay
    # separated. Report the observed gap instead of assuming it.
    max_unanswerable = max(
        (item["top_confidence"] for item in unanswerable), default=0.0
    )
    min_answerable = min(
        (item["top_confidence"] for item in answerable), default=0.0
    )
    separated = max_unanswerable < min_answerable
    calibration = {
        "max_unanswerable_top_confidence": round(max_unanswerable, 4),
        "min_answerable_top_confidence": round(min_answerable, 4),
        "separated": separated,
        "suggested_min_score": (
            round((max_unanswerable + min_answerable) / 2, 4) if separated else None
        ),
    }

    return {"metrics": metrics, "per_type": per_type, "calibration": calibration}


def _run_mode(
    mode: str,
    queries: list[dict[str, Any]],
    *,
    top_k: int,
    min_score: float,
    candidate_k: int,
    rrf_k: int,
    bm25_k1: float,
    bm25_b: float,
    max_per_document: int,
    embeddings: Any | None = None,
) -> dict[str, Any]:
    settings = Settings(
        database_url="sqlite:///:memory:",
        auto_seed=False,
        knowledge_embedding_provider="hashing",
        knowledge_embedding_model="hashing-v1",
        knowledge_top_k=top_k,
        knowledge_candidate_k=candidate_k,
        knowledge_min_score=min_score,
        knowledge_retrieval_mode=mode,
        knowledge_rrf_k=rrf_k,
        knowledge_bm25_k1=bm25_k1,
        knowledge_bm25_b=bm25_b,
    )
    embeddings = embeddings or HashingEmbeddingProvider(
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
                chunk.id for chunk in session.scalars(select(KnowledgeChunk)).all()
            }
            results: list[dict[str, Any]] = []
            rankers: list[str] = []
            for query in queries:
                started = perf_counter()
                output = search_knowledge(
                    session,
                    KnowledgeSearchInput(query=query["query"], top_k=top_k),
                    embeddings,
                    candidate_k=candidate_k,
                    min_score=min_score,
                    retrieval_mode=mode,
                    rrf_k=rrf_k,
                    bm25_k1=bm25_k1,
                    bm25_b=bm25_b,
                    max_per_document=max_per_document,
                )
                latency_ms = round((perf_counter() - started) * 1000, 3)
                rankers = list(output.rankers)
                top_confidence = max(
                    (hit.confidence or 0.0 for hit in output.hits), default=0.0
                )
                if output.no_answer and min_score > 0:
                    # Threshold calibration needs the score the production path
                    # suppressed, so re-run once with abstention disabled.
                    probe = search_knowledge(
                        session,
                        KnowledgeSearchInput(query=query["query"], top_k=1),
                        embeddings,
                        candidate_k=candidate_k,
                        min_score=0.0,
                        retrieval_mode=mode,
                        rrf_k=rrf_k,
                        bm25_k1=bm25_k1,
                        bm25_b=bm25_b,
                        max_per_document=max_per_document,
                    )
                    top_confidence = max(
                        (hit.confidence or 0.0 for hit in probe.hits), default=0.0
                    )
                hits = [hit.model_dump(mode="json") for hit in output.hits]
                rank = _first_correct_rank(hits, query.get("expected_title"))
                expected_titles = [query["expected_title"], *query.get("secondary_titles", [])]
                results.append({
                    "id": query["id"],
                    "query": query["query"],
                    "type": query.get("type", "keyword"),
                    "expected_title": query.get("expected_title"),
                    "secondary_titles": query.get("secondary_titles", []),
                    "expect_no_answer": query["expect_no_answer"],
                    "no_answer": output.no_answer,
                    "hits": hits,
                    "first_correct_rank": rank,
                    "hit_at_1": bool(rank == 1 and not query["expect_no_answer"]),
                    "hit_at_3": bool(rank is not None and rank <= HIT_K and not query["expect_no_answer"]),
                    "ndcg_at_5": round(_ndcg_at_5(rank), 4) if not query["expect_no_answer"] else 0.0,
                    "coverage_at_3": bool(
                        query.get("secondary_titles")
                        and _coverage_at_k(hits, expected_titles)
                    ),
                    "latency_ms": latency_ms,
                    "top_confidence": round(top_confidence, 4),
                })
    finally:
        engine.dispose()

    summary = _summarise(results, valid_chunk_ids=valid_chunk_ids)
    return {
        "retrieval_mode": mode,
        "seeded": seeded,
        "documents": document_count,
        "chunks": len(valid_chunk_ids),
        "embedding_model": embeddings.model,
        "rankers": rankers,
        "queries": len(results),
        **summary,
        "results": results,
    }


def _ndcg_at_5(rank: int | None) -> float:
    return _ndcg_at_k(rank, NDCG_K)


def _failures(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for item in results:
        if item["expect_no_answer"] and not item["no_answer"]:
            failures.append({
                "id": item["id"], "type": "false_answer",
                "query": item["query"],
                "top_title": item["hits"][0]["title"] if item["hits"] else None,
                "top_confidence": item["hits"][0]["confidence"] if item["hits"] else None,
            })
        elif not item["expect_no_answer"] and item["no_answer"]:
            failures.append({
                "id": item["id"], "type": "false_no_answer",
                "query": item["query"], "expected_title": item["expected_title"],
            })
        elif not item["expect_no_answer"] and not item["hit_at_3"]:
            failures.append({
                "id": item["id"], "type": "miss_at_3",
                "query": item["query"], "expected_title": item["expected_title"],
                "rank": item["first_correct_rank"],
                "top_title": item["hits"][0]["title"] if item["hits"] else None,
            })
        elif not item["expect_no_answer"] and not item["hit_at_1"]:
            failures.append({
                "id": item["id"], "type": "rank_miss_at_1",
                "query": item["query"], "expected_title": item["expected_title"],
                "rank": item["first_correct_rank"],
                "top_title": item["hits"][0]["title"] if item["hits"] else None,
            })
    return failures


def _gates(
    primary: dict[str, Any], baseline: dict[str, Any] | None, gate_mode: str,
) -> list[dict[str, Any]]:
    metrics = primary["metrics"]
    if gate_mode == "test":
        gates = [
            {
                "name": "hit_at_3_minimum",
                "passed": metrics["hit_at_3"] >= 0.9,
                "detail": f"{metrics['hit_at_3']:.4f} (must be >= 0.9)",
            },
            {
                "name": "no_answer_full",
                "passed": metrics["no_answer_accuracy"] == 1.0,
                "detail": f"{metrics['no_answer_accuracy']:.4f} (must be 1.0)",
            },
            {
                "name": "no_false_rejection",
                "passed": metrics["answerable_false_no_answer"] == 0,
                "detail": f"{metrics['answerable_false_no_answer']} answerable queries refused",
            },
            {
                "name": "citation_validity",
                "passed": metrics["citation_validity"] == 1.0,
                "detail": f"{metrics['citation_validity']:.4f} (must be 1.0)",
            },
        ]
    else:
        gates = [
            {
                "name": "hit_at_3_full",
                "passed": metrics["hit_at_3"] == 1.0,
                "detail": f"{metrics['hit_at_3']:.4f} (must be 1.0)",
            },
            {
                "name": "no_answer_full",
                "passed": metrics["no_answer_accuracy"] == 1.0,
                "detail": f"{metrics['no_answer_accuracy']:.4f} (must be 1.0)",
            },
            {
                "name": "no_false_rejection",
                "passed": metrics["answerable_false_no_answer"] == 0,
                "detail": f"{metrics['answerable_false_no_answer']} answerable queries refused",
            },
            {
                "name": "citation_validity",
                "passed": metrics["citation_validity"] == 1.0,
                "detail": f"{metrics['citation_validity']:.4f} (must be 1.0)",
            },
        ]
    if baseline is not None:
        gates.append({
            "name": "no_rank_regression",
            "passed": metrics["mrr"] >= baseline["metrics"]["mrr"],
            "detail": (
                f"mrr {metrics['mrr']:.4f} vs weighted "
                f"{baseline['metrics']['mrr']:.4f}"
            ),
        })
    return gates


def run_evaluation(
    *, top_k: int = 3, min_score: float = 0.15, candidate_k: int = 20,
    rrf_k: int = 60, bm25_k1: float = 1.5, bm25_b: float = 0.75,
    max_per_document: int = 2,
    modes: tuple[str, ...] = MODES, dataset: dict[str, Any] | None = None,
    embeddings: Any | None = None, gate_mode: str = "calibration",
) -> dict[str, Any]:
    data = dataset or load_dataset()
    queries = data["queries"]
    runs = {
        mode: _run_mode(
            mode, queries, top_k=top_k, min_score=min_score, candidate_k=candidate_k,
            rrf_k=rrf_k, bm25_k1=bm25_k1, bm25_b=bm25_b,
            max_per_document=max_per_document, embeddings=embeddings,
        )
        for mode in modes
    }
    primary = runs[PRIMARY_MODE] if PRIMARY_MODE in runs else runs[modes[0]]
    baseline = runs.get("weighted")

    gates = _gates(primary, baseline, gate_mode)
    comparison: dict[str, Any] = {}
    if baseline and PRIMARY_MODE in runs:
        for metric in ("hit_at_1", "hit_at_3", "mrr", "ndcg_at_5"):
            comparison[f"delta_{metric}"] = round(
                primary["metrics"][metric] - baseline["metrics"][metric], 4
            )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "offline_retrieval_evaluation",
        "network_calls": 0,
        "dataset": _dataset_summary(data),
        "thresholds": {
            "top_k": top_k,
            "candidate_k": candidate_k,
            "min_score": min_score,
            "rrf_k": rrf_k,
            "bm25_k1": bm25_k1,
            "bm25_b": bm25_b,
            "max_per_document": max_per_document,
            "note": (
                "min_score applies to the per-chunk confidence, not to the RRF score: "
                "RRF is rank-based and cannot express 'nothing relevant found'."
            ),
        },
        "corpus": {
            "source": "backend/app/knowledge.py SEED_DOCUMENTS",
            "documents": primary["documents"],
            "chunks": primary["chunks"],
            "embedding_model": primary["embedding_model"],
            "rankers": {mode: run["rankers"] for mode, run in runs.items()},
            "synthetic": True,
        },
        "primary_mode": PRIMARY_MODE,
        "runs": {
            mode: {key: value for key, value in run.items() if key != "results"}
            for mode, run in runs.items()
        },
        "comparison": comparison,
        "gates": gates,
        "passed": all(gate["passed"] for gate in gates),
        "failures": _failures(primary["results"]),
        "results": primary["results"],
    }


def run_split_evaluation(
    *, top_k: int = 3, min_score: float = 0.15, candidate_k: int = 20,
    rrf_k: int = 60, bm25_k1: float = 1.5, bm25_b: float = 0.75,
    max_per_document: int = 2, modes: tuple[str, ...] = MODES,
    embeddings: Any | None = None,
) -> dict[str, Any]:
    calibration_data = load_dataset(CALIBRATION_FILE)
    test_data = load_dataset(TEST_FILE)
    validation = validate_splits(calibration_data, test_data)
    common = {
        "top_k": top_k, "min_score": min_score, "candidate_k": candidate_k,
        "rrf_k": rrf_k, "bm25_k1": bm25_k1, "bm25_b": bm25_b,
        "max_per_document": max_per_document, "modes": modes,
        "embeddings": embeddings,
    }
    calibration_report = run_evaluation(
        **common, dataset=calibration_data, gate_mode="calibration",
    )
    test_report = run_evaluation(**common, dataset=test_data, gate_mode="test")
    test_primary = test_report["runs"][PRIMARY_MODE]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "offline_split_retrieval_evaluation",
        "network_calls": 0,
        "split_validation": validation,
        "threshold_policy": {
            "tuned_on": "calibration",
            "applied_to_test": True,
            "test_used_for_tuning": False,
            "min_score": min_score,
            "note": (
                "The same retrieval thresholds are applied to both splits. "
                "Calibration metrics may inform future defaults; test labels and "
                "results must not be written back into tuning."
            ),
        },
        "calibration": calibration_report,
        "test": test_report,
        "summary": {
            "calibration": calibration_report["runs"][PRIMARY_MODE]["metrics"],
            "test": test_primary["metrics"],
            "test_cross_doc_coverage_at_3": (
                test_primary["per_type"]["cross_doc"].get("coverage_at_3")
            ),
            "test_failures": test_report["failures"],
        },
        "passed": (
            validation["passed"]
            and calibration_report["passed"]
            and test_report["passed"]
        ),
    }


def _print_single_report(report: dict[str, Any], label: str) -> None:
    dataset = report["dataset"]
    print(f"[{label}] dataset={dataset['dataset_id']} split={dataset['split']}")
    print(f"rankers={report['corpus']['rankers']}")
    print(
        f"documents={report['corpus']['documents']} chunks={report['corpus']['chunks']} "
        f"queries={dataset['queries']} "
        f"(keyword={dataset['by_type']['keyword']} paraphrase={dataset['by_type']['paraphrase']} "
        f"cross_doc={dataset['by_type']['cross_doc']} unanswerable={dataset['by_type']['unanswerable']}) "
        f"network_calls=0"
    )
    for mode, run in report["runs"].items():
        m = run["metrics"]
        print(
            f"[{mode:10s}] hit@1={m['hit_at_1']:.2%} hit@3={m['hit_at_3']:.2%} "
            f"MRR={m['mrr']:.4f} nDCG@5={m['ndcg_at_5']:.4f} "
            f"no_answer={m['no_answer_accuracy']:.2%} "
            f"citation={m['citation_validity']:.2%} p50={m['retrieval_latency']['p50_ms']}ms"
        )
        for query_type, entry in run["per_type"].items():
            if not entry.get("queries"):
                continue
            if query_type == "unanswerable":
                print(f"    {query_type:13s} n={entry['queries']:2d} no_answer={entry['no_answer_accuracy']:.2%}")
            else:
                extra = (
                    f" coverage@3={entry['coverage_at_3']:.2%}"
                    if "coverage_at_3" in entry else ""
                )
                print(
                    f"    {query_type:13s} n={entry['queries']:2d} hit@1={entry['hit_at_1']:.2%} "
                    f"MRR={entry['mrr']:.4f}{extra}"
                )
    for mode, run in report["runs"].items():
        cal = run["calibration"]
        print(
            f"    calibration[{mode}] max_unanswerable={cal['max_unanswerable_top_confidence']} "
            f"min_answerable={cal['min_answerable_top_confidence']} "
            f"separated={cal['separated']} suggested_min_score={cal['suggested_min_score']}"
        )
    if report["comparison"]:
        delta = report["comparison"]
        print(
            "delta (hybrid_rrf - weighted): "
            f"hit@1 {delta.get('delta_hit_at_1', 0):+.4f} "
            f"MRR {delta.get('delta_mrr', 0):+.4f} "
            f"nDCG@5 {delta.get('delta_ndcg_at_5', 0):+.4f}"
        )
    for gate in report["gates"]:
        print(f"  {'PASS' if gate['passed'] else 'FAIL'} gate {gate['name']}: {gate['detail']}")
    if report["failures"]:
        print(f"failures ({len(report['failures'])}):")
        for item in report["failures"]:
            print(f"  - {item['type']} {item['id']}: {item.get('expected_title') or ''}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="ProfitOps offline RAG retrieval evaluation")
    parser.add_argument("--json", type=Path, default=None, help="write the JSON report here")
    parser.add_argument(
        "--dataset", choices=("split", *DATASET_FILES), default="split",
        help="evaluate the frozen calibration/test split, one split, or the legacy 42-query set",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-score", type=float, default=0.15)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--mode", choices=("both", *MODES), default="both")
    parser.add_argument("--embedding-provider", choices=("hashing", "openai"), default="hashing")
    parser.add_argument("--embedding-base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--embedding-model", default="qwen3-embedding:0.6b")
    parser.add_argument("--embedding-api-key", default="")
    args = parser.parse_args(argv)

    modes = MODES if args.mode == "both" else (args.mode,)
    embeddings: Any = None
    if args.embedding_provider == "openai":
        embeddings = OpenAIEmbeddingProvider(
            base_url=args.embedding_base_url, api_key=args.embedding_api_key,
            model=args.embedding_model, timeout=60.0,
        )
        try:
            embeddings.embed(["连通性检查"])
        except Exception as error:  # noqa: BLE001 - report the real environment failure
            print(f"embedding provider BLOCKED: {type(error).__name__}: {error}")
            return 2
    common = {
        "top_k": args.top_k,
        "min_score": args.min_score,
        "candidate_k": args.candidate_k,
        "rrf_k": args.rrf_k,
        "modes": modes,
        "embeddings": embeddings,
    }
    if args.dataset == "split":
        report = run_split_evaluation(**common)
        output = args.json or REPORT_DIR / "rag-split-latest.json"
    else:
        data = load_dataset(DATASET_FILES[args.dataset])
        report = run_evaluation(
            **common, dataset=data,
            gate_mode="test" if args.dataset == "test" else "calibration",
        )
        default_name = "rag-latest.json" if args.dataset == "legacy" else f"rag-{args.dataset}-latest.json"
        output = args.json or REPORT_DIR / default_name
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.dataset == "split":
        validation = report["split_validation"]
        print(
            "ProfitOps RAG split evaluation "
            f"(offline, embeddings={report['calibration']['corpus']['embedding_model']})"
        )
        print(f"split_validation={'PASS' if validation['passed'] else 'FAIL'}")
        for name, passed in validation["checks"].items():
            print(f"  {'PASS' if passed else 'FAIL'} {name}")
        _print_single_report(report["calibration"], "CALIBRATION")
        _print_single_report(report["test"], "TEST")
        policy = report["threshold_policy"]
        print(
            f"thresholds tuned_on={policy['tuned_on']} "
            f"test_used_for_tuning={policy['test_used_for_tuning']}"
        )
        print(
            f"test_cross_doc_coverage_at_3="
            f"{report['summary']['test_cross_doc_coverage_at_3']}"
        )
        print(f"report={output}")
        return 0 if report["passed"] else 1

    print("ProfitOps RAG retrieval evaluation (offline)")
    _print_single_report(report, args.dataset.upper())
    print(f"report={output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
