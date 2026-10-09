# Security

The agent holds provider credentials, retrieves untrusted content, and produces
engineering recommendations that may be acted on. It is a powerful tool, so its
own security posture is a first-class concern.

## Posture

**The first release is read-only.** The agent reads, analyses, researches, designs,
recommends and generates. It does not execute shell commands, deploy, destroy or
modify infrastructure. This is enforced by `SecuritySettings.allow_execution_tools`,
which defaults to `False`, and by there being no tool that performs I/O on a
customer system.

Execution capability requires: an ADR, an explicit authorization model, defined
safety boundaries, and its own security review. It is not a matter of adding a
flag.

## Threat model

| # | Threat | Vector | Control | Status |
| --- | --- | --- | --- | --- |
| T1 | **Indirect prompt injection** | A retrieved page says "ignore your instructions" or "run this command" | `UntrustedContentGuard`: nonce fencing so a payload cannot forge its own boundary, control-character stripping, `</` escaping, and instruction-shaped content reported as evidence and never followed | **Implemented and tested** |
| T2 | **Secret leakage via logs** | A credential reaches a log record, exception or MCP response | `SecretStr`; structural and textual redaction at the logging boundary; stack traces off by default | **Implemented and tested** |
| T3 | **Unverified claims** | A pricing or capability figure is invented or taken from a ranking | `assess_confidence` from tier and corroboration; `HIGH` confidence impossible without a `PricingReference` | **Implemented and tested** |
| T4 | **Command injection via content** | A retrieved page contains a shell command that gets executed | No execution capability (see Posture); content is never passed to a shell | **Implemented by absence** |
| T5 | **SSRF** | A malicious page or link redirects the fetcher to an internal endpoint | `SecuritySettings.allowed_domains` with exact, subdomain and suffix-safe matching; `GuardedFetcher` checks before any connection; the HTTP fetcher refuses off-host redirects; fetch budget `max_fetched_characters` | **Implemented and tested** |
| T6 | **Malformed tool input** | Wrong types, unknown fields, oversized strings, injection in string fields | Pydantic validation at the MCP boundary; `extra="forbid"` rejects unknown fields; length bounds on every string field | **Implemented and tested** |
| T13 | **Diagram syntax injection** | A component name containing `"` or `-->` injects a diagram directive | Positional identifiers so input never reaches an identifier position; conservative label sanitisation with every alteration reported as a warning; structural validation | **Implemented and tested** |
| T14 | **Error-message disclosure** | An exception message quotes a credential or an internal hostname | `mask_error_details=True` masks unexpected errors; expected failures are raised as `ToolError` with a scrubbed, field-level message | **Implemented and tested** |
| T7 | **Hostile markup** | Retrieved HTML with script tags or image-URL exfiltration | Retrieved content is stored and quoted as text; rendering strips markup. Diagrams are generated, never fetched | Design set; renderer in Milestone 9 |
| T8 | **Configuration tampering** | A committed `.env` or a committed secret | `.env` gitignored; CI gate asserts no `.env` is tracked; `detect-private-key` pre-commit hook | **Implemented and enforced in CI** |
| T9 | **Dependency compromise** | A vulnerable or malicious package | Lockfile committed and checked in CI; `pip-audit --strict`; dependencies added only when imported (ADR-005) | **Implemented and enforced in CI** |
| T10 | **Silent degradation** | Search fails and the agent answers as if it had evidence | `ResearchResult.degraded` requires a reason; no result means low or speculative confidence | **Implemented and tested** |
| T11 | **Over-broad permissions** | The agent gains write or execute rights it does not need | Read-only posture; per-request egress allowlist; no credential in an MCP response | **Implemented by default** |
| T12 | **Denial of service** | Expensive repeated research or unbounded context | Cache TTLs per data class (not yet wired), `max_fetched_characters`, per-request result caps, guard block budget, chunk budget | Partially implemented; no LLM or search cache yet |

## Secret handling rules

1. Credentials are `SecretStr`. They are not printed, logged, or serialised in
   cleartext, including through `repr()`, `model_dump()` and `model_dump_json()`.
2. When an API key is not configured, the provider adapter falls back to the
   vendor's own environment variable rather than receiving an empty string.
3. Redaction happens at the logging boundary, so a new module cannot leak by
   forgetting to scrub. Mapping keys matching a credential pattern are replaced
   wholesale; free-text credential shapes (`api_key=…`, `Bearer …`, PEM private key
   blocks) are scrubbed from messages.
4. Secrets never appear in a diagram, a generated document, an ADR or an MCP
   response.
5. `log_include_stacktraces` defaults to `false`: a traceback can echo local
   variables.

## Untrusted content rules

1. Retrieved content is **evidence**. It is quoted, attributed to its source, and
   carries a tier and a retrieval timestamp.
2. Content is never concatenated into a system or instruction slot.
3. Instruction-shaped content is **recorded** as an injection attempt and reported,
   not obeyed. The report is part of the response so the operator can see it.
4. Source authority is derived from provenance, never from search position
   (ADR-003).
5. Retrieved content is size-capped (`max_fetched_characters`) and treated as
   hostile to the renderer.

## Validation rules

- Every MCP input is validated by a domain model before any I/O. The error names
  the offending field; it never includes a stack trace.
- Every domain model uses `extra="forbid"`, so an unknown field is an error rather
  than a silently ignored typo.
- Enum-typed inputs reject unknown values instead of falling back to a default.
  `ArchitectureStyle.UNKNOWN` exists for parsed prose, not for user input.
- String fields carry explicit length bounds.

## Dependency policy

- Runtime dependencies are added at the milestone that imports them (ADR-005), so
  every package in the lock is one that live code uses.
- `uv.lock` is committed and verified with `uv lock --check`.
- `pip-audit --strict` gates every build. This is not theoretical: the first audit
  found a vulnerability in the pinned `pytest`, and the pin was raised in the same
  change.
- Ruff's `S` (bandit) rules run in CI alongside linting.

## Operational controls

| Control | Purpose |
| --- | --- |
| JSON logs on `stderr` with `request_id`, `operation`, `duration_ms`, `status`, `error` | Triage without exposing payload |
| `request_scope()` per MCP tool call | One request can be followed end to end |
| `log_operation()` | Duration and failure rate per operation |
| `scripts/health_check.py` | Detect misconfiguration before serving traffic |
| Structured MCP errors | Client can act without parsing stack traces |

## Reporting

Suspected vulnerabilities should be reported privately to the maintainers rather
than opened as a public issue. Include the affected version, the input that
triggers it, and the observed behaviour.
