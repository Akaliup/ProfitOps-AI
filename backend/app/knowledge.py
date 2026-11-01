"""Offline-first knowledge base for the profit analysis agent.

The default embedding provider is a deterministic feature-hashing baseline so
tests and the demo never need a network call. An OpenAI-compatible embedding
endpoint (for example Ollama or a private embedding service) can be enabled
with ``KNOWLEDGE_EMBEDDING_PROVIDER=openai``.

Retrieval is deliberately small and inspectable: cosine similarity over stored
vectors plus a lexical overlap score and a heading/title boost. It is a hybrid
retriever, not a cross-encoder reranker. Every returned chunk carries a stable
``chunk_id`` so the LLM can cite it and the workflow can verify the citation.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from datetime import date
from time import perf_counter
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import record
from .config import Settings
from .errors import DomainError
from .models import KnowledgeChunk, KnowledgeDocument, SeedVersion, new_id, utcnow
from .schemas import (
    KNOWLEDGE_MAX_HITS, KnowledgeDocumentInput, KnowledgeDocumentOutput, KnowledgeHit,
    KnowledgeSearchInput, KnowledgeSearchOutput,
)
from .security import Actor

KNOWLEDGE_SEED_VERSION = "knowledge-v1"
KNOWLEDGE_SOURCE = "synthetic://profitops/knowledge"
# Must match KnowledgeChunk.heading (String(200)); PostgreSQL enforces it.
KNOWLEDGE_HEADING_MAX_CHARS = 200

_ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9._-]*")
_CJK = re.compile(r"[㐀-䶿一-鿿]")
_WHITESPACE = re.compile(r"\s+")
_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Best-effort redaction for common credential shapes. This is defence in depth,
# not a substitute for document ACLs or a full DLP scanner.
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*['\"]?[^\s'\"]{6,}"),
)


def normalize_text(text: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def redact_knowledge_secrets(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def content_sha256(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def tokenize(text: str) -> list[str]:
    """Tokenize English words and Chinese unigrams/bigrams/trigrams.

    The Chinese n-grams make the offline hashing embedding useful for short
    Chinese queries without a neural model. English is lowercased and split on
    punctuation.
    """
    normalized = normalize_text(text).lower()
    tokens = _ASCII_WORD.findall(normalized)
    cjk = _CJK.findall(normalized)
    tokens.extend(cjk)
    tokens.extend("".join(pair) for pair in zip(cjk, cjk[1:]))
    tokens.extend("".join(triple) for triple in zip(cjk, cjk[1:], cjk[2:]))
    return tokens


def sanitize_knowledge_text(text: str, limit: int = 800) -> str:
    """Treat knowledge content as data: strip control chars and bound length.

    The caller must still keep the content in a clearly delimited data field and
    instruct the model to ignore instructions inside it.
    """
    cleaned = _CONTROL.sub(" ", unicodedata.normalize("NFKC", text))
    cleaned = redact_knowledge_secrets(cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned[:limit]


class EmbeddingProvider(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbeddingProvider:
    """Deterministic, dependency-free lexical baseline.

    This is a feature-hashing bag of tokens, not a semantic neural embedding.
    It is the default so the demo and CI are fully offline; configure an
    OpenAI-compatible provider for semantic retrieval.
    """

    def __init__(self, dimensions: int = 256, model: str = "hashing-v1"):
        self.dimensions = dimensions
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        counts = Counter(tokenize(text))
        for token, count in counts.items():
            encoded = token.encode("utf-8")
            index = int.from_bytes(
                hashlib.blake2b(encoded, digest_size=4, key=b"profitops-index").digest(), "big",
            ) % self.dimensions
            sign = 1.0 if hashlib.blake2b(
                encoded, digest_size=1, key=b"profitops-sign",
            ).digest()[0] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


class OpenAIEmbeddingProvider:
    """OpenAI-compatible ``POST /embeddings`` provider.

    Point ``KNOWLEDGE_EMBEDDING_BASE_URL`` at a local Ollama/vLLM gateway or a
    private embedding service. The API key is optional for local gateways.
    """

    def __init__(
        self, *, base_url: str, api_key: str, model: str,
        timeout: float = 20.0, transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport
        # Learned from the first successful response; lets reindex detect a
        # same-model dimension change without probing on every call.
        self.dimensions: int | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            # A loopback embedding gateway must not be routed through a
            # machine-wide HTTP proxy: on Windows that proxy often answers
            # localhost with 502 before the request reaches Ollama.
            host = (urlparse(self.base_url).hostname or "").lower()
            trust_env = host not in {"127.0.0.1", "localhost", "::1"}
            with httpx.Client(
                timeout=self.timeout, transport=self.transport, trust_env=trust_env,
            ) as client:
                response = client.post(
                    f"{self.base_url}/embeddings",
                    headers=headers,
                    json={"model": self.model, "input": texts},
                )
        except httpx.HTTPError as exc:
            raise DomainError(
                "knowledge_embedding_failed",
                f"Embedding 服务不可用：{type(exc).__name__}",
                502,
            ) from exc
        if response.status_code >= 400:
            raise DomainError(
                "knowledge_embedding_failed",
                f"Embedding 服务返回 HTTP {response.status_code}",
                502,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise DomainError(
                "knowledge_embedding_failed", "Embedding 响应不是合法 JSON", 502,
            ) from exc
        vectors = [self._normalize(vector) for vector in self._parse_vectors(payload, len(texts))]
        if vectors:
            self.dimensions = len(vectors[0])
        return vectors

    @staticmethod
    def _parse_vectors(payload: Any, expected: int) -> list[list[float]]:
        """Validate the response before any vector can reach the database."""
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise DomainError("knowledge_embedding_failed", "Embedding 响应格式无效", 502)
        data = payload["data"]
        if len(data) != expected:
            raise DomainError("knowledge_embedding_failed", "Embedding 数量无效", 502)
        ordered: list[tuple[int, list[float]]] = []
        for position, item in enumerate(data):
            if not isinstance(item, dict):
                raise DomainError("knowledge_embedding_failed", "Embedding 响应格式无效", 502)
            index = item.get("index", position)
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < expected:
                raise DomainError("knowledge_embedding_failed", "Embedding index 无效", 502)
            embedding = item.get("embedding")
            if not isinstance(embedding, list) or not embedding:
                raise DomainError("knowledge_embedding_failed", "Embedding 向量为空或格式无效", 502)
            values: list[float] = []
            for value in embedding:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise DomainError("knowledge_embedding_failed", "Embedding 包含非数字值", 502)
                try:
                    number = float(value)
                except (OverflowError, ValueError) as exc:
                    raise DomainError("knowledge_embedding_failed", "Embedding 数值超出范围", 502) from exc
                if not math.isfinite(number):
                    raise DomainError("knowledge_embedding_failed", "Embedding 包含 NaN 或 Infinity", 502)
                values.append(number)
            ordered.append((index, values))
        if len({index for index, _ in ordered}) != expected:
            raise DomainError("knowledge_embedding_failed", "Embedding index 重复或缺失", 502)
        if len({len(vector) for _, vector in ordered}) != 1:
            raise DomainError("knowledge_embedding_failed", "Embedding 维度不一致", 502)
        ordered.sort(key=lambda pair: pair[0])
        return [vector for _, vector in ordered]

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        # hypot avoids overflow when squaring very large finite components.
        norm = math.hypot(*vector)
        return [value / norm for value in vector] if norm else [0.0 for _ in vector]


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.knowledge_embedding_provider == "openai":
        return OpenAIEmbeddingProvider(
            base_url=settings.knowledge_embedding_base_url,
            api_key=settings.knowledge_embedding_api_key.get_secret_value().strip(),
            model=settings.knowledge_embedding_model,
            timeout=settings.llm_timeout,
        )
    return HashingEmbeddingProvider(
        dimensions=settings.knowledge_embedding_dimensions,
        model=settings.knowledge_embedding_model,
    )


def _bounded_heading(heading: str | None) -> str | None:
    if not heading:
        return None
    return heading[:KNOWLEDGE_HEADING_MAX_CHARS]


def _embedding_dimensions(embeddings: EmbeddingProvider, sample: str) -> int:
    """Return the provider's current vector size without re-embedding every chunk.

    Hashing and test providers expose ``dimensions``. For a remote provider the
    first probe learns it; later calls use the cached value.
    """
    dimensions = getattr(embeddings, "dimensions", None)
    if isinstance(dimensions, int) and dimensions > 0:
        return dimensions
    probe = embeddings.embed([sample])
    if not probe or not probe[0]:
        raise DomainError("knowledge_embedding_failed", "Embedding 维度无效", 502)
    return len(probe[0])


def chunk_text(content: str, max_chars: int = 600, overlap: int = 100) -> list[dict[str, str | None]]:
    """Split Markdown-ish text into overlapping chunks with heading metadata."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must be between 0 and max_chars - 1")
    text = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    sections: list[dict[str, Any]] = []
    heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        body = "\n".join(buffer).strip()
        if body:
            sections.append({"heading": heading, "text": body})
        buffer = []

    for line in text.split("\n"):
        match = _HEADING.match(line.strip())
        if match:
            flush()
            heading = _bounded_heading(match.group(2).strip())
        else:
            buffer.append(line)
    flush()
    if not sections:
        sections = [{"heading": None, "text": text}]

    chunks: list[dict[str, str | None]] = []
    for section in sections:
        chunks.extend(_split_section(section["heading"], section["text"], max_chars, overlap))
    return [chunk for chunk in chunks if chunk["content"]]


