# ADR-007: MCP runtime, transport, strict validation and error masking

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Architecture Agent Team

## Context

Milestone 2 exposes the first two capabilities over MCP: `review_architecture` and
`generate_diagram`. The MCP surface is a public API contract (CLAUDE.md §3), so the
choices made here are hard to reverse.

The specification fixes FastMCP but not how it is configured. FastMCP 4.0.11 has
three constructor options whose defaults are unsafe for this project. Each was
verified by experiment before deciding:

| Setting | Library default | Observed behaviour |
| --- | --- | --- |
| `strict_input_validation` | `False` | `"10"` silently coerced to `10` for an `int` field |
| `mask_error_details` | `False` | Raw exception messages included in the client response |
| transport | stdio | — |

Two further questions were open before this decision:

1. **Does FastMCP preserve `extra="forbid"` in the generated JSON schema?** The
   roadmap flagged this as a risk. Measured: yes — `additionalProperties: false`
   appears on both the wrapper object and the nested model.
2. **Can an internal error leak a secret across the MCP boundary?** Measured: with
   `mask_error_details=True`, a tool raising `RuntimeError("internal secret:
   sk-live-abc123")` reaches the client as `Error calling tool 'boom'` and nothing
   more.

## Decision

### Runtime

FastMCP 4.x, `fastmcp` added as the only new runtime dependency for this milestone.
Both first tools are pure over the domain, so Milestone 2 needs no LLM and no search
adapter.

### Transport

Default **stdio**, selected from `MCPTransport` (already in settings, so no new
setting is introduced). `streamable-http` is supported and configurable. stdio is
the default because MCP clients — Claude Desktop, Claude Code, IDE extensions —
launch the server as a child process; requiring an HTTP listener would add a port,
a bind address and an authentication surface for no benefit.

When HTTP is selected, `host` defaults to `127.0.0.1`, not `0.0.0.0`: this server
has no authentication, so it must not be exposed to a network by default.

### Server configuration

| Setting | Value | Why |
| --- | --- | --- |
| `strict_input_validation` | `False` | Measured: `True` rejects the JSON-native forms of this API's own types. See the correction below |
| `mask_error_details` | `True` | An unexpected exception must not describe our internals |
| `dereference_schemas` | `True` (default) | Readable tool schemas for clients |

The constant is `STRICT_INPUT_VALIDATION` in `mcp/server.py`, so the decision is
visible in code rather than buried in a constructor call.

## Correction: `strict_input_validation`

**This ADR originally specified `strict_input_validation=True` and that was
wrong.** The error was found by measuring rather than reading: the first
end-to-end call failed on input that was perfectly conforming JSON.

With `True`, Pydantic validates tool arguments in strict mode, which rejects:

| Sent over the wire | Declared as | Result under strict |
| --- | --- | --- |
| `[{"name": "api"}]` | `tuple[SystemComponent, ...]` | rejected, `tuple_type` |
| `"modular_monolith"` | `ArchitectureStyle` | rejected, `is_instance_of` |
| `"99.9"` | `float` | rejected, `float_type` |

JSON has no tuples and no enums. Every MCP client sends arrays and strings, so
strict validation made **all tools uncallable** — and the `extra_forbidden` check,
which is the guarantee that actually matters, never even got reached.

With `False`, every guarantee that protects a caller survives:

| Input | Result |
| --- | --- |
| Arrays and enum strings | accepted, converted |
| Unknown field | rejected, `extra_forbidden` |
| Enum value outside the enum | rejected |
| `"not-a-number"` for a float | rejected |
| Out-of-range value | rejected |

What is lost is refusal to accept `"99.9"` as a number. That is representational
coercion, which a JSON protocol requires, not a bug the client can be blamed for.

**The original reasoning was backwards.** I optimised against a hypothetical client
bug at the cost of making the tool unusable — and a tool that cannot be called has
no bug to protect against. The rule that survives: at a protocol boundary, measure
what conforming traffic does before enabling strictness.

ADR-005's rule about measuring rather than guessing applies to configuration
choices as much as to code.

### Error contract

Two layers, deliberately:

1. **Expected failures** — the tool layer converts domain `ValidationError` into
   `ToolError` with a scrubbed, field-level message. Visible to the client,
   actionable, no stack trace.
2. **Unexpected failures** — anything else propagates and FastMCP masks it.

