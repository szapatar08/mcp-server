# ADR-005: Runtime dependencies are added at the milestone that needs them

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Architecture Agent Team

## Context

The specification fixes the technology stack: LangChain for orchestration, FastMCP
for the MCP server, DuckDuckGo for search, Pydantic, pytest, Ruff and MyPy.

Milestone 1 is the foundation: configuration, logging, domain models, tooling and
CI. None of LangChain, FastMCP or DuckDuckGo is exercised by it. Adding them now
would mean three unused runtime dependencies, a slower `uv sync`, three more
advisories to track, and — most importantly — an `uv.lock` that pins versions
before the code that constrains them exists.

There is a real counter-argument: pinning the stack now documents the decision and
prevents drift. That is the trade-off this ADR resolves.

## Decision

Declare only the dependencies a milestone actually imports.

| Milestone | Dependencies added |
| --- | --- |
| 1 — Foundation | `pydantic`, `pydantic-settings` |
| 2 — MCP | `fastmcp` |
| 3 — Agent | `langchain`, `langchain-anthropic` (or the configured provider) |
| 6 — Research | `duckduckgo-search`, an HTTP client |
| 7 — Knowledge | an embedding provider and vector store |

Each milestone adds its dependency, its adapter and its tests in the same change,
and `.env.example` documents the new settings at the same time.

Guards:

- `uv lock --check` runs in CI, so the lockfile cannot drift from `pyproject.toml`.
- `pip-audit --strict` runs in CI against the exported lock, so a version added in
  a hurry is audited the day it lands. This is not theoretical: the first audit run
  found a vulnerability in the pinned `pytest` and the pin was raised to a fixed
  release in the same change.
- Ruff's `S` (bandit) rules run in CI, so a new dependency does not relax the
  security posture of the code that uses it.

## Alternatives considered

**Declare the full stack up front.** Documents the decision, pins versions early.
Rejected: version choices made before the code exists are guesses that get made
again. When LangChain is actually imported, the constraint that matters — which
`langchain-core` release the adapters target — will be known from the failing
import, not from a lockfile entry guessed months earlier. The stack is recorded in
`docs/requirements.md` §Constraints and in `README.md`, so the decision is
documented even though the dependency is not installed.

**Vendor the LLM and search HTTP calls by hand.** Zero dependencies, full control
over the untrusted-content path. Tempting for search (it is two HTTP requests) and
rejected for the LLM, where streaming, retries and structured output are a large
surface to own. Search may well end up hand-rolled; §6 will decide on the merits.

**A separate `requirements-optional.txt` for later milestones.** Same as declaring
them up front, with an extra file to forget to update. Rejected.

## Consequences

### Positive

- Milestone 1 installs in seconds and has two runtime dependencies.
- Every dependency in the lock is one that live code imports; there is no
  "transitively unused" ambiguity during an audit.
- The provider abstraction is proven by `LLMProvider.FAKE` and `SearchProvider.FAKE`
  existing before any vendor does — the ports are real, not aspirational.
- Dependency versions are chosen with the constraining code in view.

### Negative

- A later milestone may find that the chosen version of a package needs a newer
  Python or a conflicting dependency, changing the supported version range. Accepted:
  the range is `>=3.12,<3.14`, and the cost of a late discovery is small.
- CI must re-run from scratch at each milestone's first change. Negligible.

### Neutral

- `.env.example` documents settings for features that do not exist yet
  (`ARCH_AGENT_LLM__*`, `ARCH_AGENT_SEARCH__*`). Kept deliberately: the file is
  the operator's map of the system, and those settings become live two milestones
  later without a surprise. `README.md` labels them as belonging to later
  milestones.

## Operational impact

`uv sync` on a clean checkout installs two packages plus dev tooling. The build job
in CI verifies the resulting wheel is installable and imports cleanly.

## Security impact

Smaller dependency surface, and every addition audited on arrival. The CI gate
`if git ls-files --error-unmatch .env` asserts no environment file is ever tracked.

## Future impact

If a later milestone needs a vector database with an embedded server, the
comparison must weigh native-extension build requirements against a hosted
service; that decision belongs to Milestone 7, not here.

## How to validate this decision

`pyproject.toml` `dependencies` lists exactly two packages, both imported by
Milestone 1 code. `pip-audit --strict` and `uv lock --check` are CI steps.