def _split_section(heading: str | None, text: str, max_chars: int, overlap: int) -> list[dict[str, str | None]]:
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            units.append(paragraph)
        else:
            units.extend(_split_long(paragraph, max_chars))

    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}".strip() if current else unit
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            carry = current[-overlap:].strip() if overlap else ""
            current = f"{carry}\n\n{unit}".strip() if carry else unit
        else:
            current = unit
        while len(current) > max_chars:
            chunks.append(current[:max_chars].strip())
            carry = current[max_chars - overlap:].strip() if overlap else ""
            current = carry or current[max_chars:]
    if current.strip():
        chunks.append(current.strip())
    return [{"heading": heading, "content": chunk} for chunk in chunks if chunk.strip()]


def _split_long(text: str, max_chars: int) -> list[str]:
    # Keep the whitespace after punctuation so joining the units reproduces the
    # original paragraph; consuming it fused English sentences together.
    sentences = re.split(r"(?<=[。！？!?；;\.])", text)
    units: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if len(sentence) > max_chars:
            # Flush buffered short sentences before the long sentence's pieces.
            # Appending them afterwards would reorder the original text.
            if current:
                units.append(current)
                current = ""
            for index in range(0, len(sentence), max_chars):
                units.append(sentence[index:index + max_chars])
        elif len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            if current:
                units.append(current)
            current = sentence
    if current:
        units.append(current)
    return units


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _token_weight(token: str) -> float:
    # Single Chinese characters are weak evidence on their own ("预" matches
    # both 预算 and 预订); bigrams/trigrams and ASCII words carry the signal.
    if len(token) == 1 and _CJK.fullmatch(token):
        return 0.2
    return float(max(1, len(token)))


