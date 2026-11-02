"""Unit tests for the BM25 ranker and the hybrid rank fusion wiring."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.bm25 import BM25Index  # noqa: E402
from app.knowledge import tokenize  # noqa: E402


def test_bm25_ranks_the_rare_term_first():
    index = BM25Index(
        {
            "a": "# 广告预算\n\n广告预算调整需要审批，广告预算每月复核。",
            "b": "# 退款排查\n\n退款率异常先按 SKU 拆分，再看详情页。",
            "c": "# 履约成本\n\n履约成本按承运商拆分，含燃油附加费。",
        },
        tokenize,
    )
    ranked = index.ranked_ids("燃油附加费 怎么核对")
    assert ranked[0] == "c"


def test_bm25_returns_nothing_for_unrelated_query():
    index = BM25Index({"a": "# 广告预算\n\n广告预算调整需要审批。"}, tokenize)
    # No shared token at all: the caller must see an empty list, not a ranked
    # list of irrelevant chunks with score 0.
    assert index.ranked_ids("今天天气怎么样 ZZZZ") == []


def test_bm25_length_normalisation_does_not_favour_long_chunks():
    long_text = "# 广告预算\n\n" + "广告预算调整需要审批。" * 40
    index = BM25Index(
        {"long": long_text, "short": "# 广告预算\n\n广告预算调整需要审批。"},
        tokenize,
    )
    # Same term frequencies per document; b=0.75 keeps the two close instead of
    # letting raw length dominate.
    scores = index.scores("广告预算调整")
    assert set(scores) == {"long", "short"}
    assert max(scores.values()) / min(scores.values()) < 2.0


def test_search_reports_rankers_and_confidence(client, analyst):
    payload = client.post(
        "/api/knowledge/search", headers=analyst,
        json={"query": "广告预算下调 15% 审批", "top_k": 3},
    ).json()
    assert payload["retrieval_mode"] == "hybrid_rrf"
    # hashing has no ranking ability, so only BM25 and confidence vote.
    assert payload["rankers"] == ["bm25", "confidence"]
    assert payload["no_answer"] is False
    assert payload["hits"][0]["title"] == "广告预算调整与审批规则"
    assert all(hit["confidence"] is not None for hit in payload["hits"])
    scores = [hit["score"] for hit in payload["hits"]]
    assert scores == sorted(scores, reverse=True)


def test_top_k_is_not_filled_by_a_single_document(client, analyst):
    payload = client.post(
        "/api/knowledge/search", headers=analyst,
        json={"query": "广告预算调整需要审批", "top_k": 5},
    ).json()
    titles = [hit["title"] for hit in payload["hits"]]
    assert titles[0] == "广告预算调整与审批规则"
    # The per-document cap keeps room for a second document instead of letting
    # one SOP occupy every slot.
    assert len(set(titles)) >= 2
