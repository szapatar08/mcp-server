# ADR-003: Source trust is derived from provenance, never from search ranking

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Architecture Agent Team

## Context

FR-019 ranks sources: official documentation, official standards, official
repositories, official engineering blogs, recognized technical organizations,
qualified publications, community sources. It then states explicitly: "Do not
treat search ranking as proof of correctness."

That leaves the question of *how* a source is assigned a tier. DuckDuckGo returns
an ordered result list; position in that list is a function of the query, the
index and the ranking model, none of which is evidence of authority. A highly
ranked SEO page about "AWS vs Lambda" is not documentation.

The agent's credibility depends entirely on this decision. Pricing and capability
claims are the highest-consequence output it produces, and a confident wrong price
is worse than no answer.

## Decision

Assign tiers from **URL host classification**, and derive confidence from tier plus
independent corroboration — never from rank.

- `TierClassifier.classify(url)` matches the registrable host against curated sets
  for documentation, standards, vendor blogs, recognized organizations and
  qualified publications.
- **Unrecognised hosts fall back to `COMMUNITY`.** The classifier never promotes an
  unknown host by optimism.
- **GitHub is only tier 3 when the owning organisation is a known vendor or
  upstream project** (`kubernetes`, `aws`, `hashicorp`, `langchain-ai`, …). A
  random personal repository is `COMMUNITY`, because "official" is a property of
  the owner, not of the hosting platform.
- Deployments can extend the documentation set with `extra_official_hosts` for
  internal wikis, without patching the classifier.
- `assess_confidence(tier, corroborating_sources)` is a fixed decision table:

| Tier | Confidence |
| --- | --- |
| Standard or better | `HIGH` |
| Documentation / official repository | `HIGH` with ≥1 corroborating source, else `MEDIUM` |
| Vendor engineering blog | `HIGH` with ≥2, else `MEDIUM` |
| Recognized organization / qualified publication | `MEDIUM` with ≥1, else `LOW` |
| Community | `LOW` with ≥2, else `SPECULATIVE` |

The consequences are load-bearing: **a single community source can never produce
high confidence**, and `CostEstimate` refuses `HIGH` confidence unless at least one
`PricingReference` is attached.

## Alternatives considered

**Lexical relevance scoring (BM25 / embedding similarity over retrieved text).**
Better at topical relevance. Rejected as the *authority* signal: it measures
whether a document is on-topic, not whether it is true. It remains appropriate as a
*ranking* signal within one tier, which is where the knowledge layer will use it.

**LLM-as-judge source scoring.** Scales to arbitrary rubrics and reads well.
Rejected: it is unauditable, non-deterministic, adds an LLM call to a path that must
work when the LLM is unavailable, and a model asked "is this source authoritative"
is answering from its own priors rather than from evidence — precisely the failure
mode this ADR exists to prevent.

**Trust the top-N search results.** Simplest. Rejected: directly contradicts FR-019,
and demonstrably wrong on commercial SEO content.

**Manual allowlist of every documentation URL.** Highest precision. Rejected: cloud
documentation is thousands of paths across dozens of hosts; the list would be stale
within a release cycle. Host-level classification with a conservative fallback is
the sustainable version of the same idea.

**Per-query human verification.** The only fully reliable option. Rejected as the
default; retained as the answer for high-stakes claims, which is why
`ResearchResult.authoritative_sources()` exists so a caller can refuse to proceed
without one.

## Consequences

### Positive

- The trust signal is deterministic, auditable and testable without a network.
- "Why is this source tier N?" has a one-line answer: the host matched a curated
  set, and the set is in the source.
- Unrecognised sources are visible as low-confidence instead of being silently
  averaged into a confident-sounding answer.
- A hostile page cannot buy authority by ranking well.

### Negative

- Host lists need maintenance as vendors reorganise documentation (a real cost).
- A legitimate new vendor is tier 7 until added, which under-rates it rather than
  over-rating it. Failing safe is the correct direction for this system.
- Host classification cannot distinguish a vendor's own unvetted blog post from its
  API reference on the same host; both land in different tiers only if they are on
  different hosts. Accepted as a known limitation.

### Neutral

- Corroboration count is caller-supplied. The knowledge layer must supply *independent*
  sources; counting two pages from the same host as corroboration would defeat the
  check, and that responsibility is recorded in `docs/architecture.md`.

## Operational impact

None today. The classifier is a pure function; it costs nothing at runtime. The
`extra_official_hosts` hook means an operator can trust an internal wiki without a
code change.

## Security impact

- A prompt-injection payload hosted anywhere lands at `COMMUNITY` /
  `SPECULATIVE` unless the host is in a curated set, so authority is not
  attacker-controllable.
- Should an attacker obtain a page on an allowlisted documentation host, the
  corroboration rule requires an independent source before confidence rises. This
  does not eliminate the attack; it removes the single-source case.

## Future impact

Per-host overrides should become per-path overrides (a vendor blog post about a
specific product may deserve tier 1, not tier 4). That is a natural extension of
`extra_official_hosts` and does not change the decision table.

## How to validate this decision

`tests/unit/domain/test_research.py::TestTierClassification` covers vendor docs,
standards bodies, known and unknown repository owners, secondary sources and
unknown hosts, plus case and `www` normalisation.
`::TestConfidence` pins the decision table, including that a lone community source
is `SPECULATIVE`.