def _lexical_score(query: str, text: str) -> float:
    query_tokens = set(tokenize(query))
    text_tokens = set(tokenize(text))
    if not query_tokens:
        return 0.0
    total = sum(_token_weight(token) for token in query_tokens)
    matched = sum(_token_weight(token) for token in query_tokens if token in text_tokens)
    return matched / total if total else 0.0


def _heading_match(query: str, heading: str | None) -> float:
    if not heading:
        return 0.0
    query_tokens = set(tokenize(query))
    heading_tokens = set(tokenize(heading))
    return 1.0 if query_tokens & heading_tokens else 0.0


def build_knowledge_query(report, findings) -> str:
    """Build a compact retrieval query from deterministic findings."""
    labels = [driver.label for driver in report.drivers if driver.contribution < 0][:3]
    skus = [
        row["sku"]
        for row in sorted(report.sku_breakdown, key=lambda row: row["ad_spend_delta"], reverse=True)[:2]
    ]
    parts = [
        findings.conclusion,
        findings.primary_driver.label if findings.primary_driver else "",
        *labels,
        *skus,
        "利润下降",
        "排查",
        "审批",
        "模拟执行",
    ]
    return " ".join(dict.fromkeys(part.strip() for part in parts if part and part.strip()))[:300]


def knowledge_document_output(
    row: KnowledgeDocument, chunk_count: int, created: bool = False,
    reindexed: bool = False,
) -> KnowledgeDocumentOutput:
    return KnowledgeDocumentOutput(
        id=row.id, title=row.title, source=row.source, version=row.version,
        effective_date=row.effective_date, active=row.active,
        chunk_count=chunk_count, created=created, reindexed=reindexed,
        created_at=row.created_at,
    )


