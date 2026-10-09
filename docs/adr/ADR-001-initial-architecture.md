# ADR-001: Layered modular architecture with inward-pointing dependencies

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Architecture Agent Team

## Context

The specification requires a modular architecture rather than a monolithic
`agent.py`, separates domain, application logic, infrastructure, integrations, AI,
MCP, knowledge and search, and asks for dependency inversion "where appropriate".

The system will contain at least three LLM providers, one search provider that may
be replaced, at least three cloud providers, more than one diagram format, and
more than one vector store. It also has to be testable without network access or
provider credentials, which rules out any design where reasoning and I/O are
entangled.

The tempting simplification is a small package with functions that call OpenAI and
DuckDuckGo directly. It is faster to write and impossible to reason about: the
tests become integration tests, every provider swap is a rewrite, and the domain
invariants become untestable strings.

## Decision

Adopt four layers with strictly inward-pointing dependencies:

1. **`domain/`** — pure Pydantic models and invariants. Imports nothing from this
   project except `domain`. No I/O, no third-party SDKs.
2. **`application/`** — use-case services. Depends on `domain` and on *ports*
   (Python `Protocol`s) that it declares itself, next to the use case that needs
   them.
3. **`mcp/`, `agent/`, `tools/`** — presentation and orchestration. Depend on
   `application` and on ports. Never on adapter internals.
4. **`infrastructure/`** — adapters implementing the ports, plus cross-cutting
   technical concerns such as observability.

Two supporting rules:

- **Ports are declared by the consumer, not by the provider.** `application/design.py`
  declares the two protocols it needs rather than importing an `LLMClient` that
  someone else will later grow.
- **An adapter must not know it is served over MCP.** `infrastructure/llm/` does
  not import `mcp/`. This is the constraint that keeps adapters unit-testable.

Enforcement is by review and by the test suite, not by an import-linter plugin:
a plugin adds a dependency and a class of false positives to catch a rule that a
human reviewer can check in seconds. The rule is instead asserted structurally —
every domain model is constructible and validatable with no adapters installed, and
the test environment installs none.

## Alternatives considered

**Flat package with subfolders (`models/`, `services/`, `adapters/`).**
Simpler to navigate. Rejected: without a layering rule, nothing stops
`models/architecture.py` from importing an LLM SDK, and the first such import
erases the benefit of having typed models at all.

**Ports-and-adapters in one module (`ports.py` next to `adapters.py`).**
The canonical presentation of this pattern. Rejected for now: it front-loads an
interface inventory before any use case exists. The interfaces are cheaper to
write once the use case has shown what it actually needs; a wrong port written
early is worse than no port.

**Event-driven architecture between services.** Rejected as disproportionate. The
workload is request/response from an MCP client; there is no asynchronous
workload to decouple, and the framework would cost more in debugging than it saves
in throughput.

**A single service with an explicit `AgentState`.** Rejected: it makes reasoning
steps mutable global state, which is exactly the "hidden global state" the project
rules forbid.

## Consequences

### Positive

- The domain is testable with no network, no credentials and no adapters.
- Swapping an LLM, a search backend or a vector store touches one package.
- The MCP contract can change without touching reasoning, and reasoning can change
  without touching the MCP contract.
- Unit tests stay in the millisecond range, so the fast tier stays fast.

### Negative

- More indirection for the first three use cases: a use case is a service, a port
  and an adapter rather than one function.
- Ports written before their use case exists can be wrong; the mitigation is to
  declare ports only when a use case needs one.
- The layering rule is not mechanically enforced, so it depends on review.

### Neutral

- `tools/` and `application/` overlap in purpose. `application/` holds the use
  case; `tools/` holds the MCP-shaped adapter. When the distinction proves noisy
  it can be merged — that would be a new ADR.

## Operational impact

None today: Milestone 1 contains only `domain/`, `config/` and
`infrastructure/observability/`. The rule constrains Milestones 2–9.

## Security impact

- The domain cannot perform I/O, so a domain model cannot exfiltrate anything by
  construction.
- Prompt-injection defences live in one place — the untrusted-content guard in
  `application/` — rather than being spread across call sites.

## Future impact

Portability is the point: a second vector store or a second LLM provider is an
addition to `infrastructure/`, not a change to `application/`.

## How to validate this decision

- Every model in `domain/` is constructible in a test with no adapters installed.
- Adding a vendor SDK touches only `infrastructure/`.
- `grep` finds no import of `infrastructure`, `agent`, `tools`, `mcp` or
  `knowledge` inside `domain/`.
