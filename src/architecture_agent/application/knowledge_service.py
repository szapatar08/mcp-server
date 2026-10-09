"""Knowledge service: ingest, deduplicate, retrieve and answer freshness."""

from __future__ import annotations

from dataclasses import dataclass

from architecture_agent.application.errors import InvalidInputError
from architecture_agent.application.guard import GuardedEvidence, UntrustedContentGuard
from architecture_agent.domain.research import (
    ConfidenceLevel,
    FreshnessPolicy,
    KnowledgeCategory,
    SourceDocument,
    utc_now,
)
from architecture_agent.knowledge.documents import Chunk, KnowledgeDocument, chunk_document
from architecture_agent.knowledge.embeddings import Embedder
from architecture_agent.knowledge.storage import VectorStore


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """What one ingestion run did."""

    ingested: int = 0
    skipped_unchanged: int = 0
    rejected: int = 0
    chunks_written: int = 0
    reasons: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        """Documents considered."""
        return self.ingested + self.skipped_unchanged + self.rejected


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Retrieved chunks plus the provenance a caller needs to cite them."""

    query: str
    chunks: tuple[Chunk, ...]
    degraded: bool = False
    degradation_reason: str | None = None

    @property
    def is_empty(self) -> bool:
        """Whether anything was found."""
        return not self.chunks

    @property
    def best_confidence(self) -> ConfidenceLevel:
        """Highest confidence across the retrieved chunks."""
        from architecture_agent.domain.research import ConfidenceLevel as Level

        order = list(Level)
        rank = {level: index for index, level in enumerate(order)}
        return min(
            (chunk.confidence for chunk in self.chunks),
            key=rank.__getitem__,
            default=Level.SPECULATIVE,
        )

    def citations(self) -> tuple[str, ...]:
        """Citations a user can check."""
        return tuple(chunk.citation() for chunk in self.chunks)

    def render(self, *, nonce: str) -> str:
        """Render retrieved chunks as guarded evidence."""
        if not self.chunks:
            return ""
        blocks = tuple(
            UntrustedContentGuard().inspect(
                chunk.text,
                url=chunk.url,
                tier=chunk.tier,
                confidence=chunk.confidence,
                title=f"{chunk.topic}#{chunk.ordinal}",
            )
            for chunk in self.chunks
        )
        return GuardedEvidence(blocks=blocks, nonce=nonce).render()


class KnowledgeService:
    """Ingest retrieved documents and retrieve them.

    Ingestion is idempotent on content hash: re-running a refresh over an unchanged
    page must not rewrite the store, which is what makes a scheduled update cheap
    and what stops the corpus growing duplicates on every run.
    """

    def __init__(
        self,
        store: VectorStore,
        embedder: Embedder,
        *,
        chunk_tokens: int = 512,
        overlap_tokens: int = 64,
    ) -> None:
        """Build the service.

        Args:
            store: Chunk and vector storage.
            embedder: Used for documents and for queries.
            chunk_tokens: Chunk budget.
            overlap_tokens: Chunk overlap, must be smaller than the budget.
        """
        self._store = store
        self._embedder = embedder
        self._chunk_tokens = chunk_tokens
        self._overlap_tokens = overlap_tokens

    def ingest(self, documents: tuple[KnowledgeDocument, ...]) -> IngestionReport:
        """Ingest documents, skipping any whose content has not changed."""
        ingested = 0
        skipped = 0
        rejected = 0
        chunks_written = 0
        reasons: list[str] = []

        for document in documents:
            digest = document.content_hash
            if self._store.unchanged(document.url, digest):
                skipped += 1
                continue
            if not document.text.strip():
                rejected += 1
                reasons.append(f"{document.url}: empty document body")
                continue

            chunks = chunk_document(
                document,
                chunk_size_tokens=self._chunk_tokens,
                overlap_tokens=self._overlap_tokens,
            )
            if not chunks:
                rejected += 1
                reasons.append(f"{document.url}: produced no chunks")
                continue

            embeddings = self._embedder.embed([chunk.text for chunk in chunks])
            self._store.upsert(chunks, embeddings)
            self._store.record(document.url, digest, len(chunks))
            ingested += 1
            chunks_written += len(chunks)

        return IngestionReport(
            ingested=ingested,
            skipped_unchanged=skipped,
            rejected=rejected,
            chunks_written=chunks_written,
            reasons=tuple(reasons),
        )

    def ingest_sources(
        self, sources: tuple[SourceDocument, ...], bodies: dict[str, str]
    ) -> IngestionReport:
        """Ingest ``SourceDocument`` values using ``bodies`` keyed by URL."""
        documents = tuple(
            KnowledgeDocument(
                url=str(source.url),
                title=source.title,
                text=bodies.get(str(source.url), source.excerpt or ""),
                tier=source.tier,
                confidence=source.confidence,
                category=source.category,
                topic=source.topic,
            )
            for source in sources
        )
        return self.ingest(documents)

    def retrieve(self, *, query: str, topic: str | None = None, limit: int = 5) -> RetrievalResult:
        """Return the chunks nearest ``query``.

        A retrieval failure is a degraded result rather than an exception, because
        an empty knowledge base and a broken one are different answers.
        """
        if not query.strip():
            msg = "retrieval requires a non-empty query"
            raise InvalidInputError(msg)

        try:
            embedding = self._embedder.embed([query])[0]
            chunks = self._store.search(embedding, topic=topic, limit=limit)
        except Exception as error:
            return RetrievalResult(
                query=query,
                chunks=(),
                degraded=True,
                degradation_reason=f"retrieval failed: {type(error).__name__}",
            )

        return RetrievalResult(query=query, chunks=chunks)

    def is_stale(self, url: str, digest: str, policy: FreshnessPolicy) -> bool:
        """Whether ``url`` needs re-ingesting under ``policy``."""
        if not self._store.unchanged(url, digest):
            return True
        return policy.is_stale(utc_now())

    def stats(self) -> dict[str, int]:
        """Store statistics for the health check."""
        return self._store.stats()


def dynamic_policy(days: int = 7) -> FreshnessPolicy:
    """Freshness policy for facts that change."""
    return FreshnessPolicy(category=KnowledgeCategory.DYNAMIC, max_age_days=days)


__all__ = [
    "IngestionReport",
    "InvalidInputError",
    "KnowledgeService",
    "RetrievalResult",
    "dynamic_policy",
]
