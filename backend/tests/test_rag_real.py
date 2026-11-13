from evals.rag import (
    CALIBRATION_FILE, TEST_FILE, load_dataset, validate_splits,
)
from evals.rag_real import load_queries, run
from app.knowledge import SEED_DOCUMENTS


def test_frozen_test_set_is_fixed_and_covers_the_corpus():
    dataset = load_queries()
    queries = dataset["queries"]
    titles = {document["title"] for document in SEED_DOCUMENTS}
    assert dataset["fixed_before_run"] is True
    assert dataset["split"] == "test"
    assert len(queries) >= 20
    assert len({query["id"] for query in queries}) == len(queries)
    assert {
        query["expected_title"] for query in queries if not query["expect_no_answer"]
    } == titles
    assert sum(1 for query in queries if query["expect_no_answer"]) >= 4
    assert sum(1 for query in queries if query.get("type") == "cross_doc") >= 4


def test_calibration_and_test_splits_are_disjoint_and_structurally_valid():
    calibration = load_dataset(CALIBRATION_FILE)
    test = load_dataset(TEST_FILE)
    result = validate_splits(calibration, test)
    assert result["passed"], result["checks"]
    assert result["checks"]["ids_disjoint"]
    assert result["checks"]["queries_disjoint"]
    assert result["checks"]["known_failures_in_test"]


def test_test_set_retains_observed_failure_cases():
    dataset = load_dataset(TEST_FILE)
    known = {item["id"] for item in dataset["known_failures"]}
    assert {"test-quality-03", "test-fulfillment-06"} <= known
    by_id = {query["id"]: query for query in dataset["queries"]}
    assert by_id["test-quality-03"]["query"] == "数据完整性检查要覆盖哪些维度和字段"
    assert by_id["test-fulfillment-06"]["query"] == "履约方案成本下降但时效恶化怎么办"


def test_hashing_baseline_runs_offline_and_reports_all_metrics():
    report = run(include_bge=False)
    hashing = report["hashing"]
    assert hashing["status"] == "COMPLETED"
    assert hashing["documents"] == 6
    assert hashing["queries"] == len(load_queries()["queries"])
    assert report["dataset"]["split"] == "test"
    metrics = hashing["metrics"]
    for key in (
        "hit_at_1", "hit_at_3", "mrr", "no_answer_accuracy",
        "citation_validity", "retrieval_latency", "embedding_generation",
    ):
        assert key in metrics
    assert metrics["citation_validity"] == 1.0
    assert metrics["retrieval_latency"]["p50_ms"] is not None
    assert report["comparison"]["bge_m3_status"] == "NOT_RUN"
