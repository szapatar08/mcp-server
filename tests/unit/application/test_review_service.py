"""Review use case: findings, evidence and the refusal to score without evidence."""

from __future__ import annotations

import pytest

from architecture_agent.application.review_service import (
    ArchitectureReviewService,
    ReviewRequest,
    ReviewScope,
    finding_text,
)
from architecture_agent.domain.architecture import Architecture, ComponentKind, SystemComponent
from architecture_agent.domain.deployment import DeploymentPlan
from architecture_agent.domain.infrastructure import InfrastructurePlan
from architecture_agent.domain.security import (
    ArchitectureReview,
    ReviewDimension,
    Severity,
)

pytestmark = pytest.mark.unit


def _ids(review: ArchitectureReview) -> set[str]:
    return {finding.id for finding in review.findings}


class TestEvidenceDiscipline:
    """FR-020: absence of evidence is an evidence gap, not a low score."""

    def test_only_security_is_scored_without_an_infrastructure_plan(
        self, review_service: ArchitectureReviewService, minimal_architecture: Architecture
    ) -> None:
        review = review_service.review(ReviewRequest(architecture=minimal_architecture))

        assert {score.dimension for score in review.scores} == {ReviewDimension.SECURITY}

    def test_missing_material_becomes_an_explicit_gap(
        self, review_service: ArchitectureReviewService, minimal_architecture: Architecture
    ) -> None:
        review = review_service.review(ReviewRequest(architecture=minimal_architecture))

        gaps = " ".join(review.evidence_gaps)
        assert "No infrastructure plan supplied" in gaps
        assert "No deployment plan supplied" in gaps
        assert "No availability objective supplied" in gaps
        assert "No cloud provider supplied" in gaps

    def test_no_traffic_profile_means_scalability_is_never_trusted(
        self,
        review_service: ArchitectureReviewService,
        minimal_architecture: Architecture,
        healthy_infrastructure: InfrastructurePlan,
    ) -> None:
        review = review_service.review(
            ReviewRequest(
                architecture=minimal_architecture,
                infrastructure=healthy_infrastructure,
            )
        )
        scalability = next(
            score for score in review.scores if score.dimension is ReviewDimension.SCALABILITY
        )

        assert scalability.score == 10.0
        assert "not from measured or forecast load" in " ".join(review.evidence_gaps)
        assert "No findings were raised" in scalability.justification

    def test_a_complete_review_reports_no_gaps_about_the_scored_dimensions(
        self,
        review_service: ArchitectureReviewService,
        external_architecture: Architecture,
        healthy_infrastructure: InfrastructurePlan,
        deployment_plan: DeploymentPlan,
    ) -> None:
        review = review_service.review(
            ReviewRequest(
                architecture=external_architecture,
                infrastructure=healthy_infrastructure,
                deployment=deployment_plan,
                availability=healthy_infrastructure.availability,
                cloud_provider="aws",
            )
        )

        assert {score.dimension for score in review.scores} >= {
            ReviewDimension.SECURITY,
            ReviewDimension.RELIABILITY,
            ReviewDimension.SCALABILITY,
            ReviewDimension.OBSERVABILITY,
        }
        remaining = " ".join(review.evidence_gaps)
        assert "No infrastructure plan supplied" not in remaining
        assert "No deployment plan supplied" not in remaining

    def test_findings_are_never_scored_without_citable_evidence(
        self,
        review_service: ArchitectureReviewService,
        minimal_architecture: Architecture,
        weak_infrastructure: InfrastructurePlan,
    ) -> None:
        review = review_service.review(
            ReviewRequest(architecture=minimal_architecture, infrastructure=weak_infrastructure)
        )

        assert review.findings
        for finding in review.findings:
            assert finding.evidence, f"{finding.id} has no evidence"
            assert finding.recommendation, f"{finding.id} has no recommendation"
            assert finding.confidence == "high"