def list_documents(session: Session, tenant_id: str = "demo") -> list[KnowledgeDocumentOutput]:
    counts = dict(session.execute(
        select(KnowledgeChunk.document_id, func.count()).group_by(KnowledgeChunk.document_id)
    ).all())
    rows = session.scalars(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.active.is_(True), KnowledgeDocument.tenant_id == tenant_id)
        .order_by(KnowledgeDocument.created_at.desc())
    ).all()
    return [knowledge_document_output(row, int(counts.get(row.id, 0))) for row in rows]


def _insert_document(
    session: Session,
    *,
    title: str,
    source: str,
    version: str,
    effective_date: date | None,
    content: str,
    metadata: dict[str, Any],
    embeddings: EmbeddingProvider,
    chunk_chars: int,
    overlap: int,
    tenant_id: str = "demo",
) -> tuple[KnowledgeDocument, int]:
    chunks = chunk_text(content, chunk_chars, overlap)
    if not chunks:
        raise DomainError("knowledge_empty_document", "知识文档没有可索引的正文", 422)
    vectors = embeddings.embed([str(chunk["content"]) for chunk in chunks])
    document = KnowledgeDocument(
        id=new_id(), tenant_id=tenant_id, title=title, source=source, version=version,
        effective_date=effective_date, content=normalize_text(content),
        content_sha256=content_sha256(content), metadata_json=metadata,
    )
    session.add(document)
    session.flush()
    for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
        text = str(chunk["content"])
        session.add(KnowledgeChunk(
            id=new_id(), document_id=document.id, chunk_index=index,
            heading=chunk["heading"], content=text, char_count=len(text),
            embedding=vector, embedding_model=embeddings.model,
        ))
    return document, len(chunks)


def create_document(
    session: Session,
    payload: KnowledgeDocumentInput,
    embeddings: EmbeddingProvider,
    *,
    chunk_chars: int = 600,
    overlap: int = 100,
    actor: Actor | None = None,
    request_id: str = "",
    tenant_id: str = "demo",
) -> KnowledgeDocumentOutput:
    sha = content_sha256(payload.content)
    existing = session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == tenant_id,
            KnowledgeDocument.content_sha256 == sha,
            KnowledgeDocument.active.is_(True),
        )
    )
    if existing is not None:
        # Content dedupe must not skip a model switch. Re-embed the existing
        # chunks in place so the document ID, chunk IDs and content survive.
        return reindex_document(
            session, existing.id, embeddings, actor=actor, request_id=request_id,
            tenant_id=tenant_id,
        )

    try:
        # A savepoint keeps the outer transaction usable if the partial unique
        # index (one active document per SHA-256) rejects a concurrent insert.
        with session.begin_nested():
            document, count = _insert_document(
                session,
                title=payload.title, source=payload.source, version=payload.version,
                effective_date=payload.effective_date, content=payload.content,
                metadata=payload.metadata, embeddings=embeddings,
                chunk_chars=chunk_chars, overlap=overlap, tenant_id=tenant_id,
            )
    except IntegrityError:
        # Another request won the race between our SELECT and INSERT. Reuse its
        # document instead of creating a duplicate active row.
        existing = session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == tenant_id,
                KnowledgeDocument.content_sha256 == sha,
                KnowledgeDocument.active.is_(True),
            )
        )
        if existing is None:
            raise
        return reindex_document(
            session, existing.id, embeddings, actor=actor, request_id=request_id,
            tenant_id=tenant_id,
        )
    if actor is not None:
        record(
            session, actor, "knowledge.document.created", "knowledge", document.id,
            request_id, details={
                "title": document.title, "source": document.source,
                "version": document.version, "chunks": count,
            },
        )
    return knowledge_document_output(document, count, created=True)


