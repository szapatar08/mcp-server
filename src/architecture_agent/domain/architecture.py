"""Core software architecture vocabulary.

These models describe an architecture as structured data. Rendering them into
prose, Mermaid or PlantUML is the presentation layer's job, which keeps the
domain free of presentation concerns.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from architecture_agent.domain.research import utc_now


class ArchitectureStyle(StrEnum):
    """Recognisable architectural styles."""

    MONOLITH = "monolith"
    MODULAR_MONOLITH = "modular_monolith"
    LAYERED = "layered"
    CLIENT_SERVER = "client_server"
    MICROSERVICES = "microservices"
    EVENT_DRIVEN = "event_driven"
    SERVERLESS = "serverless"
    CQRS = "cqrs"
    PEER_TO_PEER = "peer_to_peer"
    UNKNOWN = "unknown"


class ComponentKind(StrEnum):
    """The role a component plays in the system."""

    CLIENT = "client"
    EDGE = "edge"
    CDN = "cdn"
    LOAD_BALANCER = "load_balancer"
    REVERSE_PROXY = "reverse_proxy"
    GATEWAY = "gateway"
    API = "api"
    SERVICE = "service"
    WORKER = "worker"
    SCHEDULER = "scheduler"
    CACHE = "cache"
    QUEUE = "queue"
    DATABASE = "database"
    OBJECT_STORE = "object_store"
    IDENTITY_PROVIDER = "identity_provider"
    EXTERNAL = "external"


class ExternalDependencyRisk(StrEnum):
    """Risk attached to depending on a third party."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Assumption(BaseModel):
    """An explicit premise a recommendation depends on.

    Assumptions are never hidden: FR-033 requires that every architectural
    recommendation states what it assumed and what remains unknown.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    statement: str = Field(min_length=3, max_length=512)
    basis: str | None = Field(default=None, max_length=512)
    confidence: str = Field(default="stated", min_length=3, max_length=32)
    impact_if_wrong: str | None = Field(default=None, max_length=512)

    @field_validator("confidence")
    @classmethod
    def _normalize_confidence(cls, value: str) -> str:
        allowed = {"stated", "inferred", "unknown"}
        if value not in allowed:
            msg = f"confidence must be one of {sorted(allowed)}"
            raise ValueError(msg)
        return value


class TradeOff(BaseModel):
    """A decision together with the alternatives it displaced."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    decision: str = Field(min_length=3, max_length=512)
    rationale: str = Field(min_length=3, max_length=2_000)
    alternatives_considered: tuple[str, ...] = ()
    advantages: tuple[str, ...] = ()
    disadvantages: tuple[str, ...] = ()
    operational_impact: str | None = Field(default=None, max_length=1_000)
    cost_impact: str | None = Field(default=None, max_length=1_000)
    security_impact: str | None = Field(default=None, max_length=1_000)
    future_impact: str | None = Field(default=None, max_length=1_000)
    reversibility: str = Field(default="medium", min_length=3, max_length=32)


class SystemComponent(BaseModel):
    """One deployable or logically distinct part of a system."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    kind: ComponentKind
    responsibility: str = Field(min_length=3, max_length=1_000)
    technology: tuple[str, ...] = ()
    protocols: tuple[str, ...] = ()
    scaling_model: str = Field(default="unknown", min_length=3, max_length=64)
    is_external: bool = False
    external_risk: ExternalDependencyRisk = ExternalDependencyRisk.NONE

    @model_validator(mode="after")
    def _validate_external_risk(self) -> Self:
        if self.is_external and self.external_risk is ExternalDependencyRisk.NONE:
            msg = f"component {self.name!r} is external and must declare an external_risk"
            raise ValueError(msg)
        if not self.is_external and self.external_risk is not ExternalDependencyRisk.NONE:
            msg = f"component {self.name!r} is internal and cannot declare external_risk"
            raise ValueError(msg)
        return self


class Interaction(BaseModel):
    """A directed communication channel between two components."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    protocol: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=3, max_length=1_000)
    synchronous: bool = True
    data_classification: str = Field(default="internal", min_length=3, max_length=32)


class ExternalDependency(BaseModel):
    """A third-party service the architecture cannot control."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=2, max_length=64)
    risk: ExternalDependencyRisk = ExternalDependencyRisk.MEDIUM
    sla_known: bool = False
    data_shared: tuple[str, ...] = ()
    fallback: str | None = Field(default=None, max_length=1_000)

    @field_validator("risk")
    @classmethod
    def _normalize_risk(cls, value: ExternalDependencyRisk) -> ExternalDependencyRisk:
        return value


class Architecture(BaseModel):
    """A software architecture expressed as structured data."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    style: ArchitectureStyle = ArchitectureStyle.UNKNOWN
    summary: str = Field(min_length=3, max_length=4_000)
    components: tuple[SystemComponent, ...] = Field(min_length=1)
    interactions: tuple[Interaction, ...] = ()
    external_dependencies: tuple[ExternalDependency, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    trade_offs: tuple[TradeOff, ...] = ()

    @field_validator("components")
    @classmethod
    def _unique_component_names(
        cls, value: tuple[SystemComponent, ...]
    ) -> tuple[SystemComponent, ...]:
        names = [component.name for component in value]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            msg = f"duplicate component names: {sorted(duplicates)}"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_interaction_endpoints(self) -> Self:
        known = {component.name for component in self.components}
        unknown: set[str] = set()
        for interaction in self.interactions:
            for endpoint in (interaction.source, interaction.target):
                if endpoint not in known:
                    unknown.add(endpoint)
        if unknown:
            msg = f"interactions reference unknown components: {sorted(unknown)}"
            raise ValueError(msg)
        return self

    def component(self, name: str) -> SystemComponent:
        """Return the component named ``name``."""
        for candidate in self.components:
            if candidate.name == name:
                return candidate
        msg = f"unknown component: {name!r}"
        raise KeyError(msg)

    def components_of_kind(self, kind: ComponentKind) -> tuple[SystemComponent, ...]:
        """Return every component matching ``kind``."""
        return tuple(component for component in self.components if component.kind is kind)


class AdrStatus(StrEnum):
    """Lifecycle status of an architecture decision record (FR-016)."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class ArchitectureDecisionRecord(BaseModel):
    """A numbered, immutable record of an architectural decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=3, max_length=32, pattern=r"^ADR-\d{3,}$")
    title: str = Field(min_length=5, max_length=256)
    status: AdrStatus = AdrStatus.PROPOSED
    context: str = Field(min_length=20, max_length=8_000)
    decision: str = Field(min_length=20, max_length=8_000)
    alternatives: tuple[str, ...] = Field(min_length=1)
    consequences: tuple[str, ...] = Field(min_length=1)
    trade_offs: tuple[TradeOff, ...] = ()
    risks: tuple[str, ...] = ()
    supersedes: str | None = None
    decided_at: datetime | None = None
    recorded_at: datetime = Field(default_factory=utc_now)

    @field_validator("decided_at", "recorded_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            msg = "ADR timestamps must be timezone-aware"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _supersession(self) -> Self:
        if self.status is AdrStatus.SUPERSEDED and not self.supersedes:
            msg = "a superseded ADR must name the ADR that replaces it"
            raise ValueError(msg)
        if self.supersedes == self.id:
            msg = "an ADR cannot supersede itself"
            raise ValueError(msg)
        return self

    @property
    def reference(self) -> str:
        """Human readable citation, e.g. ``ADR-001: Title``."""
        return f"{self.id}: {self.title}"
