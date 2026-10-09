"""Cloud cost modelling.

Cloud pricing is dynamic, region specific and never a guarantee. Every model in
this module carries the provenance needed to make that explicit: source tier,
region, unit, retrieval date and confidence.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    computed_field,
    field_validator,
    model_validator,
)

from architecture_agent.domain.cloud import CloudProvider
from architecture_agent.domain.research import ConfidenceLevel, SourceTier, utc_now

_CENTS = Decimal("0.01")


class Currency(StrEnum):
    """Supported estimate currencies."""

    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    BRL = "BRL"
    ARS = "ARS"
    MXN = "MXN"
    COP = "COP"
    CLP = "CLP"
    JPY = "JPY"


class BillingPeriod(StrEnum):
    """Period an amount is expressed in."""

    HOURLY = "hourly"
    MONTHLY = "monthly"
    ANNUAL = "annual"


class CostCategory(StrEnum):
    """Cost buckets used to explain where money goes."""

    COMPUTE = "compute"
    STORAGE = "storage"
    DATABASE = "database"
    NETWORK = "network"
    DATA_TRANSFER = "data_transfer"
    LOAD_BALANCING = "load_balancing"
    CDN = "cdn"
    CACHING = "caching"
    QUEUEING = "queueing"
    MONITORING = "monitoring"
    LOGGING = "logging"
    BACKUP = "backup"
    LICENSING = "licensing"
    THIRD_PARTY = "third_party"
    IDENTITY = "identity"
    DISASTER_RECOVERY = "disaster_recovery"


class BudgetScenario(StrEnum):
    """Scenarios a budget analysis must be able to express (FR-008)."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    HIGH_AVAILABILITY = "high_availability"
    DISASTER_RECOVERY = "disaster_recovery"
    GROWTH = "growth"


class PricingReference(BaseModel):
    """Where a unit price came from and how much to trust it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: CloudProvider | str
    service: str = Field(min_length=2, max_length=128)
    region: str = Field(min_length=2, max_length=64)
    unit: str = Field(min_length=1, max_length=64)
    unit_price: Decimal = Field(ge=Decimal("0"))
    currency: Currency = Currency.USD
    tier: SourceTier = SourceTier.COMMUNITY
    source_url: HttpUrl | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)

    @field_validator("retrieved_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            msg = "retrieved_at must be timezone-aware"
            raise ValueError(msg)
        return value

    @field_validator("unit_price")
    @classmethod
    def _quantize(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


class CostComponent(BaseModel):
    """A single line item in a cost estimate."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=128)
    category: CostCategory
    provider: CloudProvider | str
    service: str = Field(min_length=2, max_length=128)
    region: str = Field(min_length=2, max_length=64)
    quantity: Decimal = Field(gt=Decimal("0"))
    unit: str = Field(min_length=1, max_length=64)
    unit_price: Decimal = Field(ge=Decimal("0"))
    currency: Currency = Currency.USD
    monthly_total: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    pricing_reference: PricingReference | None = None
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    notes: str | None = Field(default=None, max_length=1_000)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def computed_monthly_total(self) -> Decimal:
        """``quantity * unit_price`` recomputed for verification."""
        return (self.quantity * self.unit_price).quantize(_CENTS, rounding=ROUND_HALF_UP)

    @model_validator(mode="after")
    def _validate_totals(self) -> Self:
        if self.pricing_reference is not None:
            reference = self.pricing_reference
            if reference.currency is not self.currency:
                msg = "pricing_reference currency must match the component currency"
                raise ValueError(msg)
            if reference.provider != self.provider or reference.service != self.service:
                msg = "pricing_reference must describe the same provider and service"
                raise ValueError(msg)
            if reference.region != self.region:
                msg = "pricing_reference region must match the component region"
                raise ValueError(msg)
        if self.monthly_total != Decimal("0"):
            expected = self.computed_monthly_total
            if abs(self.monthly_total - expected) > Decimal("0.01"):
                msg = (
                    f"monthly_total {self.monthly_total} does not match "
                    f"quantity * unit_price ({expected})"
                )
                raise ValueError(msg)
        return self

    def resolve_monthly_total(self) -> Decimal:
        """Return the authoritative monthly total, deriving it when unset."""
        return (
            self.monthly_total
            if self.monthly_total != Decimal("0")
            else self.computed_monthly_total
        )


