"""Domain invariants: validation that encodes architectural safety rules."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from architecture_agent.domain.architecture import (
    AdrStatus,
    Architecture,
    ArchitectureDecisionRecord,
    ArchitectureStyle,
    Assumption,
    ComponentKind,
    ExternalDependencyRisk,
    Interaction,
    SystemComponent,
    TradeOff,
)
from architecture_agent.domain.research import utc_now

pytestmark = pytest.mark.unit


class TestAssumptions:
    """FR-033: assumptions must be explicit and their basis traceable."""

    def test_a_valid_assumption_is_accepted(self) -> None:
        assumption = Assumption(
            statement="Peak load is 1000 requests per second.",
            basis="Derived from the marketing traffic forecast.",
            confidence="inferred",
            impact_if_wrong="Compute sizing and cost estimate both change.",
        )

        assert assumption.confidence == "inferred"

    @pytest.mark.parametrize("confidence", ["guessed", "probably", ""])
    def test_confidence_uses_a_controlled_vocabulary(self, confidence: str) -> None:
        with pytest.raises(ValidationError):
            Assumption(statement="Traffic will grow.", confidence=confidence)


class TestTradeOff:
    """FR-034: a decision must be justified and its impact stated."""

    def test_a_full_trade_off_record(self) -> None:
        trade_off = TradeOff(
            decision="Adopt a modular monolith for the first release.",
            rationale="One deployable keeps the transaction boundary simple at this team size.",
            alternatives_considered=("Microservices", "Event sourced services"),
            advantages=("Single transaction boundary",),
            disadvantages=("Module boundaries erode without enforcement",),
            operational_impact="One deployment pipeline to maintain.",
            cost_impact="Roughly a third of the cost of an equivalent service mesh.",
            security_impact="Smaller attack surface; no inter-service auth to get wrong.",
            future_impact="Modules can be extracted later behind the existing interfaces.",
            reversibility="high",
        )

        assert trade_off.alternatives_considered


class TestSystemComponent:
    """External dependencies must declare their risk (FR-004)."""

    def test_internal_components_cannot_declare_external_risk(
        self, component_factory: Callable[..., SystemComponent]
    ) -> None:
        with pytest.raises(ValidationError, match="internal and cannot declare external_risk"):
            component_factory(
                kind=ComponentKind.SERVICE,
                external_risk=ExternalDependencyRisk.HIGH,
            )

    def test_external_components_must_declare_a_risk(
        self, component_factory: Callable[..., SystemComponent]
    ) -> None:
        with pytest.raises(ValidationError, match="must declare an external_risk"):
            component_factory(kind=ComponentKind.EXTERNAL, is_external=True)

        component = component_factory(
            kind=ComponentKind.EXTERNAL,
            is_external=True,
            external_risk=ExternalDependencyRisk.CRITICAL,
        )

        assert component.external_risk is ExternalDependencyRisk.CRITICAL

    def test_a_responsibility_is_required(self) -> None:
        with pytest.raises(ValidationError):
            SystemComponent(name="api", kind=ComponentKind.API, responsibility="x")


class TestArchitecture:
    """The architecture graph must be internally consistent."""

    @pytest.fixture
    def architecture(self, component_factory: Callable[..., SystemComponent]) -> Architecture:
        return Architecture(
            name="Checkout platform",
            style=ArchitectureStyle.MODULAR_MONOLITH,
            summary="A modular monolith serving checkout for the storefront.",
            components=(
                component_factory("edge", kind=ComponentKind.CDN),
                component_factory("api", kind=ComponentKind.API),
                component_factory("db", kind=ComponentKind.DATABASE),
            ),
            interactions=(
                Interaction(
                    source="api",
                    target="db",
                    protocol="postgresql",
                    description="Reads and writes order rows.",
                ),
            ),
        )

    def test_components_and_interactions_are_resolvable(self, architecture: Architecture) -> None:
        assert architecture.component("api").kind is ComponentKind.API
        assert architecture.components_of_kind(ComponentKind.DATABASE)[0].name == "db"

        with pytest.raises(KeyError):
            architecture.component("missing")

    def test_duplicate_component_names_are_rejected(
        self, component_factory: Callable[..., SystemComponent]
    ) -> None:
        with pytest.raises(ValidationError, match="duplicate component names"):
            Architecture(
                name="Duplicated",
                summary="Two components share a name, which breaks diagram rendering.",
                components=(
                    component_factory("api", kind=ComponentKind.API),
                    component_factory("api", kind=ComponentKind.SERVICE),
                ),
            )

    def test_interactions_must_reference_known_components(
        self, component_factory: Callable[..., SystemComponent]
    ) -> None:
        with pytest.raises(ValidationError, match="unknown components"):
            Architecture(
                name="Dangling",
                summary="An interaction references a component that does not exist.",
                components=(component_factory("api", kind=ComponentKind.API),),
                interactions=(
                    Interaction(
                        source="api",
                        target="ghost",
                        protocol="https",
                        description="Calls a service that is not modelled.",
                    ),
                ),
            )

    def test_at_least_one_component_is_required(self) -> None:
        with pytest.raises(ValidationError):
            Architecture(  # type: ignore[call-arg]
                name="Empty", summary="An architecture must model something."
            )


class TestArchitectureDecisionRecord:
    """FR-016 and §53: ADRs are numbered, immutable and self-consistent."""

    def test_a_complete_adr(self) -> None:
        adr = ArchitectureDecisionRecord(
            id="ADR-001",
            title="Adopt FastMCP for the MCP server",
            status=AdrStatus.ACCEPTED,
            context="We need an MCP server with typed tool schemas and multiple transports.",
            decision="Use FastMCP as the only MCP implementation in the codebase.",
            alternatives=("Write the MCP protocol by hand", "Use the low-level SDK directly"),
            consequences=("One dependency to maintain", "Typed tools come for free"),
            decided_at=utc_now(),
        )

        assert adr.reference == "ADR-001: Adopt FastMCP for the MCP server"

    @pytest.mark.parametrize("identifier", ["adr-001", "ADR-1", "001", "ADR-ABC"])
    def test_identifiers_must_use_the_sequential_format(self, identifier: str) -> None:
        with pytest.raises(ValidationError):
            ArchitectureDecisionRecord(
                id=identifier,
                title="Rejected identifier format for an architecture decision record",
                context="A context long enough to satisfy the validation constraint.",
                decision="A decision long enough to satisfy the validation constraint.",
                alternatives=("Something else",),
                consequences=("A consequence",),
            )

    def test_alternatives_and_consequences_are_mandatory(self) -> None:
        with pytest.raises(ValidationError):
            ArchitectureDecisionRecord(  # type: ignore[call-arg]
                id="ADR-002",
                title="A decision record without alternatives is not a decision record",
                context="A context long enough to satisfy the validation constraint.",
                decision="A decision long enough to satisfy the validation constraint.",
            )

    def test_superseded_records_must_name_their_successor(self) -> None:
        with pytest.raises(ValidationError, match="must name the ADR that replaces it"):
            ArchitectureDecisionRecord(
                id="ADR-003",
                title="Superseded without naming the replacing decision record",
                status=AdrStatus.SUPERSEDED,
                context="A context long enough to satisfy the validation constraint.",
                decision="A decision long enough to satisfy the validation constraint.",
                alternatives=("Something else",),
                consequences=("A consequence",),
            )

    def test_a_record_cannot_supersede_itself(self) -> None:
        with pytest.raises(ValidationError, match="cannot supersede itself"):
            ArchitectureDecisionRecord(
                id="ADR-004",
                title="A decision record that supersedes itself is incoherent",
                status=AdrStatus.SUPERSEDED,
                context="A context long enough to satisfy the validation constraint.",
                decision="A decision long enough to satisfy the validation constraint.",
                alternatives=("Something else",),
                consequences=("A consequence",),
                supersedes="ADR-004",
            )

    def test_records_are_immutable(self) -> None:
        adr = ArchitectureDecisionRecord(
            id="ADR-005",
            title="Architecture decision records must not be edited after the fact",
            context="A context long enough to satisfy the validation constraint.",
            decision="A decision long enough to satisfy the validation constraint.",
            alternatives=("Something else",),
            consequences=("A consequence",),
        )

        with pytest.raises(ValidationError):
            adr.status = AdrStatus.REJECTED  # type: ignore[misc]
