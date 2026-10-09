"""Cost engine.

Prices are dynamic, regional and never a contract. This engine therefore refuses
to produce a number that is not traceable to a retrieved price: every
``CostComponent`` must carry a ``PricingReference`` whose provider, service, region
and currency match, and a caller may ask for a figure whose provenance is absent —
in which case the answer is a refusal, not a number.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from architecture_agent.application.errors import InvalidInputError
from architecture_agent.application.llm_service import (
    LLMClient,
    ReasoningOutcome,
    build_structured_request,
)
from architecture_agent.domain.cloud import CloudProvider
from architecture_agent.domain.cost import (
    BudgetScenario,
    CostCategory,
    CostComponent,
    CostEstimate,
    Currency,
    PricingReference,
)
from architecture_agent.domain.research import (
    ConfidenceLevel,
    SourceTier,
    assess_confidence,
    utc_now,
)

_SYSTEM = """\
You are a principal cloud cost engineer producing an engineering estimate.

Rules you must follow without exception:

- Never fabricate a price. Every unit price must come from the supplied price
  evidence, which names the provider, service, region and retrieval date. If a
  price is not in the evidence, omit the line item and record it as a gap. A
  plausible wrong number is worse than a missing one.
- Never present an estimate as a guarantee. An estimate is a figure with stated
  assumptions and a confidence.
- State every assumption about usage: requests, storage growth, data transfer,
  licences, and anything else that moves the total.
- Distinguish on-demand from committed-use and from free tiers.
- Return only the JSON object requested. No prose outside it, no code fences.
"""

_CURRENCY = Currency.USD


class PriceQuote(BaseModel):
    """A price retrieved for one service in one region."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: CloudProvider | str
    service: str = Field(min_length=2, max_length=128)
    region: str = Field(min_length=2, max_length=64)
    unit: str = Field(min_length=1, max_length=64)
    unit_price: Decimal = Field(ge=Decimal("0"))
    currency: Currency = Currency.USD
    tier: SourceTier = SourceTier.COMMUNITY
    source_url: str | None = None
    retrieved_at: str | None = None


class PriceBook(BaseModel):
    """The prices available to an estimate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    quotes: tuple[PriceQuote, ...] = ()
    as_of: str = ""

    def find(self, service: str, region: str | None = None) -> PriceQuote | None:
        """Return the quote for ``service``, optionally region-scoped."""
        for quote in self.quotes:
            if quote.service.lower() != service.lower():
                continue
            if region is not None and quote.region.lower() != region.lower():
                continue
            return quote
        return None

    @property
    def is_empty(self) -> bool:
        """Whether any price is available at all."""
        return not self.quotes

    @property
    def authoritative(self) -> tuple[PriceQuote, ...]:
        """Quotes at official documentation or standard level."""
        return tuple(quote for quote in self.quotes if quote.tier <= SourceTier.OFFICIAL_STANDARD)


class CostDraft(BaseModel):
    """A model-proposed estimate."""

    model_config = ConfigDict(extra="forbid")

    components: tuple[dict[str, Any], ...] = ()
    assumptions: tuple[str, ...] = ()
    cost_drivers: tuple[str, ...] = ()
    optimization_opportunities: tuple[str, ...] = ()
    unpriced_items: tuple[str, ...] = Field(
        default=(),
        description=(
            "Resources the model could not price because no retrieved price covers "
            "them. These are reported rather than estimated."
        ),
    )


class BudgetLine(BaseModel):
    """One line of a budget scenario."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=128)
    scenario: BudgetScenario
    monthly: Decimal = Field(ge=Decimal("0"))
    rationale: str = Field(default="", max_length=1_000)


@dataclass(frozen=True, slots=True)
class CostGap:
    """A resource that could not be priced."""

    resource: str
    reason: str

    def render(self) -> str:
        """Render for a human reader."""
        return f"{self.resource}: {self.reason}"


