# ADR-006: Observability is an infrastructure package, not a top-level one

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Architecture Agent Team

## Context

NFR-27 requires structured logging with correlation fields, redaction and health
checks. The specification's package tree (§4) has no `observability/` package,
and places `infrastructure/` as a container for `persistence/`, `search/`,
`llm/`, `embeddings/` and `external/` — that is, adapters to third-party systems.
Logging is not an adapter to a third-party system; it is a cross-cutting technical
concern.

This matters because of a failure mode the specification's own §3 anticipates:
each new area invents its own logging. Once logging lives in
`infrastructure/observability/`, "where do I log?" has one answer, and the ADR
guardrails can say "no new top-level package for cross-cutting concerns" with a
concrete example behind it.

## Decision

Create `src/architecture_agent/infrastructure/observability/` with three cohesive
modules:

- `context.py` — `ContextVar`-based request correlation: `request_scope()`,
  `bind_request_context()`, `current_request_id()`, `current_operation()`.
- `logging.py` — `JsonFormatter`, `TextFormatter`, `configure_logging()`,
  `get_logger()`, `log_operation()`.
- `redaction.py` — `redact()`, `redact_text()`, `is_sensitive_key()`.

Two supporting decisions:

1. **Logging configuration is flat**, not nested: `ARCH_AGENT_LOG_LEVEL`,
   `ARCH_AGENT_LOG_FORMAT`, `ARCH_AGENT_LOG_INCLUDE_STACKTRACES`,
   `ARCH_AGENT_SERVICE_NAME`, rather than `ARCH_AGENT_OBSERVABILITY__LEVEL`.
   Logging is the knob operators tune most often; the double-underscore form for
   the most-used setting is a papercut. Every other concern stays nested.
2. **`config/settings.py` holds the logging settings**, not the observability
   package. Configuration has one home, which is what lets the project rule
   "never read `os.environ` outside `config/settings.py`" hold without exceptions.

## Alternatives considered

**A top-level `observability/` package.** Matches NFR-27's prominence and would
work. Rejected: it creates a second plausible home for cross-cutting code, and the
specification's `domain/` import rule has to enumerate it by name. Nesting it under
`infrastructure/` keeps the "no new top-level package for cross-cutting concerns"
rule enforceable by inspection.

**Fold logging into `config/`.** They co-locate today. Rejected: `config` is about
*what* the system is configured with; observability is about *what the system does*
at runtime. Merging them means every logging change touches the configuration
module, and the two have different reasons to change.

**Put redaction in a `security/` package.** Plausible, since redaction is a
security control. Rejected: redaction exists to serve the log boundary, and
extracting it "for reuse" anticipates consumers that do not exist. If a second
consumer appears — rendering a secret into a diagram, for example — it moves.

**One `observability.py` module instead of three.** Fewer files. Rejected:
redaction has a genuinely different failure mode from formatting (structural
type-based scrubbing versus serialisation), and context propagation has a
different lifetime (request scope versus process). Three modules means three
places a change belongs.

## Consequences

### Positive

- One answer to "where do I log?" for every future layer.
- The domain's no-import rule names one package instead of two.
- Redaction sits next to the formatter that applies it, so it cannot be bypassed
  by using the wrong logging entry point.
- `config/settings.py` remains the single place that reads the environment.

### Negative

- The path is longer than `observability/logging.py`. Accepted; it is the correct
  parent.
- Flat logging settings are inconsistent with the nested style used everywhere
  else. Deliberate, and recorded above so a future reader does not "fix" it.
- The specification's §4 tree now has a documented deviation. Recorded in
  `docs/architecture.md` §8.

### Neutral

- `Domain` may not import `infrastructure/observability`, so domain models cannot
  log. Correct: a model that logs during validation is a model with side effects.

## Operational impact

`configure_logging()` writes JSON lines to `stderr` and is idempotent, so the
server, the CLI health check and each test can initialise it without stacking
handlers. Third-party loggers are pinned to `WARNING`.

## Security impact

Redaction is applied at the emit boundary rather than at call sites, so a future
module cannot leak a credential by forgetting to scrub. `SecretStr` is matched by
type, so the underlying secret is never materialised as a string in order to be
scrubbed. `log_include_stacktraces` defaults to `false` because a traceback can
echo local variables.

## Future impact

Metrics and tracing join this package in the milestone that needs them, following
the same shape: `ContextVar` propagation, no call-site responsibility, one home.
If OpenTelemetry is adopted, `request_id` becomes the trace id here.

## How to validate this decision

`tests/unit/observability/test_logging.py` covers the field set, correlation
injection, redaction and timing. `tests/unit/test_settings.py` asserts the flat
variable names resolve (`test_prefixed_and_nested_environment_overrides`) and that
logging is configured only from `config/settings.py`.
