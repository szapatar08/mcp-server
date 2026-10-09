# ADR-010: The local embedder is documented as lexical, not semantic

- **Status:** Accepted
- **Date:** 2026-10-08
- **Deciders:** Architecture Agent Team

## Context

Retrieval needs embeddings. Options were a local sentence-transformers model, a
hosted embedding API, or a deterministic non-semantic embedder.

A local transformer is ~90 MB of weights, pulls in `torch`, and — decisively for a
read-only agent whose whole point is low operational burden — needs a download step
before `search_architecture_knowledge` works for the first time. A hosted API adds a
credential and a per-call cost to a read-only tool.

## Decision

Ship `HashedNGramEmbedder`: lowercase alphanumeric tokens, hashed character
trigrams into fixed buckets, term-frequency weighted, L2 normalised.

**The capability resource and the class docstring both state that it is lexical, not
semantic.** Lexical overlap produces similarity; paraphrase does not.

Configure a real embedding provider by changing one setting — the `Embedder` port
means no caller changes.

## Alternatives considered

**A local sentence-transformers model.** Genuinely semantic. Rejected for now: the
dependency and download weight are not justified before there is a retrieval quality
measurement showing lexical search is insufficient. This is the decision most likely
to be reversed, and the port makes reversing it cheap.

**A hosted embedding API.** Best quality, no local weight. Rejected: a credential and
a cost line for a read-only tool, plus a network dependency in the middle of the
evidence path, where a failure must be degradable.

**BM25 over a real tokeniser.** Better lexical matching than hashed trigrams, and
better than an embedding for keyword search. Rejected as the default because the
`Embedder` port already carries the vector interface the storage layer expects;
swapping in BM25 means a search implementation change, not an embedder change.

## Consequences

### Positive

- Zero download, zero credentials, deterministic across machines, ~1 ms per query.
- Tests are reproducible because the vector is a pure function of the text.
- An operator can compare a real embedder against the baseline on their own corpus.

### Negative

- Paraphrase will not match. "How much does it cost" will not retrieve "pricing".
- Hash collisions dilute similarity slightly at low bucket counts.
- Retrieval quality is **not** validated against a relevance ground truth, because no
  such set exists yet. That is a genuine gap, recorded rather than hidden.

### Neutral

- `build_embedder` already accepts a provider name; adding a real one is one class
  implementing the port.

## Operational impact

`pip install` is enough to run knowledge search. There is no model to warm, download
or version-pin.

## Security impact

The embedder is local and deterministic, so no user query text leaves the machine
during embedding. A hosted embedder would need that documented as a data-flow change.

## Future impact

The honest next step is not a better default embedder but a relevance set: about fifty
queries with expected chunk URLs, so "is lexical search good enough" becomes a
measurement rather than an opinion. That belongs in Milestone 10's performance tier.

## How to validate this decision

`tests/unit/application/test_knowledge_and_cost.py` asserts only the property this
embedder actually has — lexical overlap ranks the right chunk first — and does not
claim semantic quality it cannot deliver.
