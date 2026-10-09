# ADR-009: SQLite for the knowledge store, behind a declared port

- **Status:** Accepted
- **Date:** 2026-10-08
- **Deciders:** Architecture Agent Team

## Context

Milestone 7 needs vector storage. The specification says "choose the simplest
appropriate storage solution initially", and asks the architecture to support adding
a vector database later without rewriting the application.

The realistic candidates were a hosted vector database, a local file-backed engine
such as Chroma or LanceDB, and SQLite with vectors in a column.

Scale matters here: the corpus is retrieved architecture documentation, not a
user-uploaded document collection. Realistically thousands of chunks, growing to
perhaps hundreds of thousands.

## Decision

Declare `VectorStore` in `knowledge/storage.py`; implement `SqliteVectorStore`
there and nowhere else.

- Four tables: `documents`, `chunks`, `chunks_by_topic` index, `ingestion_log`.
- Embeddings stored as float32 BLOBs. Cosine similarity computed in Python.
- **Content-hash deduplication.** `ingestion_log` records the hash per URL, so a
  scheduled refresh over unchanged pages writes nothing. This is the property that
  makes refresh cheap, and it is why `ingestion_log` exists rather than deriving
  dedup from the chunk table.
- Direct `sqlite3`, no ORM. Four tables do not justify a dependency.

## Alternatives considered

**Chroma or LanceDB.** Purpose-built, better query performance. Rejected: a native
extension or a second storage engine for a corpus of thousands of chunks is more
operational surface than the problem has. Revisit when retrieval latency shows up in
a measurement.

**A hosted vector database (Pinecone, Qdrant Cloud, pgvector).** Scales without
effort. Rejected for the first release: it needs credentials, a network hop and a
cost line for a corpus that fits in a local file. It also introduces a second
failure mode into a system whose first release is meant to be operable by one
engineer.

**pgvector.** Postgres is a likely future datastore. Rejected: it would make Postgres
a hard runtime dependency of a read-only agent for no benefit at this scale.

**Keep chunks in memory and reload from disk on restart.** No database at all.
Rejected: durability and queryability are the point of the layer.

## Consequences

### Positive

- No new dependency, no credentials, no network. `update_knowledge` and
  `search_architecture_knowledge` work offline.
- Dedup by content hash is explicit and testable, not incidental.
- The port is a real seam: the second implementation is a column-store swap, not a
  rewrite.

### Negative

- **Linear scan.** Every query scans up to 5,000 rows and computes cosine in Python.
  At thousands of chunks this is milliseconds; at millions it is not. The 5,000-row
  cap is a deliberate ceiling that will surface as reduced recall rather than a
  timeout, and it is documented on the method.
- Writes are serialised through SQLite. Fine for scheduled ingestion, not for
  concurrent ingestion.
- Float32 BLOBs are not queryable by SQL functions, so the corpus cannot be filtered
  by similarity in the database.

### Neutral

- An in-memory database needs a keepalive connection and a shared-cache URI. Without
  both, every operation sees a *different* empty database. This is a real SQLite
  footgun and is handled explicitly in the constructor with a comment, because it
  presents as "the schema vanished" rather than as a connection bug.

## Operational impact

`.knowledge/` is a local directory, gitignored. The file is disposable: delete it and
re-run ingestion. Nothing else in the system depends on its contents.

## Security impact

Retrieved text is stored locally. The store contains no credentials, and query text
never reaches it — only the embedding does, so a sensitive query leaves no plaintext
trail in the database.

## Future impact

The `VectorStore` protocol has three methods a hosted store also needs: `upsert`,
`search` with an optional topic filter, and `unchanged`. Switching stores means
implementing those plus a migration that re-embeds. The embedder is a separate port,
so the model can change independently of the store.

## How to validate this decision

`tests/unit/application/test_knowledge_and_cost.py` covers chunking termination and
coverage, hash idempotency, lexical retrieval ranking, and the in-memory schema
surviving across operations.
