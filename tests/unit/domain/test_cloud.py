"""Cloud architecture and comparison invariants (FR-005, FR-006)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from architecture_agent.domain.cloud import (
    CloudArchitecture,
    CloudProvider,
    CloudService,
    CloudServiceCategory,
    ComparisonCriterion,
    ServiceComparison,
)

pytestmark = pytest.mark.unit

ServiceFactory = Callable[..., CloudService]


class TestCloudArchitecture:
    """A cloud architecture must be internally provider-consistent."""

    def test_a_single_region_aws_architecture(self, cloud_service_factory: ServiceFactory) -> None:
        architecture = CloudArchitecture(
            provider=CloudProvider.AWS,
            regions=("us-east-1",),
            services=(
                cloud_service_factory("ECS", category=CloudServiceCategory.CONTAINER),
                cloud_service_factory("RDS", category=CloudServiceCategory.DATABASE),
            ),
        )

        assert len(architecture.services_in_category(CloudServiceCategory.DATABASE)) == 1

    def test_services_from_another_provider_are_rejected(
        self, cloud_service_factory: ServiceFactory
    ) -> None:
        with pytest.raises(ValidationError, match="do not belong to aws"):
            CloudArchitecture(
                provider=CloudProvider.AWS,
                regions=("us-east-1",),
                services=(
                    cloud_service_factory("ECS", category=CloudServiceCategory.CONTAINER),
                    cloud_service_factory(
                        "AKS", provider=CloudProvider.AZURE, category=CloudServiceCategory.CONTAINER
                    ),
                ),
            )

    def test_multi_cloud_may_mix_providers(self, cloud_service_factory: ServiceFactory) -> None:
        architecture = CloudArchitecture(
            provider=CloudProvider.MULTI_CLOUD,
            regions=("us-east-1", "westeurope"),
            services=(
                cloud_service_factory("ECS", provider=CloudProvider.AWS),
                cloud_service_factory("AKS", provider=CloudProvider.AZURE),
            ),
            multi_region=True,
        )

        assert len(architecture.services) == 2

    def test_multi_region_needs_at_least_two_regions(
        self, cloud_service_factory: ServiceFactory
    ) -> None:
        with pytest.raises(ValidationError, match="at least two regions"):
            CloudArchitecture(
                provider=CloudProvider.AWS,
                regions=("us-east-1",),
                services=(cloud_service_factory("ECS"),),
                multi_region=True,
            )

    def test_duplicate_regions_are_rejected(self, cloud_service_factory: ServiceFactory) -> None:
        with pytest.raises(ValidationError, match="regions must be unique"):
            CloudArchitecture(
                provider=CloudProvider.AWS,
                regions=("us-east-1", "us-east-1"),
                services=(cloud_service_factory("ECS"),),
            )


class TestServiceComparison:
    """FR-006: a comparison must decide, and justify the decision."""

    @pytest.fixture
    def services(self, cloud_service_factory: ServiceFactory) -> tuple[CloudService, CloudService]:
        return (
            cloud_service_factory("ECS"),
            cloud_service_factory(
                "EKS",
                summary="Managed Kubernetes control plane with worker node pools.",
            ),
        )

    def test_a_complete_comparison(self, services: tuple[CloudService, CloudService]) -> None:
        comparison = ServiceComparison(
            question="Should we run containers on ECS or EKS for this workload?",
            services=services,
            criteria=(
                ComparisonCriterion.OPERATIONAL_OVERHEAD,
                ComparisonCriterion.COST,
                ComparisonCriterion.TEAM_EXPERTISE,
                ComparisonCriterion.VENDOR_LOCK_IN,
            ),
            recommendation="Use ECS Fargate for the first release.",
            recommendation_rationale=(
                "The team has no Kubernetes experience and the workload does not "
                "need custom scheduling, so ECS removes an entire operational layer."
            ),
            situational_exceptions=("Reconsider once we need GPU scheduling or multi-cluster.",),
        )

        assert len(comparison.criteria) == 4

    def test_at_least_two_services_and_two_criteria_are_required(
        self, services: tuple[CloudService, CloudService]
    ) -> None:
        with pytest.raises(ValidationError):
            ServiceComparison(
                question="A question that needs at least two services to compare",
                services=services[:1],
                criteria=(ComparisonCriterion.COST,),
                recommendation="Something.",
                recommendation_rationale="A rationale long enough to pass validation.",
            )

    def test_comparing_a_service_with_itself_is_meaningless(
        self, cloud_service_factory: ServiceFactory
    ) -> None:
        service = cloud_service_factory("ECS")
        with pytest.raises(ValidationError, match="distinct services"):
            ServiceComparison(
                question="Is ECS better than ECS for this particular workload?",
                services=(service, service),
                criteria=(ComparisonCriterion.COST, ComparisonCriterion.COMPLEXITY),
                recommendation="Something.",
                recommendation_rationale="A rationale long enough to pass validation.",
            )