class CostEstimate(BaseModel):
    """An estimate with explicit assumptions, bounds and provenance.

    This model deliberately cannot express a guaranteed price: ``confidence``,
    ``assumptions`` and ``pricing_references`` are required, and every component
    is flagged ``is_estimate``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    title: str = Field(min_length=3, max_length=256)
    currency: Currency
    billing_period: BillingPeriod = BillingPeriod.MONTHLY
    components: tuple[CostComponent, ...] = Field(min_length=1)
    assumptions: tuple[str, ...] = Field(min_length=1)
    confidence: ConfidenceLevel = ConfidenceLevel.LOW
    low_estimate: Decimal | None = Field(default=None, ge=Decimal("0"))
    high_estimate: Decimal | None = Field(default=None, ge=Decimal("0"))
    generated_at: datetime = Field(default_factory=utc_now)
    validity_days: int = Field(default=14, ge=1, le=365)
    cost_drivers: tuple[str, ...] = ()
    optimization_opportunities: tuple[str, ...] = ()
    pricing_references: tuple[PricingReference, ...] = ()
    notes: str | None = Field(default=None, max_length=4_000)

    @field_validator("generated_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            msg = "generated_at must be timezone-aware"
            raise ValueError(msg)
        return value

    @field_validator("components")
    @classmethod
    def _single_currency(cls, value: tuple[CostComponent, ...]) -> tuple[CostComponent, ...]:
        currencies = {component.currency for component in value}
        if len(currencies) > 1:
            msg = "all cost components must share a single currency"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_estimate(self) -> Self:
        mismatched = [
            component.name
            for component in self.components
            if component.currency is not self.currency
        ]
        if mismatched:
            msg = f"components not in {self.currency}: {mismatched}"
            raise ValueError(msg)

        monthly = self.monthly_total()
        low, high = self.low_estimate, self.high_estimate
        if low is not None and high is not None and low > high:
            msg = "low_estimate must not exceed high_estimate"
            raise ValueError(msg)
        if low is not None and low > monthly:
            msg = "low_estimate must not exceed the point estimate"
            raise ValueError(msg)
        if high is not None and high < monthly:
            msg = "high_estimate must not be below the point estimate"
            raise ValueError(msg)

        if self.confidence is ConfidenceLevel.HIGH and not self.pricing_references:
            msg = "high confidence estimates require at least one pricing reference"
            raise ValueError(msg)
        return self

    def monthly_total(self) -> Decimal:
        """Sum of the monthly line items."""
        return sum(
            (component.resolve_monthly_total() for component in self.components),
            start=Decimal("0"),
        ).quantize(_CENTS, rounding=ROUND_HALF_UP)

    def annual_total(self) -> Decimal:
        """Monthly total projected over twelve months."""
        return (self.monthly_total() * Decimal("12")).quantize(_CENTS, rounding=ROUND_HALF_UP)

    def bounds(self) -> tuple[Decimal, Decimal]:
        """Expected minimum and maximum monthly cost.

        Unset bounds collapse onto the point estimate, which is the honest
        answer when no sensitivity analysis was performed.
        """
        monthly = self.monthly_total()
        return (
            self.low_estimate if self.low_estimate is not None else monthly,
            self.high_estimate if self.high_estimate is not None else monthly,
        )

    def totals_by_category(self) -> dict[str, Decimal]:
        """Monthly spend grouped by :class:`CostCategory`."""
        grouped: dict[str, Decimal] = {}
        for component in self.components:
            key = component.category.value
            grouped[key] = grouped.get(key, Decimal("0")) + component.resolve_monthly_total()
        return {
            key: value.quantize(_CENTS, rounding=ROUND_HALF_UP)
            for key, value in sorted(grouped.items())
        }

    def is_stale(self, *, now: datetime | None = None) -> bool:
        """Whether the estimate should be re-priced."""
        reference = now or utc_now()
        if reference.tzinfo is None:
            msg = "staleness comparison requires a timezone-aware datetime"
            raise ValueError(msg)
        return reference - self.generated_at > timedelta(days=self.validity_days)