class CostEngine:
    """Estimate infrastructure cost from retrieved prices, or refuse."""

    def __init__(self, llm: LLMClient, *, max_tokens: int = 6_000) -> None:
        """Build the engine.

        Args:
            llm: The model adapter that maps a design to line items.
            max_tokens: Generation budget per call.
        """
        self._llm = llm
        self._max_tokens = max_tokens

    def estimate(
        self,
        *,
        design: str,
        prices: PriceBook,
        currency: Currency = _CURRENCY,
        usage_assumptions: tuple[str, ...] = (),
        evidence: str = "",
        request_id: str | None = None,
    ) -> tuple[CostEstimate, ReasoningOutcome, tuple[CostGap, ...]]:
        """Estimate the monthly cost of ``design``.

        Every priced component is matched against ``prices``. A component the
        model proposes that no quote covers is dropped and reported as a gap, so
        the returned total can never contain an unsourced figure.

        Raises:
            InvalidInputError: if no price is available at all, or nothing in the
                design could be priced. A total with nothing behind it is worse
                than no answer.
        """
        if prices.is_empty:
            msg = (
                "no retrieved prices are available for this provider, region and date. "
                "Retrieve pricing before estimating; quoting a figure without a source "
                "would be fabrication."
            )
            raise InvalidInputError(msg)

        instruction = [
            "Break the design into billable line items and price each one from the "
            "supplied price book.",
            "For each component give: name, category, provider, service, region, "
            "quantity, unit, and the unit_price taken verbatim from the price book.",
            "Do not invent a unit price. If the price book has no quote for a "
            "resource you identify, list it in unpriced_items instead.",
            "List every usage assumption that moves the total.",
        ]
        if usage_assumptions:
            instruction.append("Caller-supplied usage assumptions: " + "; ".join(usage_assumptions))
        instruction.append(
            f"Available prices as of {prices.as_of or 'an unstated date'}: "
            f"{len(prices.quotes)} quote(s), {len(prices.authoritative)} at official level."
        )

        request = build_structured_request(
            system=_SYSTEM,
            instruction="\n".join(instruction),
            context=(
                f"Design:\n{design}",
                "Price book:\n" + prices.model_dump_json(indent=2),
            ),
            response_model=CostDraft,
            max_tokens=self._max_tokens,
            request_id=request_id,
        ).with_evidence(evidence)

        response = self._llm.complete(request)
        from architecture_agent.application.llm_service import parse_envelope

        envelope = parse_envelope(response.content)
        outcome = ReasoningOutcome(
            payload=envelope.result,
            reasoning_summary=envelope.reasoning_summary,
            assumptions=envelope.assumptions,
            confidence=envelope.confidence,
            model=response.model,
            provider=response.provider,
            degraded=response.degraded,
            degradation_reason=response.degradation_reason,
        )
        draft: CostDraft = outcome.as_model(CostDraft)

        components, gaps = self._reconcile(draft, prices, currency)
        if not components:
            msg = (
                "nothing in the design could be priced from the retrieved price book. "
                f"{len(gaps)} resource(s) had no quote: "
                + "; ".join(gap.render() for gap in gaps[:5])
            )
            raise InvalidInputError(msg)

        estimate = CostEstimate(
            title=f"Infrastructure estimate from {len(components)} priced resource(s)",
            currency=currency,
            components=components,
            assumptions=draft.assumptions
            or (
                "No assumptions were stated by the model; this estimate is incomplete "
                "and should not be used for budgeting.",
            ),
            confidence=min(
                (component.confidence for component in components),
                key=_confidence_rank,
                default=ConfidenceLevel.SPECULATIVE,
            ),
            cost_drivers=draft.cost_drivers,
            optimization_opportunities=draft.optimization_opportunities,
            pricing_references=tuple(
                component.pricing_reference
                for component in components
                if component.pricing_reference is not None
            ),
        )
        gaps = gaps + tuple(
            CostGap(resource=item, reason="no retrieved price covers this resource")
            for item in draft.unpriced_items
        )
        return estimate, outcome, gaps

    def _reconcile(
        self, draft: CostDraft, prices: PriceBook, currency: Currency
    ) -> tuple[tuple[CostComponent, ...], tuple[CostGap, ...]]:
        """Keep only components whose price matches a retrieved quote."""
        components: list[CostComponent] = []
        gaps: list[CostGap] = []

        for index, raw in enumerate(draft.components):
            service = str(raw.get("service", "")).strip()
            region = str(raw.get("region", "")).strip()
            if not service or not region:
                gaps.append(
                    CostGap(
                        resource=str(raw.get("name", f"item-{index}")),
                        reason="service or region missing",
                    )
                )
                continue

            quote = prices.find(service, region)
            if quote is None:
                gaps.append(
                    CostGap(
                        resource=str(raw.get("name", service)),
                        reason=f"no retrieved price for {service} in {region}",
                    )
                )
                continue

            try:
                components.append(
                    CostComponent(
                        name=str(raw.get("name") or f"{service} line item"),
                        category=CostCategory(str(raw.get("category", "compute"))),
                        provider=quote.provider,
                        service=quote.service,
                        region=quote.region,
                        quantity=Decimal(str(raw.get("quantity", 1))),
                        unit=quote.unit,
                        unit_price=quote.unit_price,
                        currency=quote.currency if quote.currency is not currency else currency,
                        pricing_reference=_reference_for(quote),
                        confidence=_quote_confidence(quote),
                    )
                )
            except (ValueError, ArithmeticError) as error:
                gaps.append(
                    CostGap(
                        resource=str(raw.get("name", service)),
                        reason=f"rejected by the cost model: {error}",
                    )
                )

        return tuple(components), tuple(gaps)

    def budget_scenarios(
        self, *, baseline: CostEstimate, scenarios: tuple[BudgetScenario, ...]
    ) -> tuple[BudgetLine, ...]:
        """Project a baseline estimate across scenarios.

        The multipliers are named, not hidden: a scenario is a stated assumption
        about how the footprint changes, and each line records its rationale so a
        reader can disagree with the multiplier rather than with the arithmetic.
        """
        from decimal import ROUND_HALF_UP

        multipliers: dict[BudgetScenario, tuple[Decimal, str]] = {
            BudgetScenario.DEVELOPMENT: (
                Decimal("0.1"),
                "Assumes one non-production environment at a tenth of production size.",
            ),
            BudgetScenario.STAGING: (
                Decimal("0.2"),
                "Assumes staging mirrors production data volume at a fifth of the footprint.",
            ),
            BudgetScenario.PRODUCTION: (
                Decimal("1"),
                "The baseline estimate as modelled.",
            ),
            BudgetScenario.HIGH_AVAILABILITY: (
                Decimal("1.8"),
                "Assumes a standby in a second zone for stateful components: roughly "
                "80% additional spend.",
            ),
            BudgetScenario.DISASTER_RECOVERY: (
                Decimal("1.35"),
                "Assumes continuous backup plus a warm standby: roughly 35% additional "
                "spend, excluding the engineer time to prove it recovers.",
            ),
            BudgetScenario.GROWTH: (
                Decimal("2.5"),
                "Assumes 2.5x traffic and 3x data growth over a year; sub-linear because "
                "fixed costs do not scale.",
            ),
        }

        monthly = baseline.monthly_total()
        lines: list[BudgetLine] = []
        for scenario in scenarios:
            multiplier, rationale = multipliers.get(
                scenario, (Decimal("1"), "No multiplier defined.")
            )
            lines.append(
                BudgetLine(
                    name=f"{scenario.value} ({multiplier}x baseline)",
                    scenario=scenario,
                    monthly=(monthly * multiplier).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    ),
                    rationale=rationale,
                )
            )
        return tuple(lines)