def reindex_document(
    session: Session,
    document_id: str,
    embeddings: EmbeddingProvider,
    *,
    actor: Actor | None = None,
    request_id: str = "",
    tenant_id: str = "demo",
) -> KnowledgeDocumentOutput:
    """Re-embed an existing document's chunks with the current provider model.

    Chunk boundaries, IDs and content are preserved; only vectors and the model
    name change. The caller owns the transaction: if embedding or persistence
    fails, the previous valid chunks remain in the database. Calling this again
    with the same model is a no-op, so it is safe to retry.
    """
    document = session.scalar(select(KnowledgeDocument).where(
        KnowledgeDocument.id == document_id,
        KnowledgeDocument.tenant_id == tenant_id,
    ))
    if document is None or not document.active:
        raise DomainError("knowledge_document_not_found", "知识文档不存在", 404)
    chunks = list(session.scalars(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.document_id == document.id)
        .order_by(KnowledgeChunk.chunk_index)
    ).all())
    if not chunks:
        raise DomainError(
            "knowledge_document_empty", "知识文档没有可重新索引的片段", 422,
        )
    stale = [
        chunk for chunk in chunks
        if chunk.embedding_model != embeddings.model or not chunk.embedding
    ]
    if not stale:
        # The model name can stay the same while its output dimension changes
        # (provider upgrade, reconfiguration). Stored vectors of another size
        # cannot be compared with new query vectors, so treat them as stale too.
        dimensions = _embedding_dimensions(embeddings, chunks[0].content)
        stale = [
            chunk for chunk in chunks
            if not isinstance(chunk.embedding, list) or len(chunk.embedding) != dimensions
        ]
    if not stale:
        return knowledge_document_output(
            document, len(chunks), created=False, reindexed=False,
        )
    # Embed before mutating any row. A provider failure therefore leaves the
    # previous vectors and model names untouched even before the rollback.
    vectors = embeddings.embed([chunk.content for chunk in chunks])
    if len(vectors) != len(chunks):
        raise DomainError(
            "knowledge_embedding_failed", "Embedding 数量与知识片段不一致", 502,
        )
    if any(not isinstance(vector, list) or not vector for vector in vectors):
        raise DomainError("knowledge_embedding_failed", "Embedding 向量为空", 502)
    if len({len(vector) for vector in vectors}) != 1:
        raise DomainError(
            "knowledge_embedding_failed", "Embedding 维度不一致", 502,
        )
    previous_models = sorted({chunk.embedding_model for chunk in chunks})
    for chunk, vector in zip(chunks, vectors):
        chunk.embedding = vector
        chunk.embedding_model = embeddings.model
    document.updated_at = utcnow()
    session.flush()
    if actor is not None:
        record(
            session, actor, "knowledge.document.reindexed", "knowledge", document.id,
            request_id, details={
                "title": document.title, "chunks": len(chunks),
                "from_models": previous_models, "to_model": embeddings.model,
            },
        )
    return knowledge_document_output(
        document, len(chunks), created=False, reindexed=True,
    )


def delete_document(
    session: Session, document_id: str, actor: Actor, request_id: str,
    tenant_id: str = "demo",
) -> KnowledgeDocumentOutput:
    document = session.scalar(select(KnowledgeDocument).where(
        KnowledgeDocument.id == document_id,
        KnowledgeDocument.tenant_id == tenant_id,
    ))
    if document is None or not document.active:
        raise DomainError("knowledge_document_not_found", "知识文档不存在", 404)
    count = int(session.scalar(
        select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
    ) or 0)
    document.active = False
    record(
        session, actor, "knowledge.document.deleted", "knowledge", document.id,
        request_id, details={"title": document.title, "chunks": count},
    )
    return knowledge_document_output(document, count)


