"""Vector storage port and a SQLite implementation.

The port exists because two implementations are already justified: the embedded
SQLite store used by default, and a hosted vector database for teams that need
one. SQL is used directly rather than through an ORM: the schema is four tables
and an extra dependency would be a liability, not an asset.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from architecture_agent.domain.research import (
    ConfidenceLevel,
    KnowledgeCategory,
    SourceTier,
)
from architecture_agent.knowledge.documents import Chunk
from architecture_agent.knowledge.embeddings import Embedder

DATABASE_FILENAME = "knowledge.sqlite3"
"""Name of the database file inside the configured knowledge directory."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    title       TEXT NOT NULL,
    topic       TEXT NOT NULL,
    tier        TEXT NOT NULL,
    confidence  TEXT NOT NULL,
    category    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    topic       TEXT NOT NULL,
    url         TEXT NOT NULL,
    tier        TEXT NOT NULL,
    confidence  TEXT NOT NULL,
    category    TEXT NOT NULL,
    metadata    TEXT NOT NULL,
    embedding   BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_by_topic ON chunks(topic);
CREATE TABLE IF NOT EXISTS ingestion_log (
    url          TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    chunks       INTEGER NOT NULL,
    skipped      INTEGER NOT NULL,
    recorded_at  TEXT NOT NULL
);
"""


class VectorStore(Protocol):
    """Stores and retrieves chunks."""

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> int:
        """Insert or replace ``chunks``; returns how many were new."""
        ...

    def search(
        self, embedding: Sequence[float], *, topic: str | None, limit: int
    ) -> tuple[Chunk, ...]:
        """Return the ``limit`` nearest chunks."""
        ...

    def unchanged(self, url: str, content_hash: str) -> bool:
        """Whether ``url`` was last ingested with this exact content."""
        ...

    def record(self, url: str, content_hash: str, chunks: int, skipped: bool = False) -> None:
        """Record an ingestion attempt."""
        ...

    def stats(self) -> dict[str, int]:
        """Return counts for reporting."""
        ...


class SqliteVectorStore:
    """A dependency-free vector store backed by SQLite.

    Similarity is cosine distance computed in Python. That is appropriate at the
    scale this store is for — thousands to low hundreds of thousands of chunks.
    Beyond that, replace it with a hosted store; the port is the seam.
    """

    def __init__(self, path: Path, embedder: Embedder) -> None:
        """Open or create the store.

        Args:
            path: Directory holding the knowledge database. It is created if
                absent. ``Path(":memory:")`` is honoured for tests; see the note
                on connection handling below.
            embedder: Used to size the schema and to embed queries.

        Raises:
            OSError: if the directory cannot be created or written.
        """
        self._embedder = embedder
        self._dimensions = embedder.dimensions

        # An in-memory SQLite database belongs to its connection: a second
        # connection to ":memory:" gets a *different*, empty database, so the schema
        # would vanish between operations. A shared-cache URI gives every connection
        # the same database, and one connection is held open to keep it alive.
        self._in_memory = self._in_memory_path(path)
        if self._in_memory:
            self._directory = Path(":memory:")
            self._path = f"file:architecture_agent_{id(self)}?mode=memory&cache=shared"
        else:
            # The setting is named `directory`, so honour that: create the directory
            # and keep the database file inside it. Handing sqlite the directory
            # itself either creates a *file* with that name when nothing is there
            # yet, or fails with "unable to open database file" once it exists as a
            # directory. Both behaviours are wrong; containerising the server is
            # what exposed the second one.
            self._directory = Path(path)
            self._directory.mkdir(parents=True, exist_ok=True)
            self._path = str(self._directory / DATABASE_FILENAME)

        self._keepalive: sqlite3.Connection | None = None
        if self._in_memory:
            self._keepalive = self._connect()
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    @staticmethod
    def _in_memory_path(path: Path) -> bool:
        """Whether ``path`` selects an in-memory database."""
        return str(path) == ":memory:"

    @property
    def location(self) -> str:
        """Absolute path of the database file, for logs and diagnostics."""
        return self._path

    def close(self) -> None:
        """Release the keepalive connection held for an in-memory store."""
        if self._keepalive is not None:
            self._keepalive.close()
            self._keepalive = None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, uri=self._in_memory)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def upsert(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> int:
        """Insert or replace chunks and their document rows."""
        if len(chunks) != len(embeddings):
            msg = "every chunk needs exactly one embedding"
            raise ValueError(msg)

        added = 0
        with self._connect() as connection:
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                if len(embedding) != self._dimensions:
                    msg = (
                        f"embedding for {chunk.id} has {len(embedding)} dimensions, "
                        f"expected {self._dimensions}"
                    )
                    raise ValueError(msg)
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk.document_id,
                        chunk.url,
                        chunk.topic,
                        chunk.topic,
                        chunk.tier.name,
                        chunk.confidence.value,
                        chunk.category.value,
                        chunk.id.rsplit(":", 1)[0],
                        chunk.metadata.get("ingested_at", ""),
                    ),
                )
                added += cursor.rowcount if cursor.rowcount > 0 else 0
                connection.execute(
                    "INSERT OR REPLACE INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chunk.id,
                        chunk.document_id,
                        chunk.ordinal,
                        chunk.text,
                        chunk.topic,
                        chunk.url,
                        chunk.tier.name,
                        chunk.confidence.value,
                        chunk.category.value,
                        json.dumps(chunk.metadata),
                        _pack(embedding),
                    ),
                )
        return added

    def search(
        self, embedding: Sequence[float], *, topic: str | None = None, limit: int = 5
    ) -> tuple[Chunk, ...]:
        """Return the nearest chunks by cosine similarity."""
        query = "SELECT id, document_id, ordinal, text, topic, url, tier, confidence, "
        query += "category, metadata, embedding FROM chunks"
        parameters: tuple[object, ...] = ()
        if topic is not None:
            query += " WHERE topic = ?"
            parameters = (topic,)
        query += " LIMIT 5000"

        scored: list[tuple[float, Chunk]] = []
        with self._connect() as connection:
            for row in connection.execute(query, parameters):
                chunk = _row_to_chunk(row)
                scored.append((cosine(embedding, _unpack(row[10])), chunk))

        scored.sort(key=lambda item: (-item[0], item[1].id))
        return tuple(chunk for _score, chunk in scored[:limit])

    def unchanged(self, url: str, content_hash: str) -> bool:
        """Whether ``url`` was last ingested with this content hash."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT content_hash FROM ingestion_log WHERE url = ?", (url,)
            ).fetchone()
        return row is not None and row[0] == content_hash

    def record(self, url: str, content_hash: str, chunks: int, skipped: bool = False) -> None:
        """Record an ingestion attempt."""
        from architecture_agent.domain.research import utc_now

        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO ingestion_log VALUES (?, ?, ?, ?, ?)",
                (url, content_hash, chunks, 1 if skipped else 0, utc_now().isoformat()),
            )

    def forget(self, url: str) -> None:
        """Remove every chunk from ``url``."""
        with self._connect() as connection:
            connection.execute("DELETE FROM chunks WHERE url = ?", (url,))
            connection.execute("DELETE FROM documents WHERE url = ?", (url,))
            connection.execute("DELETE FROM ingestion_log WHERE url = ?", (url,))

    def stats(self) -> dict[str, int]:
        """Return counts for the health check."""
        with self._connect() as connection:
            documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            urls = connection.execute("SELECT COUNT(*) FROM ingestion_log").fetchone()[0]
        return {"documents": documents, "chunks": chunks, "urls": urls}


def _row_to_chunk(row: Sequence[object]) -> Chunk:
    """Rebuild a chunk from a database row."""
    return Chunk(
        id=str(row[0]),
        document_id=str(row[1]),
        ordinal=int(str(row[2])),
        text=str(row[3]),
        topic=str(row[4]),
        url=str(row[5]),
        tier=_tier(str(row[6])),
        confidence=_confidence(str(row[7])),
        category=_category(str(row[8])),
        metadata=dict(json.loads(str(row[9]))),
    )


def _tier(name: str) -> SourceTier:
    return SourceTier[name]


def _confidence(value: str) -> ConfidenceLevel:
    return ConfidenceLevel(value)


def _category(value: str) -> KnowledgeCategory:
    return KnowledgeCategory(value)


def _pack(embedding: Sequence[float]) -> bytes:
    """Serialise an embedding as float32 bytes."""
    import struct

    return struct.pack(f"<{len(embedding)}f", *embedding)


def _unpack(blob: bytes) -> list[float]:
    """Deserialise a float32 blob."""
    import struct

    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity, returning 0 for a degenerate vector."""
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def build_store(path: Path, embedder: Embedder) -> VectorStore:
    """Return the default store implementation."""
    return SqliteVectorStore(path, embedder)
