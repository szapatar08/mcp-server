# ADR-004: Cost models cannot express a guaranteed price

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** Architecture Agent Team

## Context

FR-007 requires a currency, period, assumptions, estimated cost, data source,
source date and confidence for every cost figure, and states: "Never present an
estimated price as a guaranteed price." FR-008 adds minimum, expected and
maximum/peak figures plus cost drivers and optimisation opportunities.

The engineering problem is that none of this is enforced by a dataclass. A model
with a single `monthly_cost: float` will happily hold `Decimal("1234.56")` with no
region, no source and no assumptions, and it will serialise into an MCP response
looking exactly as authoritative as one that did the work.

There is a second, subtler failure: the caller computing the total. If the caller
supplies `monthly_total`, the model has two numbers that can disagree, and the
disagreement surfaces as a wrong invoice.

## Decision

Encode the prohibition in the type system.

1. **`CostComponent.monthly_total` is optional and derived.** When supplied it is
   validated against `quantity * unit_price` within one cent; when omitted it is
   computed. A caller cannot state a total that contradicts its own arithmetic.
2. **All line items in an estimate share one currency**, enforced at both the field
   and the model level. A mixed-currency estimate is a conversion table pretending
   to be a total, so it is rejected rather than silently summed.
3. **`assumptions` is `min_length=1`.** An estimate with no assumptions cannot be
   constructed.
4. **`confidence=HIGH` requires at least one `PricingReference`.** There is no way
   to be confident about a price without citing one.
5. **`PricingReference` carries provider, service, region, unit, tier, optional
   source URL and a timezone-aware `retrieved_at`**, and must describe the same
   provider, service, region and currency as the component it explains.
6. **Bounds are `low_estimate` / `high_estimate`, both optional.** Unset bounds
   collapse onto the point estimate via `bounds()`, because "we did not model the
   sensitivity" is the honest answer. When supplied they must bracket the point
   estimate; a bound that excludes it is rejected.
7. **Every estimate expires.** `validity_days` (default 14) plus `is_stale()` makes
   staleness checkable rather than a matter of memory.
8. **Amounts are `Decimal`, never `float`.** Binary floating point cannot represent
   `0.1`; rounding at line-item level in a currency model is a defect.

`CostCategory` covers all fifteen buckets FR-007 and FR-008 enumerate (compute,
storage, database, networking, data transfer, load balancing, CDN, monitoring,
logging, backups, licensing, third-party), which makes `totals_by_category()`
mechanically complete rather than a free-text summary.

## Alternatives considered

**A single `CostEstimate` with a total and a narrative notes field.** Simplest, and
exactly the failure mode above: nothing stops `notes=""` and `total=1234.56`.
Rejected.

**Return a Markdown report only.** Readable and useless for an agent caller: an MCP
client cannot compute with prose, and cannot verify a number it cannot parse.
Rejected — the model is the contract, and prose is rendered *from* it.

**Compute totals in the application service and pass them in.** Keeps the domain
dumb. Rejected: arithmetic belongs next to the invariants it must satisfy, and
tests must not need a service to check that a total is correct.

**Allow mixed currencies with an FX rate field.** More flexible. Rejected for now:
FX introduces a second dynamic data source with its own timestamp and volatility,
which is a project in itself. Revisit if a user has genuinely multi-currency
footprints; the `Currency` enum already admits the currencies that would be needed.

**`float` for amounts with rounding at the edge.** Rejected outright. Currency
arithmetic in binary floating point produces off-by-a-cent totals that are
impossible to explain and impossible to reconcile against an invoice.

## Consequences

### Positive

- A confident price without a citation cannot be represented, let alone serialised.
- Totals are correct by construction and verifiable from the line items.
- Staleness is a method call, so a caller must make a freshness decision.
- `Decimal` makes the arithmetic auditable end to end.
- Bounds default to the point estimate, so the common case is honest without
  pretending a sensitivity analysis happened.

### Negative

- Constructing a `PricingReference` per component is verbose for a large footprint.
  Mitigated by attaching references to representative components and reusing them
  via `CostEstimate.pricing_references`.
- Optional bounds mean a caller can produce a degenerate `low == high` range. This
  is deliberate: `bounds()` documents that it collapsed, so the reader can tell
  "no sensitivity analysis" from "the analysis found a tight range".
- `Decimal` is not JSON-serialisable by default. Pydantic serialises it as a string
  to preserve precision, which the MCP schemas must document (Milestone 2).

### Neutral

- No FX support. Deliberate; see above.

## Operational impact

`validity_days` is the knob that decides when a cached estimate must be
re-priced. It interacts with `CacheSettings.pricing_ttl_seconds` (default 6 hours):
the cache holds the retrieved *price*, the estimate holds the *arithmetic*, and
they expire on different clocks because they answer different questions.

## Security impact

`PricingReference.source_url` is an `HttpUrl`, so a source citation cannot be
`javascript:` or a file path. Retrieved price text passes through the same
untrusted-content guard as any other retrieved content, so a page cannot smuggle
instructions into a cost prompt.

## Future impact

A per-region cost profile and a "what changed since last estimate" report both
compose on top of `PricingReference` without changing the model.

## How to validate this decision

`tests/unit/domain/test_cost.py` pins: derived totals, rejection of a stated total
that contradicts the arithmetic, single-currency enforcement, mandatory
assumptions, `HIGH` confidence requiring a reference, bounds bracketing the point
estimate, and estimate expiry.