def _reference_for(quote: PriceQuote) -> PricingReference:
    """Build the pricing reference a component must carry."""
    from datetime import datetime

    retrieved = utc_now()
    if quote.retrieved_at:
        try:
            retrieved = datetime.fromisoformat(quote.retrieved_at)
        except ValueError:
            retrieved = utc_now()

    reference = PricingReference(
        provider=quote.provider,
        service=quote.service,
        region=quote.region,
        unit=quote.unit,
        unit_price=quote.unit_price,
        currency=quote.currency,
        tier=quote.tier,
        retrieved_at=retrieved,
    )
    if quote.source_url:
        return reference.model_copy(update={"source_url": quote.source_url})
    return reference


def _quote_confidence(quote: PriceQuote) -> ConfidenceLevel:
    """Confidence implied by a quote's authority and whether it has a URL."""
    return assess_confidence(quote.tier, 1 if quote.source_url else 0)


_CONFIDENCE_ORDER = (
    ConfidenceLevel.HIGH,
    ConfidenceLevel.MEDIUM,
    ConfidenceLevel.LOW,
    ConfidenceLevel.SPECULATIVE,
)


def _confidence_rank(level: ConfidenceLevel) -> int:
    """Lower rank means higher confidence."""
    return _CONFIDENCE_ORDER.index(level)


def price_book_json(quotes: tuple[PriceQuote, ...], as_of: str) -> str:
    """Render a price book for inclusion in a prompt or a document."""
    return json.dumps(PriceBook(quotes=quotes, as_of=as_of).model_dump(mode="json"), indent=2)


__all__ = [
    "BudgetLine",
    "BudgetScenario",
    "CostDraft",
    "CostEngine",
    "CostGap",
    "PriceBook",
    "PriceQuote",
    "price_book_json",
]
