# ADR-008: LangChain behind a declared LLM port, with a deterministic fake provider

- **Status:** Accepted
- **Date:** 2026-10-08
- **Deciders:** Architecture Agent Team

## Context

The specification fixes LangChain as the orchestration framework and requires the
business logic not to be tightly coupled to one LLM provider. Milestone 3 must add
an LLM dependency, and ADR-005 says dependencies arrive at the milestone that
imports them.

Four options shape the abstraction, and the differences show up in testing long
before they show up in production.

## Decision

Declare `LLMClient` in `application/llm_service.py`; implement it twice in
`infrastructure/llm/`.

1. **`LangChainLLMClient`** — the only module importing LangChain. Uses
   `init_chat_model`, so a provider change is configuration, not code. Structured
   output is **parsed, not trusted**: the model is asked for JSON, the JSON is
   validated against a domain model, and a mismatch is a failure.
2. **`FakeLLMClient`** — a scripted queue, selected by `LLMProvider.FAKE`. Not a
   test double; it is the offline operating mode. An exhausted script raises rather
   than inventing an answer, so a test cannot silently pass on a fabricated
   response.

Structured output gets **exactly one retry**. A provider that returned malformed
JSON once usually returns correct JSON when told what it did wrong; beyond two
attempts the honest answer is to report failure.

## Alternatives considered

**Use LangChain agents and chains directly.** Fastest to a first prompt.
Rejected: it puts the use case inside a framework abstraction, so testing a design
means instantiating a chain graph, and swapping providers means rewriting the use
case. The port exists precisely so that neither is required.

**Wrap the vendor SDK directly, no LangChain.** Fewer layers. Rejected for the LLM:
streaming, retries and structured output are a large surface to own for one
provider. Worth revisiting for *search*, where it is two HTTP requests — Milestone 6
uses a two-line client on purpose.

**Structured output via a tool call or provider-native schema.** Better fidelity
where supported. Rejected as the only path: support is uneven across providers and
versions, so a provider-specific mechanism would fragment the abstraction. The JSON
envelope works everywhere.

**A retry policy with exponential backoff.** Needed eventually. Rejected *as part of
this decision*: the retry that matters here is the malformed-output retry, which is
not the same as a transport retry. Rate limiting and backoff belong with the provider
adapter once a provider is actually selected.

## Consequences

### Positive

- Every reasoning test runs offline, in milliseconds, with no credential.
- A provider swap touches one module and one setting.
- A malformed model answer can never be partially accepted: the domain validates the
  whole payload or the call fails.
- The model-reported confidence is carried separately from evidence confidence, so a
  confident tone cannot pass for a well-sourced claim.

### Negative

- Two layers exist between a use case and the model. The indirection pays for itself
  the first time a second provider is configured.
- The JSON envelope costs tokens and can be truncated by a long answer. Mitigated by
  per-call `max_tokens` and by small response models.
- `FakeLLMClient` must be kept in step with `LLMRequest`, so an interface change
  breaks the fake. That is a feature: the fake is the first consumer of the port.

### Neutral

- `infrastructure/llm/` imports LangChain eagerly in its adapter module. The domain
  and application layers do not, so importing them stays cheap.

## Operational impact

Provider errors become `ModelUnavailableError`; malformed output becomes
`StructuredOutputError`. Both are `ApplicationError` subclasses, so the MCP layer
converts them to actionable messages while masking anything unexpected.

## Security impact

No credential crosses the MCP boundary: the API key is a `SecretStr` in settings and
is unwrapped only inside the adapter. Retrieved content reaches the model only
through `UntrustedContentGuard`, never by interpolation into an instruction slot.

## Future impact

Per-provider effort accounting is available already (`prompt_tokens`,
`completion_tokens` on `LLMResponse`). Caching and streaming are not implemented and
need a decision about whether they belong in the port or the adapter.

## How to validate this decision

`tests/integration/test_research_pipeline.py` drives a full design through the fake
provider and asserts the evidence block reached the prompt.
