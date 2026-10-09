# ADR-002: Structured logging on the standard library, not a logging framework

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Architecture Agent Team

## Context

NFR-27 requires structured logs carrying `timestamp`, `level`, `request_id`,
`operation`, `duration`, `status` and `error`, with secrets never logged. The logs
must be consumable by an arbitrary aggregator without a bespoke parser.

The obvious choices are `structlog`, `loguru` and the stdlib `logging` module with
a custom formatter. This is a small decision with an outsized blast radius: the
logging module is imported by every layer, so a decision that turns out to be
awkward is felt everywhere.

Two specific requirements shape it:

1. **Correlation fields must come from the request in flight**, not from a
   context captured when a logger object was created.
2. **Redaction must be structural**, applied at the emit boundary so no call site
   has to remember it.

## Decision

Use the standard library `logging` module with a hand-written `JsonFormatter` and a
`contextvars`-based request context. Add a recursive `redact()` applied to every
formatted record.

Concretely:

- `get_logger(name)` returns a plain `logging.Logger`. Correlation fields are read
  from a `ContextVar` at emit time.
- `request_scope()` binds a `request_id` and operation for the duration of a unit
  of work, generating an id if the caller has not bound one.
- `log_operation()` is a context manager that emits start, completion and failure
  records with `duration_ms` and `status`.
- `JsonFormatter` collects `record.__dict__` entries outside a reserved set,
  injects the ambient correlation fields, and passes the whole payload through
  `redact()`.

**`logging.LoggerAdapter` is explicitly not used.** Its `process()` implementation
assigns `kwargs["extra"] = self.extra`, replacing the caller's `extra` mapping
instead of merging it, so structured fields attached by a tool are silently
dropped. This was found while writing the first tests, not by inspection.

## Alternatives considered

**`structlog`.** Excellent at exactly this, and better at processor composition and
testing. Rejected: it is a third-party dependency on the critical path of every
layer, its configuration has a learning curve, and the requirement (JSON lines
with a fixed field set plus redaction) is small enough to implement in about 150
lines that we fully own. Revisit if the log schema grows processor requirements
such as automatic service discovery or sampling.

**`loguru`.** Simpler API and better defaults. Rejected for the same dependency
reason, plus it replaces rather than extends the stdlib `logging` API, so every
future library that logs through `logging` needs a handler shim.

**Stdlib with `dictConfig` and an external formatter package.** The stdlib path
taken to its conclusion. Rejected: still requires a formatter package, and
`dictConfig` is file-based configuration, which conflicts with typed settings
being the single source of configuration truth.

**Context attached to the logger object.** Rejected: a logger is module-level and
long-lived; a request-scoped id baked into it is wrong for every request but one.
This is precisely why `LoggerAdapter` looked attractive and why its `extra`
behaviour is a trap.

## Consequences

### Positive

- Zero new runtime dependencies.
- Full control over the emitted schema, so the field set is a contract we can test.
- Correlation follows the request, verified by a test that reads the formatted JSON.
- Redaction is applied once, at the boundary, and cannot be forgotten at a call site.
- `SecretStr` values are redacted by type, so a secret is never materialised as a
  string just to be scrubbed.

### Negative

- Processor composition, sampling and log forwarding must be built if ever needed.
- The reserved-attribute set must be maintained if the stdlib adds `LogRecord`
  attributes.
- JSON serialisation is hand-rolled, so exotic types fall back to `str()`.

### Neutral

- Consumers that expect `logging` interop (`pytest caplog`, `dictConfig`) still work.

## Operational impact

Logs go to `stderr` as JSON lines, which is what both a container runtime and a
local terminal need. `configure_logging()` is idempotent, so repeated
initialisation in tests does not stack handlers. Third-party loggers (`httpx`,
`httpcore`, `urllib3`, provider SDKs) are pinned to `WARNING` so an upstream
retry storm cannot flood the operator's log.

## Security impact

- Credentials cannot reach a log line: `SecretStr` is redacted by type, mapping
  keys matching a credential pattern have their value replaced wholesale, and
  free-text forms of `api_key=…`, `Bearer …` and PEM private key blocks are
  scrubbed.
- Exception messages are scrubbed; stack traces are opt-in via
  `log_include_stacktraces`, default off, because a traceback can echo local
  variables.
- `TextFormatter` scrubs too, so switching output format for local debugging does
  not weaken redaction.

## Future impact

If the project adopts OpenTelemetry, the `request_id` here becomes the W3C
`traceparent` id and this formatter gains a `trace_id` field. The reserved-attribute
mechanism already supports adding fields without touching call sites.

## How to validate this decision

- `tests/unit/observability/test_logging.py` asserts the exact field set, the
  correlation injection, the redaction of both `extra` fields and `SecretStr`
  values, and that `duration_ms` and `status` appear on completion and failure.
