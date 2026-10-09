"""Infrastructure invariants: segmentation, exposure, availability and telemetry."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from architecture_agent.domain.infrastructure import (
    AvailabilityObjective,
    ComputeType,
    ComputeUnit,
    DatabaseEngine,
    DatabaseTopology,
    DatabaseUnit,
    InfrastructurePlan,
    NetworkSegment,
    NetworkTier,
    ObservabilitySignal,
    ObservabilityStack,
    StorageType,
    StorageUnit,
)

pytestmark = pytest.mark.unit


class TestNetworkSegment:
    """Segmentation is only meaningful if it is enforced."""

    def test_a_valid_private_data_segment(self) -> None:
        segment = NetworkSegment(
            name="data-subnet", cidr="10.0.10.0/24", tier=NetworkTier.DATA, public=False
        )

        assert str(segment.network) == "10.0.10.0/24"

    @pytest.mark.parametrize("cidr", ["10.0.0.0/33", "not-a-cidr", "10.0.0.1/24", "10.0.0.0/16"])
    def test_invalid_or_oversized_cidrs_are_rejected(self, cidr: str) -> None:
        with pytest.raises(ValidationError):
            NetworkSegment(name="segment", cidr=cidr, tier=NetworkTier.APPLICATION, public=False)

    @pytest.mark.parametrize("tier", [NetworkTier.DATA, NetworkTier.MANAGEMENT])
    def test_data_and_management_tiers_must_never_be_public(self, tier: NetworkTier) -> None:
        with pytest.raises(ValidationError, match="must never be internet reachable"):
            NetworkSegment(name="segment", cidr="10.0.1.0/24", tier=tier, public=True)

    def test_the_internet_tier_must_be_public(self) -> None:
        with pytest.raises(ValidationError, match="must be modelled as public"):
            NetworkSegment(
                name="segment", cidr="192.0.2.0/24", tier=NetworkTier.INTERNET, public=False
            )


class TestComputeUnit:
    """Sized compute must state its size; HA compute must span zones."""

    def test_a_vm_declares_its_size(self) -> None:
        unit = ComputeUnit(
            name="app-nodes",
            compute_type=ComputeType.VIRTUAL_MACHINE,
            quantity=3,
            vcpu=4,
            memory_gb=16,
        )

        assert unit.vcpu == 4

    @pytest.mark.parametrize(
        "compute_type",
        [
            ComputeType.BARE_METAL,
            ComputeType.VIRTUAL_MACHINE,
            ComputeType.KUBERNETES_NODE,
        ],
    )
    def test_undersized_compute_is_rejected(self, compute_type: ComputeType) -> None:
        with pytest.raises(ValidationError, match="requires vcpu and memory_gb"):
            ComputeUnit(name="nodes", compute_type=compute_type, quantity=1)

    def test_multi_az_requires_more_than_one_unit(self) -> None:
        with pytest.raises(ValidationError, match="at least two units"):
            ComputeUnit(
                name="nodes",
                compute_type=ComputeType.VIRTUAL_MACHINE,
                quantity=1,
                vcpu=2,
                memory_gb=8,
                multi_az=True,
            )

    def test_serverless_compute_needs_no_declared_size(self) -> None:
        unit = ComputeUnit(
            name="functions", compute_type=ComputeType.SERVERLESS_FUNCTION, quantity=1
        )

        assert unit.vcpu is None


class TestDatabaseUnit:
    """FR-049: databases are not exposed to the internet (FR-003)."""

    def test_a_healthy_database_topology(self) -> None:
        unit = DatabaseUnit(
            name="orders",
            engine=DatabaseEngine.POSTGRESQL,
            topology=DatabaseTopology.MULTI_AZ_PRIMARY_REPLICA,
            read_replica=True,
            automated_backups=True,
            point_in_time_recovery=True,
        )

        assert unit.publicly_reachable is False

    def test_public_databases_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="publicly reachable"):
            DatabaseUnit(name="orders", engine=DatabaseEngine.POSTGRESQL, publicly_reachable=True)

    def test_multi_az_implies_a_standby(self) -> None:
        with pytest.raises(ValidationError, match="implies a standby replica"):
            DatabaseUnit(
                name="orders",
                engine=DatabaseEngine.POSTGRESQL,
                topology=DatabaseTopology.MULTI_AZ_PRIMARY_REPLICA,
            )


class TestAvailabilityObjective:
    """RPO must fit inside RTO, and 99.99% must be designed for."""

    def test_a_coherent_objective(self) -> None:
        objective = AvailabilityObjective(
            availability_target=99.95, rto_minutes=30, rpo_minutes=5, multi_az=True
        )

        assert objective.rpo_minutes == 5

    def test_rpo_cannot_exceed_rto(self) -> None:
        with pytest.raises(ValidationError, match="rpo_minutes must not exceed rto_minutes"):
            AvailabilityObjective(availability_target=99.9, rto_minutes=15, rpo_minutes=60)

    @pytest.mark.parametrize("target", [99.99, 99.999, 100.0])
    def test_four_nines_requires_multi_region(self, target: float) -> None:
        with pytest.raises(ValidationError, match="requires multi-region"):
            AvailabilityObjective(availability_target=target, multi_az=True)

    def test_four_nines_is_accepted_with_multi_region(self) -> None:
        assert AvailabilityObjective(availability_target=99.99, multi_region=True)

    @pytest.mark.parametrize("target", [50.0, 99.99999])
    def test_targets_outside_plausible_bounds_are_rejected(self, target: float) -> None:
        with pytest.raises(ValidationError):
            AvailabilityObjective(availability_target=target)


class TestObservabilityStack:
    """FR-014: gaps must be detectable, not just describable."""

    def test_a_complete_stack_has_no_gaps(self) -> None:
        stack = ObservabilityStack(
            logs=True,
            metrics=True,
            traces=True,
            alerting=True,
            health_checks=True,
            dashboards=True,
            slo_defined=True,
            audit_logging=True,
            retention_days=90,
        )

        assert stack.missing_signals() == ()
        assert stack.is_operable is True

    def test_gaps_are_enumerated(self) -> None:
        stack = ObservabilityStack(logs=True, metrics=True)

        assert stack.missing_signals() == (
            ObservabilitySignal.TRACE,
            ObservabilitySignal.ALERT,
            ObservabilitySignal.HEALTH_CHECK,
            ObservabilitySignal.DASHBOARD,
            ObservabilitySignal.SLO,
            ObservabilitySignal.AUDIT,
        )
        assert stack.is_operable is False


class TestInfrastructurePlan:
    """A plan must keep its resource names unambiguous."""

    @pytest.fixture
    def plan(self) -> InfrastructurePlan:
        return InfrastructurePlan(
            name="Two-tier internal platform",
            network=(
                NetworkSegment(
                    name="public", cidr="10.0.0.0/24", tier=NetworkTier.APPLICATION, public=True
                ),
                NetworkSegment(
                    name="data", cidr="10.0.10.0/24", tier=NetworkTier.DATA, public=False
                ),
            ),
            storage=(StorageUnit(name="objects", storage_type=StorageType.OBJECT, size_gb=1_024),),
        )

    def test_segments_are_queryable_by_exposure(self, plan: InfrastructurePlan) -> None:
        assert [segment.name for segment in plan.public_segments()] == ["public"]
        assert [segment.name for segment in plan.segment_for_tier(NetworkTier.DATA)] == ["data"]

    def test_duplicate_resource_names_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate resource names"):
            InfrastructurePlan(
                name="Ambiguous plan",
                storage=(
                    StorageUnit(name="store", storage_type=StorageType.BLOCK, size_gb=10),
                    StorageUnit(name="store", storage_type=StorageType.OBJECT, size_gb=20),
                ),
            )