def search_knowledge(
    session: Session,
    payload: KnowledgeSearchInput,
    embeddings: EmbeddingProvider,
    *,
    candidate_k: int = 20,
    min_score: float = 0.15,
    tenant_id: str = "demo",
) -> KnowledgeSearchOutput:
    started = perf_counter()
    query = normalize_text(payload.query)
    if not query:
        raise DomainError("knowledge_invalid_query", "知识库检索问题不能为空", 422)
    query_vector = embeddings.embed([query])[0]
    rows = session.execute(
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .where(
            KnowledgeDocument.tenant_id == tenant_id,
            KnowledgeDocument.active.is_(True),
            KnowledgeChunk.embedding_model == embeddings.model,
        )
    ).all()
    scored: list[tuple[float, KnowledgeChunk, KnowledgeDocument]] = []
    for chunk, document in rows:
        embedding = list(chunk.embedding or [])
        # A same-model dimension change leaves old vectors in another space.
        # Do not let them contribute lexical-only hits before reindex.
        if len(embedding) != len(query_vector):
            continue
        cosine = _cosine(query_vector, embedding)
        lexical = _lexical_score(query, f"{chunk.heading or ''} {chunk.content}")
        heading_boost = _heading_match(query, chunk.heading)
        if lexical <= 0 and cosine <= 0.05:
            continue
        score = 0.65 * max(0.0, cosine) + 0.35 * lexical + 0.05 * heading_boost
        scored.append((score, chunk, document))

    scored.sort(key=lambda item: item[0], reverse=True)
    candidates = scored[:candidate_k]
    reranked = [
        (score + 0.03 * _heading_match(query, document.title), chunk, document)
        for score, chunk, document in candidates
    ]
    reranked.sort(key=lambda item: item[0], reverse=True)

    hits: list[KnowledgeHit] = []
    for rank, (score, chunk, document) in enumerate(reranked[:payload.top_k], start=1):
        hits.append(KnowledgeHit(
            chunk_id=chunk.id, document_id=document.id, title=document.title,
            source=document.source, version=document.version,
            heading=chunk.heading, content=chunk.content,
            score=round(score, 4), rank=rank,
        ))
    no_answer = not hits or hits[0].score < min_score
    if no_answer:
        # A low-confidence near miss must not become a citable source. The
        # caller sees no_answer=true and an empty hit list instead.
        hits = []
    return KnowledgeSearchOutput(
        query=query, hits=hits, no_answer=no_answer,
        embedding_model=embeddings.model,
        retrieval_ms=round((perf_counter() - started) * 1000, 3),
    )


def merge_knowledge_hits(
    knowledge: KnowledgeSearchOutput | None,
    extra_hits: list[dict[str, Any]] | None,
    preferred_ids: list[str] | None = None,
) -> KnowledgeSearchOutput | None:
    """Merge pre-retrieved and tool-retrieved chunks without exceeding the schema cap.

    The LLM may call ``search_knowledge`` more than once, so the union can exceed
    ``KNOWLEDGE_MAX_HITS`` even though each individual search is bounded. Keep the
    highest-scoring unique chunks and re-number them; returning an oversized
    context would make ``KnowledgeSearchOutput`` validation fail downstream.

    ``preferred_ids`` are chunks the model actually cited. They are kept ahead of
    the score cut so the persisted citation panel never drops a valid citation.
    """
    if knowledge is None and not extra_hits:
        return None
    base = knowledge or KnowledgeSearchOutput(
        query="", hits=[], no_answer=True,
        embedding_model="", retrieval_ms=0.0,
    )
    seen = {hit.chunk_id for hit in base.hits}
    merged = list(base.hits)
    for raw in extra_hits or []:
        try:
            hit = KnowledgeHit.model_validate(raw)
        except Exception:
            continue
        if hit.chunk_id in seen:
            continue
        seen.add(hit.chunk_id)
        merged.append(hit)
    merged.sort(key=lambda hit: hit.score, reverse=True)
    preferred = set(preferred_ids or [])
    if preferred:
        merged = (
            [hit for hit in merged if hit.chunk_id in preferred]
            + [hit for hit in merged if hit.chunk_id not in preferred]
        )
    merged = [
        hit.model_copy(update={"rank": rank})
        for rank, hit in enumerate(merged[:KNOWLEDGE_MAX_HITS], start=1)
    ]
    return KnowledgeSearchOutput(
        query=base.query,
        hits=merged,
        no_answer=not merged,
        embedding_model=base.embedding_model,
        retrieval_ms=base.retrieval_ms,
    )