class TestChecks:
    """Each check fires on the evidence that justifies it and stays silent otherwise."""

    def test_a_healthy_plan_raises_nothing(
        self,
        review_service: ArchitectureReviewService,
        minimal_architecture: Architecture,
        healthy_infrastructure: InfrastructurePlan,
    ) -> None:
        review = review_service.review(
            ReviewRequest(
                architecture=minimal_architecture,
                infrastructure=healthy_infrastructure,
                availability=healthy_infrastructure.availability,
            )
        )

        assert review.findings == ()
        assert review.critical_findings == ()

    def test_a_high_risk_dependency_without_a_fallback_is_flagged(
        self,
        review_service: ArchitectureReviewService,
        external_architecture: Architecture,
    ) -> None:
        review = review_service.review(ReviewRequest(architecture=external_architecture))
        finding = next(item for item in review.findings if item.id == "REL-001")

        assert finding.severity is Severity.HIGH
        assert "Stripe Payments" in (finding.evidence or "")
        assert "SendGrid" not in (finding.evidence or "")

    def test_confidential_data_over_cleartext_is_flagged(
        self, review_service: ArchitectureReviewService, minimal_architecture: Architecture
    ) -> None:
        leaky = minimal_architecture.model_copy(
            update={
                "interactions": (
                    minimal_architecture.interactions[0].model_copy(
                        update={"protocol": "http", "data_classification": "restricted"}
                    ),
                    minimal_architecture.interactions[1],
                )
            }
        )
        review = review_service.review(ReviewRequest(architecture=leaky))
        finding = next(item for item in review.findings if item.id == "SEC-001")

        assert finding.severity is Severity.HIGH
        assert "storefront->checkout-api" in (finding.evidence or "")

    def test_cleartext_over_public_data_is_not_flagged(
        self, review_service: ArchitectureReviewService, minimal_architecture: Architecture
    ) -> None:
        leaky = minimal_architecture.model_copy(
            update={
                "interactions": (
                    minimal_architecture.interactions[0].model_copy(
                        update={"protocol": "http", "data_classification": "public"}
                    ),
                    minimal_architecture.interactions[1],
                )
            }
        )

        assert "SEC-001" not in _ids(review_service.review(ReviewRequest(architecture=leaky)))

    def test_an_external_data_store_is_flagged(
        self, review_service: ArchitectureReviewService, minimal_architecture: Architecture
    ) -> None:
        exposed = minimal_architecture.model_copy(
            update={
                "components": (
                    minimal_architecture.components[0],
                    SystemComponent(
                        name="billing-saas",
                        kind=ComponentKind.DATABASE,
                        responsibility="Stores billing records.",
                        is_external=True,
                        external_risk="high",
                    ),
                )
            }
        )
        finding = next(
            item
            for item in review_service.review(ReviewRequest(architecture=exposed)).findings
            if item.id == "SEC-002"
        )

        assert "billing-saas" in (finding.evidence or "")

    def test_a_single_node_database_and_no_backups_are_flagged(
        self,
        review_service: ArchitectureReviewService,
        minimal_architecture: Architecture,
        weak_infrastructure: InfrastructurePlan,
    ) -> None:
        review = review_service.review(
            ReviewRequest(architecture=minimal_architecture, infrastructure=weak_infrastructure)
        )

        assert {"REL-002", "REL-003"} <= _ids(review)
        resilience = next(item for item in review.findings if item.id == "REL-002")
        assert resilience.severity is Severity.MEDIUM
        assert "topology=single" in (resilience.evidence or "")

    @pytest.mark.parametrize(
        ("target", "expected"),
        [(99.9, Severity.MEDIUM), (99.99, Severity.HIGH)],
    )
    def test_a_single_node_database_is_severer_at_higher_targets(
        self,
        review_service: ArchitectureReviewService,
        minimal_architecture: Architecture,
        target: float,
        expected: Severity,
    ) -> None:
        """The same topology failure is weighted against the promise made."""
        from architecture_agent.domain.infrastructure import (
            AvailabilityObjective,
            DatabaseEngine,
            DatabaseUnit,
        )

        plan = InfrastructurePlan(
            name="p",
            databases=(DatabaseUnit(name="orders", engine=DatabaseEngine.POSTGRESQL),),
        )
        review = review_service.review(
            ReviewRequest(
                architecture=minimal_architecture,
                infrastructure=plan,
                availability=AvailabilityObjective(
                    availability_target=target,
                    multi_az=True,
                    multi_region=target >= 99.99,
                    rto_minutes=30,
                    rpo_minutes=5,
                ),
            )
        )
        finding = next(item for item in review.findings if item.id == "REL-002")

        assert finding.severity is expected

    def test_the_domain_refuses_an_extreme_target_without_multi_region(self) -> None:
        """The impossible state is prevented, so there is no check for it.

        A reviewer rule for "99.99% without multi-region" would be unreachable:
        ``AvailabilityObjective`` will not validate. Preventing the state is a
        stronger guarantee than reporting it.
        """
        from pydantic import ValidationError

        from architecture_agent.domain.infrastructure import AvailabilityObjective

        with pytest.raises(ValidationError, match="requires multi-region"):
            AvailabilityObjective(availability_target=99.99, multi_az=True)

    def test_a_missing_recovery_objective_is_flagged(
        self, review_service: ArchitectureReviewService, minimal_architecture: Architecture
    ) -> None:
        from architecture_agent.domain.infrastructure import AvailabilityObjective

        review = review_service.review(
            ReviewRequest(
                architecture=minimal_architecture,
                availability=AvailabilityObjective(availability_target=99.9, multi_az=True),
            )
        )

        assert "REL-005" in _ids(review)

    def test_missing_observability_is_flagged_with_the_signals_listed(
        self,
        review_service: ArchitectureReviewService,
        minimal_architecture: Architecture,
        weak_infrastructure: InfrastructurePlan,
    ) -> None:
        review = review_service.review(
            ReviewRequest(architecture=minimal_architecture, infrastructure=weak_infrastructure)
        )
        finding = next(item for item in review.findings if item.id == "OBS-001")

        assert "trace" in (finding.evidence or "")
        assert "alert" in (finding.evidence or "")

    def test_a_single_fixed_instance_is_flagged_for_scalability(
        self,
        review_service: ArchitectureReviewService,
        minimal_architecture: Architecture,
        weak_infrastructure: InfrastructurePlan,
    ) -> None:
        review = review_service.review(
            ReviewRequest(architecture=minimal_architecture, infrastructure=weak_infrastructure)
        )

        assert "SCAL-001" in _ids(review)

    def test_an_internet_exposure_without_a_load_balancer_is_flagged(
        self, review_service: ArchitectureReviewService, minimal_architecture: Architecture
    ) -> None:
        from architecture_agent.domain.deployment import (
            DeploymentPlan,
            DeploymentStrategy,
            DeploymentTarget,
            ExposureLayer,
            TlsTermination,
        )

        bare = DeploymentPlan(
            name="Bare VM exposure",
            target=DeploymentTarget.VIRTUAL_MACHINE,
            strategy=DeploymentStrategy.RECREATE,
            tls_termination=TlsTermination.AT_REVERSE_PROXY,
            exposure_layers=(ExposureLayer.INTERNET, ExposureLayer.APPLICATION_LAYER),
        )
        review = review_service.review(
            ReviewRequest(architecture=minimal_architecture, deployment=bare)
        )

        assert "SEC-004" in _ids(review)


