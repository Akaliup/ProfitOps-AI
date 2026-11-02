from evals.rag_real import load_queries, run
from app.knowledge import SEED_DOCUMENTS


def test_labelled_query_set_is_fixed_and_covers_the_corpus():
    dataset = load_queries()
    queries = dataset["queries"]
    titles = {document["title"] for document in SEED_DOCUMENTS}
    assert dataset["fixed_before_run"] is True
    assert len(queries) >= 30
    assert len({query["id"] for query in queries}) == len(queries)
    assert {query["expected_title"] for query in queries if not query["expect_no_answer"]} <= titles
    assert sum(1 for query in queries if query["expect_no_answer"]) >= 6


def test_hashing_baseline_runs_offline_and_reports_all_metrics():
    report = run(include_bge=False)
    hashing = report["hashing"]
    assert hashing["status"] == "COMPLETED"
    assert hashing["documents"] == 6
    assert hashing["queries"] == len(load_queries()["queries"])
    metrics = hashing["metrics"]
    for key in (
        "hit_at_1", "hit_at_3", "mrr", "no_answer_accuracy",
        "citation_validity", "retrieval_latency", "embedding_generation",
    ):
        assert key in metrics
    assert metrics["citation_validity"] == 1.0
    assert metrics["retrieval_latency"]["p50_ms"] is not None
    assert report["comparison"]["bge_m3_status"] == "NOT_RUN"