SEED_DOCUMENTS: list[dict[str, Any]] = [
    {
        "title": "广告预算调整与审批规则",
        "source": f"{KNOWLEDGE_SOURCE}/ad-budget-policy",
        "version": "v1",
        "effective_date": date(2026, 10, 1),
        "metadata": {"synthetic": True, "domain": "advertising"},
        "content": """# 适用范围
本规则适用于北美旗舰店与欧洲直营店的站内广告预算调整。所有动作必须先经过人工审批，并在模拟环境执行；不得直接修改真实广告平台。

## 触发条件
- 连续 7 天广告支出环比上升超过 20%，且净利润环比下降。
- 广告支出是利润桥中绝对值最大的负向贡献项。
- 数据完整性检查通过，缺失的每日经营记录为 0。

## 预算口径
参考日预算 = 统计周期广告支出 / 统计天数，金额使用 USD 整数美分。单次模拟下调不超过参考日预算的 15%。不得对上新 30 天内的 SKU 下调预算。

## 审批流程
分析师发起利润分析并创建建议；审批负责人复核利润桥、数据覆盖、SKU 与预算前后值后批准。只有批准后的建议可以模拟执行。执行使用幂等键，重复请求返回同一执行记录。

## 复核指标
模拟执行后 7 天复核 ROAS、转化率、自然流量占比和退款率。若 ROAS 低于 2.0，暂停进一步下调；若退款率上升超过 2 个百分点，转为成本核查任务。

## 禁止事项
不得把会计贡献项当作因果证明；不得承诺收益；不得调用真实广告、采购或支付系统。""",
    },
    {
        "title": "退款率异常排查手册",
        "source": f"{KNOWLEDGE_SOURCE}/refund-playbook",
        "version": "v1",
        "effective_date": date(2026, 10, 1),
        "metadata": {"synthetic": True, "domain": "refunds"},
        "content": """# 适用范围
适用于订单退款率、退款金额或净销售额异常下降的排查。退款只扣减收入；采购成本已经是退货后的残值，不得对退款重复扣减成本。

## 排查顺序
1. 按 SKU、店铺、日期拆分退款率，确认异常是单 SKU 还是全店。
2. 检查商品详情页、尺码表、材质说明与近期改版记录。
3. 检查承运商时效、破损率和丢件率。
4. 检查促销活动、折扣叠加与预期管理。
5. 检查支付争议与平台判责记录。

## 证据要求
每条结论必须引用订单退款证据、每日经营记录或知识库片段。数据不完整时停止生成建议，不把缺失值当成零。

## 处置建议
先创建成本核查任务，核对退货原因与残值；必要时调整 listing 或物流方案。任何真实退款政策变更必须由人工审批，模拟环境不改变真实订单。

## 复核
7 天后比较退款率、退货原因分布和净销售额；若退款率仍高于基线 2 个百分点，升级为运营专项。""",
    },
    {
        "title": "履约成本异常排查",
        "source": f"{KNOWLEDGE_SOURCE}/fulfillment-cost",
        "version": "v1",
        "effective_date": date(2026, 10, 1),
        "metadata": {"synthetic": True, "domain": "fulfillment"},
        "content": """# 适用范围
适用于物流履约成本、承运商费用、仓储费和包装成本的异常排查。

## 触发条件
- 履约成本环比上升超过 15%，且订单量没有同比例增长。
- 履约成本是利润桥中的主要负向贡献项。
- 数据完整性检查通过。

## 排查步骤
1. 按承运商、仓库、目的国拆分单均履约成本。
2. 核对燃油附加费、偏远地区附加费、超重费和退件费。
3. 检查包装规格、装箱率和仓库操作费。
4. 对比不同承运商的时效与破损率。
5. 检查是否出现临时加价或合同价到期。

## 处置建议
先创建成本核查任务，向承运商索取账单明细；在模拟环境比较不同承运商方案。不得直接修改真实物流合同或支付。

## 复核
7 天后复核单均履约成本、时效和破损率；若成本下降但时效明显恶化，暂停方案并重新评估。""",
    },
    {
        "title": "平台手续费与结算核对",
        "source": f"{KNOWLEDGE_SOURCE}/platform-fees",
        "version": "v1",
        "effective_date": date(2026, 10, 1),
        "metadata": {"synthetic": True, "domain": "platform_fees"},
        "content": """# 适用范围
适用于平台佣金、支付手续费、促销服务费和结算差异的核对。

## 排查步骤
1. 按平台、店铺、类目核对佣金费率。
2. 区分平台佣金、支付手续费、促销服务费和广告费。
3. 检查结算周期、退款回冲和汇率折算。
4. 对比订单金额、平台结算单和银行入账。
5. 对差异超过 1% 的订单抽样复核。

## 证据要求
平台手续费必须来自订单字段或平台结算单；不得把广告费重复计入平台手续费。金额使用整数美分，差异按订单逐笔核对。

## 处置建议
创建结算核查任务，向平台索取费用明细；确认多扣后按平台流程申诉。模拟环境只生成核查记录，不发起真实申诉或支付。

## 复核
下个结算周期复核差异率；若差异仍超过 1%，升级为财务专项。""",
    },
    {
        "title": "数据完整性与缺失处理",
        "source": f"{KNOWLEDGE_SOURCE}/data-quality",
        "version": "v1",
        "effective_date": date(2026, 10, 1),
        "metadata": {"synthetic": True, "domain": "data_quality"},
        "content": """# 适用范围
适用于每日经营记录缺失、标记不完整、订单与广告数据不一致的情况。

## 完整性定义
每日经营记录必须覆盖全部店铺 × SKU × 日期。缺行或 complete=false 都视为不完整。空集不等于零销售，不得把缺失值当成经营零值。

## 处理流程
1. 运行数据质量检查，列出缺失日期、店铺和 SKU。
2. 补齐每日经营记录后重新分析。
3. 在补齐前，Agent 必须停在 insufficient_data，不创建可执行建议。
4. 在审计日志记录缺失范围与请求 ID。

## 证据要求
利润计算、环比和利润桥必须能回算到账本；证据不完整时不得生成 LLM 建议。数据修复后重新运行分析，不复用旧快照。

## 禁止事项
不得用估算值填充缺失记录；不得跳过完整性检查直接审批；不得把规则结果伪装成 LLM 输出。""",
    },
    {
        "title": "审批、执行与幂等规则",
        "source": f"{KNOWLEDGE_SOURCE}/approval-policy",
        "version": "v1",
        "effective_date": date(2026, 10, 1),
        "metadata": {"synthetic": True, "domain": "approval"},
        "content": """# 适用范围
适用于利润建议的审批、拒绝和模拟执行。

## 角色
经营分析师可以发起分析、查看指标和审计；审批负责人可以批准、拒绝和模拟执行。角色由服务端 Bearer token 决定，客户端传入的 X-Role 或 actor 无效。

## 状态机
proposed → pending_approval → approved → executing → executed；pending_approval → rejected；executing → execution_failed。审批结果不可反转。

## 执行约束
执行必须携带 Idempotency-Key，并在单个数据库事务中完成状态抢占、唯一执行记录、模拟结果和审计。重复请求返回同一 execution，replayed=true，不产生第二次模拟变更。不同建议不能复用执行键。

## 安全边界
所有执行均为 simulation，不调用真实广告、采购或支付系统。审批页面必须展示待执行动作的参数；LLM 不能调用审批或执行工具。

## 复核
执行失败保留错误码和诊断，HTTP 返回 500；不得伪装成成功。审计日志可按 request_id 精确追踪。""",
    },
]


