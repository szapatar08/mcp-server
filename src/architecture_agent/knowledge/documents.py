"""Knowledge documents and chunking.

A document is a retrieved source with provenance. A chunk is a slice of one with
enough context to be retrieved usefully and enough identity to be deduplicated.

Chunking is token-aware but deliberately simple: overlap exists so a fact split
across a boundary is retrievable from either side. No semantic splitting, because
it cannot be justified without an evaluation set, and guessing at boundaries
makes retrieval worse rather than better.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Final

from architecture_agent.domain.research import (
    ConfidenceLevel,
    KnowledgeCategory,
    SourceTier,
    content_hash,
    utc_now,
)

_PARAGRAPH: Final = re.compile(r"\n\s*\n")
_CHARS_PER_TOKEN: Final = 4


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    """A retrieved document that will be ingested."""

    url: str
    title: str
    text: str
    tier: SourceTier
    confidence: ConfidenceLevel
    category: KnowledgeCategory
    topic: str

    @property
    def content_hash(self) -> str:
        """Digest used to skip re-ingesting unchanged content."""
        return content_hash(self.text)


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable slice of a document."""

    id: str
    document_id: str
    text: str
    ordinal: int
    topic: str
    url: str
    tier: SourceTier
    confidence: ConfidenceLevel
    category: KnowledgeCategory
    metadata: dict[str, str] = field(default_factory=dict)

    def citation(self) -> str:
        """Render a citation a user can check."""
        return f"{self.url}#{self.ordinal} ({self.tier.name}, {self.confidence.value})"


def document_id(url: str, digest: str) -> str:
    """Stable identity for a document version.

    The URL is folded into the digest so two documents with identical content at
    different URLs stay distinct records: provenance is part of the identity.
    """
    combined = hashlib.sha256(f"{url}\n{digest}".encode()).hexdigest()
    return combined[:16]


def chunk_document(
    document: KnowledgeDocument,
    *,
    chunk_size_tokens: int = 512,
    overlap_tokens: int = 64,
) -> tuple[Chunk, ...]:
    """Split a document into overlapping chunks on paragraph boundaries.

    A paragraph longer than the budget is split on sentence boundaries; a
    paragraph with no sentence boundary is split on character count. Chunk size is
    estimated from character count, which is imprecise but never silently wrong:
    the estimate is a documented approximation, not a tokeniser contract.
    """
    if chunk_size_tokens <= 0:
        msg = "chunk_size_tokens must be positive"
        raise ValueError(msg)
    if overlap_tokens >= chunk_size_tokens:
        msg = "overlap_tokens must be smaller than chunk_size_tokens"
        raise ValueError(msg)

    budget = chunk_size_tokens * _CHARS_PER_TOKEN
    overlap = overlap_tokens * _CHARS_PER_TOKEN
    digest = document.content_hash
    doc_ref = document_id(document.url, digest)

    chunks: list[Chunk] = []
    ordinal = 0
    position = 0
    body = document.text

    while position < len(body):
        window = body[position : position + budget]
        if not window.strip():
            break

        # Whether this window reaches the end of the body decides the advance.
        # Testing the cut length instead would be wrong twice over: a cut equal to
        # the window length also happens when the budget, not the body, was the
        # limit, and would discard everything after the first window.
        reached_end = position + len(window) >= len(body)
        cut = _split_point(window, budget)
        piece = window[:cut].strip()

        if piece:
            ordinal += 1
            chunks.append(
                Chunk(
                    id=f"{doc_ref}:{ordinal}",
                    document_id=doc_ref,
                    text=piece,
                    ordinal=ordinal,
                    topic=document.topic,
                    url=document.url,
                    tier=document.tier,
                    confidence=document.confidence,
                    category=document.category,
                    metadata={"ingested_at": utc_now().isoformat()},
                )
            )

        if reached_end:
            break
        position += max(cut - overlap, 1)

    return tuple(chunks)


def _split_point(window: str, budget: int) -> int:
    """Choose where to cut a window, preferring a paragraph then a sentence."""
    if len(window) <= budget:
        return len(window)

    for separator in ("\n\n", ". ", "\n"):
        index = window.rfind(separator, 0, budget)
        if index > budget // 4:
            return index + len(separator)
    return budget