`mask_error_details=True` is what makes layer 2 safe. It also means every *intended*
message must be raised explicitly as `ToolError`; a message that "looks safe but is
masked" is indistinguishable from a bug at the call site, so `application/errors.py`
is the single place that converts to `ToolError`.

Pydantic echoes the rejected `input_value` in validation errors. That value came
from the same client that receives the error, so echoing it is not a disclosure —
but it means validation messages are *not* run through secret scrubbing for the
client's benefit. They are still scrubbed for the server log.

### Output typing

Tools return **Pydantic models, never `dict`**. Measured: a tool annotated `-> dict`
produces the near-useless output schema `{"type": "object",
"additionalProperties": true}`, whereas a model returns its full JSON Schema. A
client cannot verify a response shape it was not told, so an untyped tool result
is a contract hole.

### Contract snapshot

`tests/mcp/test_contract.py` snapshots every tool name, description, input schema
and output schema to a committed JSON file. A schema change fails the test until the
snapshot and an ADR change together. This is what makes "the MCP interface is an
API contract" enforceable rather than aspirational.

## Alternatives considered

**`mask_error_details=False` for better diagnostics.** Clients would see real error
messages. Rejected: exception messages in this system can quote user input,
configuration values and provider responses. "Diagnostics" are also available
server-side in the logs, where redaction applies.

**Return `dict` and document the shape in the description.** Avoids model
choreography. Rejected for the schema reason above.

**HTTP only, no stdio.** Easier to containerise. Rejected: every major MCP client
spawns stdio servers; making HTTP the only mode would exclude the primary
consumers, and an unauthenticated HTTP listener on a routable interface is a
liability (T11 in `docs/security.md`).

**Write the MCP server on the raw `mcp` SDK.** Total control. Rejected: schema
generation, transport handling and error semantics are all reinvented, and the
specification chose FastMCP for that reason.

## Consequences

### Positive

- A client cannot accidentally mutate input through type coercion.
- An unexpected exception cannot leak a secret or an internal path.
- Tool output schemas are precise enough for a client to validate a response.
- A schema change is caught in CI, not discovered by a broken integration.
- `additionalProperties: false` survives schema generation, so unknown-field
  rejection works at the protocol boundary without re-validation.

### Negative

- Lax validation accepts `"99.9"` for a float field. A client sending a numeric
  string where a number was declared gets no complaint. Accepted: the alternative
  was rejecting conforming JSON outright.
- Masked errors make a genuine bug harder to diagnose from the client side. Mitigated
  by server-side structured logs carrying `request_id`, `operation` and `error.type`.
- The committed schema snapshot must be regenerated on every intentional change,
  which is friction by design.

### Neutral

- `fastmcp` pulls in `mcp`, `starlette`, `uvicorn` and an OpenTelemetry API stack.
  Accepted: the specification chose FastMCP, and the transport implementation is
  most of that weight.

## Operational impact

`python -m architecture_agent.mcp.server` runs the server. Logs go to `stderr` as
JSON, which is the channel stdio transports reserve for the protocol, so logging is
configured for `stderr` explicitly.

## Security impact

- T6 (malformed input): enforced by the schema plus `extra="forbid"`. The
  strictness regression above was found by a test that sends conforming JSON and
  asserts it is accepted; that test is the guard against reintroducing it.
- T11 (over-broad permissions): stdio default, `127.0.0.1` HTTP default, no auth
  exposed by default.
- T2 (secret leakage): `mask_error_details` closes the response path; the
  `application/errors.py` scrubber closes the log path.
- Diagram syntax injection from user-supplied component names is handled separately
  by the renderer sanitiser and covered by `tests/security/`.

## Future impact

Authentication for HTTP transport is not implemented. Adding it later is an additive
change to `mcp/server.py`; the tools themselves need no authentication awareness,
because they are read-only and hold no per-user state.

## How to validate this decision

- `tests/mcp/test_contract.py` — snapshots names, descriptions and both schemas;
  asserts `additionalProperties: false` survives on input and output; asserts
  JSON-native input is accepted and unparseable values are rejected; asserts an
  internal error's message does not reach the client.
- `tests/security/test_mcp_boundary.py` — asserts secret scrubbing in errors and
  logs, malformed-input rejection, and that every tool is read-only.
