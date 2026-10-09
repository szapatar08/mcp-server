"""Cost model invariants.

Cloud prices are dynamic, so the model must make it impossible to state an
estimate without its assumptions, bounds and provenance.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from architecture_agent.domain.cloud import CloudProvider
from architecture_agent.domain.cost import (
    CostCategory,
    CostComponent,
    CostEstimate,
    Currency,
    PricingReference,
)
from architecture_agent.domain.research import ConfidenceLevel, SourceTier, utc_now

pytestmark = pytest.mark.unit

ComponentFactory = Callable[..., CostComponent]


def _reference(**overrides: object) -> PricingReference:
    base: dict[str, object] = {
        "provider": CloudProvider.AWS,
        "service": "EC2",
        "region": "us-east-1",
        "unit": "vCPU-hour",
        "unit_price": Decimal("0.0416"),
        "tier": SourceTier.OFFICIAL_DOCUMENTATION,
    }
    base.update(overrides)
    return PricingReference(**base)


class TestCostComponent:
    """Line items derive their totals instead of trusting the caller."""

    def test_monthly_total_is_derived_from_quantity_and_price(
        self, cost_component_factory: ComponentFactory
    ) -> None:
        component = cost_component_factory(quantity=3, unit_price=Decimal("0.0416"))

        assert component.computed_monthly_total == Decimal("0.12")
        assert component.resolve_monthly_total() == Decimal("0.12")

    def test_an_explicit_total_must_match_the_arithmetic(
        self, cost_component_factory: ComponentFactory
    ) -> None:
        with pytest.raises(ValidationError, match="does not match"):
            cost_component_factory(
                quantity=3, unit_price=Decimal("0.0416"), monthly_total=Decimal("9.99")
            )

    def test_a_matching_explicit_total_is_accepted(
        self, cost_component_factory: ComponentFactory
    ) -> None:
        component = cost_component_factory(
            quantity=3, unit_price=Decimal("0.0416"), monthly_total=Decimal("0.12")
        )

        assert component.monthly_total == Decimal("0.12")

    def test_zero_quantity_is_rejected(self, cost_component_factory: ComponentFactory) -> None:
        with pytest.raises(ValidationError):
            cost_component_factory(quantity=0)

    def test_negative_prices_are_rejected(self, cost_component_factory: ComponentFactory) -> None:
        with pytest.raises(ValidationError):
            cost_component_factory(unit_price=Decimal("-1"))

    def test_pricing_reference_must_describe_the_same_resource(
        self, cost_component_factory: ComponentFactory
    ) -> None:
        with pytest.raises(ValidationError, match="same provider and service"):
            cost_component_factory(pricing_reference=_reference(service="Lambda"))

    def test_pricing_reference_region_must_match(
        self, cost_component_factory: ComponentFactory
    ) -> None:
        with pytest.raises(ValidationError, match="region must match"):
            cost_component_factory(pricing_reference=_reference(region="eu-west-1"))

    def test_pricing_reference_currency_must_match(
        self, cost_component_factory: ComponentFactory
    ) -> None:
        with pytest.raises(ValidationError, match="currency must match"):
            cost_component_factory(
                pricing_reference=_reference(currency=Currency.EUR), currency=Currency.USD
            )

    def test_pricing_reference_timestamps_must_be_timezone_aware(self) -> None:
        from datetime import datetime

        with pytest.raises(ValidationError, match="timezone-aware"):
            _reference(retrieved_at=datetime(2026, 1, 1, 0, 0))  # noqa: DTZ001


class TestCostEstimate:
    """An estimate is only meaningful with assumptions and bounds."""

    @pytest.fixture
    def components(self, cost_component_factory: ComponentFactory) -> tuple[CostComponent, ...]:
        return (
            cost_component_factory(name="compute", quantity=3, unit_price=Decimal("0.0416")),
            cost_component_factory(
                name="storage",
                quantity=100,
                unit_price=Decimal("0.023"),
                category=CostCategory.STORAGE,
            ),
        )

    def test_totals_are_computed_and_annualised(
        self, components: tuple[CostComponent, ...]
    ) -> None:
        estimate = CostEstimate(
            title="Baseline AWS footprint for the checkout service",
            currency=Currency.USD,
            components=components,
            assumptions=("On-demand pricing in us-east-1 as retrieved today.",),
        )

        assert estimate.monthly_total() == Decimal("2.42")
        assert estimate.annual_total() == Decimal("29.04")
        assert estimate.totals_by_category()["storage"] == Decimal("2.30")

    def test_bounds_collapse_onto_the_point_estimate_when_not_modelled(
        self, components: tuple[CostComponent, ...]
    ) -> None:
        estimate = CostEstimate(
            title="Baseline AWS footprint without a sensitivity analysis",
            currency=Currency.USD,
            components=components,
            assumptions=("On-demand pricing as retrieved today.",),
        )

        low, high = estimate.bounds()

        assert low == high == estimate.monthly_total()

    def test_explicit_bounds_must_bracket_the_point_estimate(
        self, components: tuple[CostComponent, ...]
    ) -> None:
        with pytest.raises(ValidationError, match="low_estimate must not exceed"):
            CostEstimate(
                title="Bounds that exclude the point estimate are misleading",
                currency=Currency.USD,
                components=components,
                assumptions=("On-demand pricing as retrieved today.",),
                low_estimate=Decimal("99"),
                high_estimate=Decimal("199"),
            )

        with pytest.raises(ValidationError, match="must not be below the point estimate"):
            CostEstimate(
                title="An upper bound below the point estimate is misleading too",
                currency=Currency.USD,
                components=components,
                assumptions=("On-demand pricing as retrieved today.",),
                high_estimate=Decimal("0.01"),
            )

    def test_low_and_high_bounds_must_be_ordered(
        self, components: tuple[CostComponent, ...]
    ) -> None:
        with pytest.raises(ValidationError, match="low_estimate must not exceed high_estimate"):
            CostEstimate(
                title="Bounds declared in the wrong order are contradictory",
                currency=Currency.USD,
                components=components,
                assumptions=("On-demand pricing as retrieved today.",),
                low_estimate=Decimal("2.00"),
                high_estimate=Decimal("1.00"),
            )

    def test_generated_at_must_be_timezone_aware(
        self, components: tuple[CostComponent, ...]
    ) -> None:
        from datetime import datetime

        with pytest.raises(ValidationError, match="timezone-aware"):
            CostEstimate(
                title="An estimate with an ambiguous generation time cannot expire",
                currency=Currency.USD,
                components=components,
                assumptions=("On-demand pricing as retrieved today.",),
                generated_at=datetime(2026, 1, 1),  # noqa: DTZ001
            )

    def test_staleness_comparison_requires_aware_datetimes(
        self, components: tuple[CostComponent, ...]
    ) -> None:
        from datetime import datetime

        estimate = CostEstimate(
            title="An estimate compared against an ambiguous reference time",
            currency=Currency.USD,
            components=components,
            assumptions=("On-demand pricing as retrieved today.",),
        )

        with pytest.raises(ValueError, match="timezone-aware"):
            estimate.is_stale(now=datetime(2026, 1, 1))  # noqa: DTZ001

    def test_assumptions_are_mandatory(self, components: tuple[CostComponent, ...]) -> None:
        with pytest.raises(ValidationError):
            CostEstimate(  # type: ignore[call-arg]
                title="An estimate with no assumptions stated is not usable",
                currency=Currency.USD,
                components=components,
            )

    def test_components_must_share_the_estimate_currency(
        self, cost_component_factory: ComponentFactory
    ) -> None:
        with pytest.raises(ValidationError, match="single currency"):
            CostEstimate(
                title="A mixed currency estimate cannot be totalled meaningfully",
                currency=Currency.USD,
                components=(
                    cost_component_factory(name="usd", currency=Currency.USD),
                    cost_component_factory(name="eur", currency=Currency.EUR),
                ),
                assumptions=("Mixed currency line items.",),
            )

    def test_high_confidence_requires_pricing_provenance(
        self, components: tuple[CostComponent, ...]
    ) -> None:
        with pytest.raises(ValidationError, match="require at least one pricing reference"):
            CostEstimate(
                title="A confident estimate with no cited pricing is unfounded",
                currency=Currency.USD,
                components=components,
                assumptions=("On-demand pricing as retrieved today.",),
                confidence=ConfidenceLevel.HIGH,
            )

        estimate = CostEstimate(
            title="A confident estimate backed by an official pricing page",
            currency=Currency.USD,
            components=components,
            assumptions=("On-demand pricing as retrieved today.",),
            confidence=ConfidenceLevel.HIGH,
            pricing_references=(_reference(),),
        )

        assert estimate.confidence is ConfidenceLevel.HIGH

    def test_estimates_expire(self, components: tuple[CostComponent, ...]) -> None:
        estimate = CostEstimate(
            title="An estimate that becomes stale as prices move",
            currency=Currency.USD,
            components=components,
            assumptions=("On-demand pricing as retrieved today.",),
            validity_days=7,
            generated_at=utc_now() - timedelta(days=8),
        )

        assert estimate.is_stale() is True
        assert (
            CostEstimate(
                title="A freshly generated estimate that is still current",
                currency=Currency.USD,
                components=components,
                assumptions=("On-demand pricing as retrieved today.",),
                generated_at=utc_now() - timedelta(days=1),
            ).is_stale()
            is False
        )

    def test_at_least_one_line_item_is_required(self) -> None:
        with pytest.raises(ValidationError):
            CostEstimate(  # type: ignore[call-arg]
                title="An estimate with no line items conveys nothing at all",
                currency=Currency.USD,
                assumptions=("No resources modelled.",),
            )