def seed_knowledge(factory, embeddings: EmbeddingProvider, settings: Settings, tenant_id: str = "demo") -> dict:
    """Seed synthetic SOP documents once; never delete user documents."""
    try:
        with factory.begin() as session:
            if session.get(SeedVersion, KNOWLEDGE_SEED_VERSION):
                return {
                    "seeded": False, "version": KNOWLEDGE_SEED_VERSION,
                    "message": "知识库已存在，保留全部文档",
                }
            session.add(SeedVersion(version=KNOWLEDGE_SEED_VERSION))
            session.flush()
            created = 0
            for item in SEED_DOCUMENTS:
                sha = content_sha256(item["content"])
                existing = session.scalar(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.tenant_id == tenant_id,
                        KnowledgeDocument.content_sha256 == sha,
                        KnowledgeDocument.active.is_(True),
                    )
                )
                if existing is not None:
                    continue
                _insert_document(
                    session,
                    title=item["title"], source=item["source"], version=item["version"],
                    effective_date=item["effective_date"], content=item["content"],
                    metadata=item["metadata"], embeddings=embeddings,
                    chunk_chars=settings.knowledge_chunk_chars,
                    overlap=settings.knowledge_chunk_overlap,
                    tenant_id=tenant_id,
                )
                created += 1
            return {
                "seeded": True, "version": KNOWLEDGE_SEED_VERSION,
                "documents": created,
                "message": f"已初始化 {created} 篇演示知识文档",
            }
    except IntegrityError:
        with factory() as session:
            if session.get(SeedVersion, KNOWLEDGE_SEED_VERSION):
                return {
                    "seeded": False, "version": KNOWLEDGE_SEED_VERSION,
                    "message": "知识库已由另一请求初始化",
                }
        raise