class TestScoring:
    """Scores are a reproducible penalty sum with a stated justification."""

    def test_a_clean_dimension_scores_ten_and_says_why(
        self,
        review_service: ArchitectureReviewService,
        minimal_architecture: Architecture,
    ) -> None:
        review = review_service.review(ReviewRequest(architecture=minimal_architecture))
        security = review.scores[0]

        assert security.score == 10.0
        assert security.confidence == "medium"
        assert security.blocking_findings == ()

    def test_scores_fall_with_severity_and_never_below_zero(
        self,
        review_service: ArchitectureReviewService,
        minimal_architecture: Architecture,
        weak_infrastructure: InfrastructurePlan,
    ) -> None:
        review = review_service.review(
            ReviewRequest(architecture=minimal_architecture, infrastructure=weak_infrastructure)
        )
        reliability = next(
            score for score in review.scores if score.dimension is ReviewDimension.RELIABILITY
        )

        assert 0.0 <= reliability.score <= 10.0
        assert reliability.blocking_findings
        assert reliability.confidence == "high"
        assert "finding(s)" in reliability.justification

    def test_recommendations_are_ordered_most_severe_first(
        self,
        review_service: ArchitectureReviewService,
        minimal_architecture: Architecture,
        weak_infrastructure: InfrastructurePlan,
    ) -> None:
        review = review_service.review(
            ReviewRequest(architecture=minimal_architecture, infrastructure=weak_infrastructure)
        )
        by_id = {finding.id: finding.severity for finding in review.findings}
        ordered = [by_id[item.split(":")[0]] for item in review.recommendations]

        assert ordered == sorted(ordered, key=lambda item: -item.rank)
        assert len(review.recommendations) == len(review.findings)

    def test_a_confident_low_score_must_cite_blocking_findings(
        self,
        review_service: ArchitectureReviewService,
        external_architecture: Architecture,
        healthy_infrastructure: InfrastructurePlan,
        deployment_plan: DeploymentPlan,
    ) -> None:
        review = review_service.review(
            ReviewRequest(
                architecture=external_architecture,
                infrastructure=healthy_infrastructure,
                deployment=deployment_plan,
                availability=healthy_infrastructure.availability,
            )
        )

        for score in review.scores:
            if score.confidence == "high":
                assert score.blocking_findings, (
                    f"{score.dimension} is high confidence with no blocker"
                )


class TestCatalogue:
    """Findings are published, not improvised per call site."""

    def test_every_catalogue_entry_is_complete(self) -> None:
        for identifier in ("SEC-001", "REL-002", "OBS-001"):
            text = finding_text(identifier)
            assert text.title
            assert text.description
            assert text.recommendation
            assert text.category

    def test_an_unknown_identifier_is_a_bug_guard(self) -> None:
        with pytest.raises(KeyError):
            finding_text("SEC-999")

    def test_the_scope_enum_documents_its_two_levels(self) -> None:
        assert {member.value for member in ReviewScope} == {"fast", "full"}

    def test_review_defaults_to_the_full_scope(
        self, review_service: ArchitectureReviewService, minimal_architecture: Architecture
    ) -> None:
        assert ReviewRequest(architecture=minimal_architecture).scope is ReviewScope.FULL
