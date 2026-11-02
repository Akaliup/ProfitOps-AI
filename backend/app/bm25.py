"""In-process BM25 index for the operations knowledge base.

Why not FTS5 / pgvector: the corpus is small (tens to a few thousand chunks), the
API must behave identically on SQLite and PostgreSQL, and CI has no extra service.
Building the inverted index per query is the same order of work the retriever
already does (it scans and tokenizes every candidate chunk anyway), so this adds a
second *ranking signal* without adding infrastructure.

Tokenization is injected instead of imported so the token rules stay in
``app.knowledge`` and the embedding, the lexical score and BM25 keep using the
same tokens.

BM25 reference: Robertson & Zaragoza, "The Probabilistic Relevance Framework:
BM25 and Beyond" (2009), with the usual k1=1.5, b=0.75 defaults.
"""

from __future__ import annotations

from collections import Counter
from math import log
from typing import Callable, Iterable, Mapping

Tokenizer = Callable[[str], list[str]]


class BM25Index:
    """Okapi BM25 over an in-memory corpus of ``(chunk_id, text)`` pairs."""

    def __init__(
        self,
        documents: Mapping[str, str] | Iterable[tuple[str, str]],
        tokenize: Tokenizer,
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        items = list(documents.items()) if isinstance(documents, Mapping) else list(documents)
        self.tokenize = tokenize
        self.k1 = k1
        self.b = b
        self.ids: list[str] = []
        self.term_frequencies: list[Counter[str]] = []
        self.lengths: list[int] = []
        document_frequency: Counter[str] = Counter()
        for chunk_id, text in items:
            tokens = tokenize(text)
            frequencies = Counter(tokens)
            self.ids.append(chunk_id)
            self.term_frequencies.append(frequencies)
            self.lengths.append(len(tokens))
            document_frequency.update(frequencies.keys())
        self.size = len(self.ids)
        self.average_length = (sum(self.lengths) / self.size) if self.size else 0.0
        # Lucene-style idf: always positive, no negative scores for common terms.
        self.idf = {
            term: log(1.0 + (self.size - count + 0.5) / (count + 0.5))
            for term, count in document_frequency.items()
        }

    def scores(self, query: str) -> dict[str, float]:
        """Return ``chunk_id -> BM25 score`` for chunks sharing at least one term."""
        terms = [term for term in self.tokenize(query) if term in self.idf]
        if not terms or not self.size:
            return {}
        # Repeated query terms should not multiply the score; BM25 sums over the
        # distinct terms the query actually contains.
        scores: dict[str, float] = {}
        for position, chunk_id in enumerate(self.ids):
            frequencies = self.term_frequencies[position]
            length = self.lengths[position] or 1
            normalizer = self.k1 * (1.0 - self.b + self.b * length / (self.average_length or 1.0))
            total = 0.0
            for term in terms:
                frequency = frequencies.get(term)
                if not frequency:
                    continue
                total += self.idf[term] * frequency * (self.k1 + 1.0) / (frequency + normalizer)
            if total > 0.0:
                scores[chunk_id] = total
        return scores

    def ranked_ids(self, query: str, limit: int | None = None) -> list[str]:
        """Return chunk ids ordered by descending BM25 score."""
        scored = self.scores(query)
        ordered = sorted(scored.items(), key=lambda item: item[1], reverse=True)
        ids = [chunk_id for chunk_id, _ in ordered]
        return ids[:limit] if limit is not None else ids
