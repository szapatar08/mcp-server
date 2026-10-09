# Architecture Decision Records

Sequential, immutable once accepted. A decision is superseded by a **new** ADR that
references the one it replaces; accepted records are never edited.

One exception is recorded in place: **ADR-007 carries a visible correction**. It was
accepted and implemented within the same milestone, and measurement during
implementation disproved one of its decisions. Amending it with a marked correction
tells a reader what actually happened; a superseding ADR would hide the mistake behind
a second document. Corrections made after a release use a superseding ADR instead.

| ID | Title | Status | Date |
| --- | --- | --- | --- |
| [ADR-001](ADR-001-initial-architecture.md) | Layered modular architecture with inward-pointing dependencies | Accepted | 2026-10-07 |
| [ADR-002](ADR-002-standard-library-structured-logging.md) | Structured logging on the standard library, not a logging framework | Accepted | 2026-10-07 |
| [ADR-003](ADR-003-source-trust-by-provenance.md) | Source trust is derived from provenance, never from search ranking | Accepted | 2026-10-07 |
| [ADR-004](ADR-004-cost-estimates-carry-provenance.md) | Cost models cannot express a guaranteed price | Accepted | 2026-10-07 |
| [ADR-005](ADR-005-dependencies-added-per-milestone.md) | Runtime dependencies are added at the milestone that needs them | Accepted | 2026-10-07 |
| [ADR-006](ADR-006-observability-in-infrastructure.md) | Observability is an infrastructure package, not a top-level one | Accepted | 2026-10-07 |
| [ADR-007](ADR-007-mcp-runtime-and-contract.md) | MCP runtime, transport, validation strictness and error masking | Accepted, corrected | 2026-10-07 |
| [ADR-008](ADR-008-langchain-provider-abstraction.md) | LangChain behind a declared LLM port, with a deterministic fake provider | Accepted | 2026-10-08 |
| [ADR-009](ADR-009-sqlite-vector-store.md) | SQLite for the knowledge store, behind a declared port | Accepted | 2026-10-08 |
| [ADR-010](ADR-010-local-embedder-is-documented-as-lexical.md) | The local embedder is documented as lexical, not semantic | Accepted | 2026-10-08 |

## Format

```markdown
# ADR-NNN: Title

- **Status:** Proposed | Accepted | Superseded by ADR-MMM
- **Date:** YYYY-MM-DD
- **Deciders:** ...

## Context
## Decision
## Alternatives considered
## Consequences
  ### Positive
  ### Negative
  ### Neutral
## Operational impact
## Security impact
## Future impact
## How to validate this decision
```

## When an ADR is required

- A structural change to the layers, ports or dependency rules.
- Choosing a dependency, or declining one.
- Changing the MCP tool surface (also a breaking change).
- Changing a security posture or the untrusted-data policy.
- Rejecting a requirement, or resolving a contradiction in the specification.
