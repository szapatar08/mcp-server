# Testing strategy

Testing is a first-class component, not a phase. A change that adds behaviour
without adding a test that verifies that behaviour is incomplete.

## Principles

1. **No network, no credentials.** Every test runs offline. Anything needing an
   LLM or a search backend uses the `fake` provider or a stub adapter.
2. **Test behaviour, not implementation.** A test asserts what the system does for
   an input, not which internal method it called.
3. **Invariants get negative tests.** For every rule the domain enforces, there is a
   test proving the model rejects the bad input. A validation rule with no negative
   test is untested.
4. **Every bug becomes a regression test.** Fixed in `tests/unit/`, cited from the
   commit message.
5. **Coverage is a signal, not a target.** Current coverage is above 95% of branches. The
   uncovered lines are getters and defensive branches; no invariant is untested.
   Do not add a test to move a number.

## Tiers

| Tier | Directory | Scope | Needs network | Runs in CI |
| --- | --- | --- | --- | --- |
| Unit | `tests/unit/` | Domain models, configuration, logging, redaction, services, renderers | No | Yes, every push |
| Integration | `tests/integration/` | Adapters, LangChain, search, persistence, embeddings | No (mocked) | Yes |
| MCP contract | `tests/mcp/` | Startup, discovery, schema snapshot, invocation, invalid input, resources, prompts | No | Yes |
| End-to-end | `tests/e2e/` | Full scenario: request to structured answer | No | Yes |
| Security | `tests/security/` | Injection, secret leakage, malformed input, SSRF | No | Yes |
| Evaluation | `tests/evaluation/` | Agent output quality against a scored dataset | No | **Empty** — not yet built |
| Performance | `tests/performance/` | Latency, concurrency, memory, cache effectiveness | No | **Empty** — not yet built |

Markers mirror the tiers: `pytest -m unit`, `pytest -m security`, and so on.

## What each tier is for

### Unit

The domain is the system. Most of the test budget belongs here. Priorities:

- **Validation boundaries.** Exact bounds, exact error messages where the message
  is part of the MCP contract.
- **Derived values.** `monthly_total()`, `overall_score()`, `missing_signals()`,
  `bounds()` — computed, never asserted against a hand-written literal.
- **Decision tables.** `assess_confidence`, `TierClassifier` host matching.
- **Security of the log boundary.** Redaction of mapping keys, `SecretStr`,
  free-text credential shapes and exception messages.

### Integration

Adapters against stubs and recorded fixtures. Not live-provider tests: a test that
fails because a vendor had an incident is worse than no test, because it trains the
team to ignore red builds. Contract behaviour is pinned with recorded fixtures.

### MCP contract

The MCP surface is a public API (ADR-007), so it is tested as one:

- Server starts and lists tools; each tool is described and declares itself
  read-only.
- Tool names, descriptions, input schemas and output schemas are snapshotted to
  `tests/mcp/snapshots/tool_schemas.json`. Regenerate with
  `pytest tests/mcp --update-contracts`, never implicitly.
- `additionalProperties: false` is asserted on both the request object and the
  nested domain model, so a regression that drops `extra="forbid"` fails.
- Output schemas are asserted *precise*, not `{"type": "object"}`.
- **Conforming JSON is accepted.** A test sends arrays and enum strings, because
  strict validation once made every tool uncallable (ADR-007 correction).
- Invalid input produces a validation error naming the field, not a stack trace.
- An internal error's message never reaches the client.
- Resources and prompts resolve and carry the agent's rules.

### End-to-end

Scenarios from the specification: design an architecture, estimate AWS cost,
review an architecture. Each asserts the *sections* the response must contain —
security, reliability, cost, assumptions, sources — not exact wording. Using the
`fake` provider keeps them deterministic.

### Security

Mandatory for anything that touches retrieved content or credentials:

- **Prompt injection.** Retrieved documents containing "ignore previous
  instructions", "reveal your system prompt", "run this command" must not change
  agent behaviour; the attempt is recorded as evidence.
- **Hostile markup.** Script tags, HTML comments and markdown image exfiltration
  URLs in retrieved content must not render or fetch.
- **Secret leakage.** A `SecretStr` credential must not appear in any log record,
  exception message, MCP response, generated diagram or generated document.
- **Malformed tool input.** Wrong types, unknown fields (rejected by
  `extra="forbid"`), oversized strings, injection in string fields.
- **SSRF.** URLs outside `SecuritySettings.allowed_domains` must be refused; the
  check is tested for exact hosts, subdomains and suffix-confusion attacks
  (`evil-example.com` against an `example.com` allowlist).
- **Diagram syntax injection.** A hostile architecture whose names, technologies,
  protocols and responsibilities carry quotes, newlines and arrow sequences must
  still render valid source, with no fragment of the payload escaping a quoted
  string. The property tested is *containment*, not absence: a payload word may
  survive as a sanitised label, but never as structure.
- **Error-message leakage.** A secret inside an exception message must not reach
  the client or the log, in either the masked or the rejected path.
- **Over-broad permissions.** The default posture denies execution tools; a test
  asserts `allow_execution_tools` defaults to `False`.

### Evaluation

A dataset, not a test suite. Each case defines an input, required concepts,
forbidden behaviours and criteria. A case passes when the output covers the
required concepts and violates no forbidden behaviour — **not** when the text
matches a reference. Text similarity is the wrong target for a system whose value
is reasoning quality.

Planned cases: microservices design, monolith versus microservices, AWS
architecture, Kubernetes architecture, database selection, high availability,
disaster recovery, security architecture, cost optimisation, CI/CD.

## Fixtures

`tests/fixtures/` holds recorded, sanitised payloads: retrieved pages, pricing
extracts, and provider responses. Fixtures must be small enough to read in full and
must not contain real credentials or customer data.

## What CI enforces

- `ruff format --check` and `ruff check` (including `S` bandit rules).
- `mypy` strict.
- `pytest` with coverage on Python 3.12 and 3.13.
- `pip-audit --strict` on the exported lock.
- A gate asserting no `.env` is tracked.

Pre-commit runs the same checks locally before a commit reaches CI.

## Adding tests

| Change | Required test |
| --- | --- |
| New domain field with a constraint | Boundary and negative test |
| New validation rule | Negative test asserting the error |
| New MCP tool | Contract test plus an evaluation case if it reasons |
| Bug fix | Regression test in the tier where the bug appeared |
| Anything touching retrieved content | Injection test |
| Anything touching credentials | Leakage test across logs, errors and responses |
| Anything rendering caller text into a syntax | Containment test: no payload fragment outside a quoted span |
| New setting | Test in `tests/unit/test_settings.py` and `.env.example` entry |

## Running

```bash
uv run pytest                                        # everything
uv run pytest -m unit                                # fast tier
uv run pytest -m security                            # security tier
uv run pytest -m "not evaluation and not performance"  # exclude slow tiers
uv run pytest --cov=architecture_agent --cov-report=term-missing
uv run pytest tests/unit/domain/test_cost.py -v      # one file
```
