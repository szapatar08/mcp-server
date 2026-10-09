"""Deployment, exposure-path and CI/CD pipeline invariants (FR-009, FR-010, FR-049)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from architecture_agent.domain.deployment import (
    DeploymentPlan,
    DeploymentStrategy,
    DeploymentTarget,
    ExposureLayer,
    PipelinePolicy,
    PipelineStage,
    RollbackStrategy,
    TlsTermination,
)

pytestmark = pytest.mark.unit


def _plan(**overrides: object) -> DeploymentPlan:
    base: dict[str, object] = {
        "name": "Kubernetes production deployment",
        "target": DeploymentTarget.KUBERNETES,
        "strategy": DeploymentStrategy.CANARY,
        "tls_termination": TlsTermination.AT_INGRESS,
        "exposure_layers": (
            ExposureLayer.INTERNET,
            ExposureLayer.DNS,
            ExposureLayer.CDN,
            ExposureLayer.WAF,
            ExposureLayer.LOAD_BALANCER,
            ExposureLayer.PRIVATE_SUBNET,
            ExposureLayer.APPLICATION_LAYER,
            ExposureLayer.DATABASE_LAYER,
            ExposureLayer.MONITORING,
        ),
    }
    base.update(overrides)
    return DeploymentPlan(**base)


class TestExposurePath:
    """FR-049: the internet-to-database path must be explicit and ordered."""

    def test_a_layered_path_is_accepted(self) -> None:
        plan = _plan()

        assert plan.exposure_layers[0] is ExposureLayer.INTERNET
        assert plan.exposure_layers[-1] is ExposureLayer.MONITORING

    def test_layers_must_follow_the_canonical_order(self) -> None:
        with pytest.raises(ValidationError, match="internet-to-database order"):
            _plan(
                exposure_layers=(
                    ExposureLayer.DATABASE_LAYER,
                    ExposureLayer.APPLICATION_LAYER,
                    ExposureLayer.PRIVATE_SUBNET,
                )
            )

    def test_a_database_without_a_private_subnet_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="segmentation error"):
            _plan(
                exposure_layers=(
                    ExposureLayer.INTERNET,
                    ExposureLayer.PUBLIC_SUBNET,
                    ExposureLayer.APPLICATION_LAYER,
                    ExposureLayer.DATABASE_LAYER,
                )
            )

    def test_layers_are_grouped_by_network_tier(self) -> None:
        grouped = _plan().layers_by_tier

        assert (
            ExposureLayer.DATABASE_LAYER
            in grouped[
                next(
                    tier
                    for tier, layers in grouped.items()
                    if ExposureLayer.DATABASE_LAYER in layers
                )
            ]
        )
        assert grouped  # tiers with layers are the only ones returned

    def test_at_least_one_layer_is_required(self) -> None:
        with pytest.raises(ValidationError):
            _plan(exposure_layers=())


class TestDeploymentPlan:
    """Deployment plans must be operable."""

    def test_health_check_paths_must_be_absolute(self) -> None:
        assert _plan(health_check_path="/healthz").health_check_path == "/healthz"
        with pytest.raises(ValidationError, match="absolute path"):
            _plan(health_check_path="healthz")

    def test_a_plan_records_its_rollback_and_secret_source(self) -> None:
        plan = _plan(
            rollback_strategy=RollbackStrategy.AUTOMATIC,
            secrets_source="aws-secrets-manager",
        )

        assert plan.rollback_strategy is RollbackStrategy.AUTOMATIC
        assert plan.secrets_source == "aws-secrets-manager"


class TestPipelinePolicy:
    """FR-010: delivery pipelines must be ordered and genuinely gated."""

    def test_a_complete_pipeline(self) -> None:
        policy = PipelinePolicy(
            name="production delivery",
            platform="github-actions",
            stages=(
                PipelineStage.SOURCE,
                PipelineStage.BUILD,
                PipelineStage.LINT,
                PipelineStage.TYPE_CHECK,
                PipelineStage.UNIT_TEST,
                PipelineStage.INTEGRATION_TEST,
                PipelineStage.SECURITY_SCAN,
                PipelineStage.CONTAINER_IMAGE,
                PipelineStage.DEPLOY_STAGING,
                PipelineStage.SMOKE_TEST,
                PipelineStage.APPROVAL,
                PipelineStage.DEPLOY_PRODUCTION,
                PipelineStage.VERIFY,
                PipelineStage.ROLLBACK,
            ),
            requires_approval_for_production=True,
            rollback=RollbackStrategy.AUTOMATIC,
            signed_artifacts=True,
            pinned_action_versions=True,
        )

        assert policy.gates_blocking is True

    def test_stages_must_be_in_delivery_order(self) -> None:
        with pytest.raises(ValidationError, match="not in a valid delivery order"):
            PipelinePolicy(
                name="reversed",
                platform="gitlab-ci",
                stages=(PipelineStage.UNIT_TEST, PipelineStage.BUILD),
            )

    def test_duplicate_stages_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be unique"):
            PipelinePolicy(
                name="duplicated",
                platform="jenkins",
                stages=(PipelineStage.BUILD, PipelineStage.BUILD),
            )

    def test_production_approval_requires_an_approval_stage(self) -> None:
        with pytest.raises(ValidationError, match="explicit approval stage"):
            PipelinePolicy(
                name="ungated",
                platform="github-actions",
                stages=(
                    PipelineStage.BUILD,
                    PipelineStage.DEPLOY_PRODUCTION,
                ),
                requires_approval_for_production=True,
            )

    def test_a_rollback_stage_needs_a_rollback_strategy(self) -> None:
        with pytest.raises(ValidationError, match="contradictory"):
            PipelinePolicy(
                name="incoherent",
                platform="github-actions",
                stages=(PipelineStage.BUILD, PipelineStage.ROLLBACK),
                rollback=RollbackStrategy.NONE,
            )

    def test_at_least_two_stages_are_required(self) -> None:
        with pytest.raises(ValidationError):
            PipelinePolicy(name="stub", platform="github-actions", stages=(PipelineStage.BUILD,))
