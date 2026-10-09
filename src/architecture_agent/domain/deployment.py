"""Deployment topology and CI/CD pipeline vocabulary."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from architecture_agent.domain.infrastructure import NetworkTier


class DeploymentStrategy(StrEnum):
    """Release strategies (FR-009)."""

    RECREATE = "recreate"
    ROLLING = "rolling"
    BLUE_GREEN = "blue_green"
    CANARY = "canary"
    RAMPED = "ramped"


class DeploymentTarget(StrEnum):
    """Where a release lands."""

    STATIC = "static"
    DOCKER = "docker"
    KUBERNETES = "kubernetes"
    VIRTUAL_MACHINE = "virtual_machine"
    SERVERLESS = "serverless"
    PAAS = "paas"


class TlsTermination(StrEnum):
    """Where TLS is terminated."""

    AT_EDGE_CDN = "at_edge_cdn"
    AT_LOAD_BALANCER = "at_load_balancer"
    AT_REVERSE_PROXY = "at_reverse_proxy"
    AT_INGRESS = "at_ingress"
    END_TO_END = "end_to_end"


class PipelineStage(StrEnum):
    """Stages of a delivery pipeline (FR-010)."""

    SOURCE = "source"
    BUILD = "build"
    LINT = "lint"
    FORMAT_CHECK = "format_check"
    TYPE_CHECK = "type_check"
    UNIT_TEST = "unit_test"
    INTEGRATION_TEST = "integration_test"
    SECURITY_SCAN = "security_scan"
    LICENSE_SCAN = "license_scan"
    BUILD_ARTIFACT = "build_artifact"
    CONTAINER_IMAGE = "container_image"
    DEPLOY_STAGING = "deploy_staging"
    SMOKE_TEST = "smoke_test"
    APPROVAL = "approval"
    DEPLOY_PRODUCTION = "deploy_production"
    VERIFY = "verify"
    ROLLBACK = "rollback"


class RollbackStrategy(StrEnum):
    """How a failed release is undone."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"
    FORWARD_FIX = "forward_fix"
    NONE = "none"


class PipelinePolicy(BaseModel):
    """A delivery pipeline described as data so it can be reviewed and tested."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    platform: str = Field(min_length=2, max_length=64)
    stages: tuple[PipelineStage, ...] = Field(min_length=2)
    requires_approval_for_production: bool = False
    rollback: RollbackStrategy = RollbackStrategy.MANUAL
    signed_artifacts: bool = False
    pinned_action_versions: bool = False
    gates_blocking: bool = True

    @model_validator(mode="after")
    def _ordering(self) -> Self:
        order = list(PipelineStage)
        positions = [order.index(stage) for stage in self.stages]
        if positions != sorted(positions):
            msg = "pipeline stages are not in a valid delivery order"
            raise ValueError(msg)
        if len(set(self.stages)) != len(self.stages):
            msg = "pipeline stages must be unique"
            raise ValueError(msg)
        if (
            self.requires_approval_for_production
            and PipelineStage.DEPLOY_PRODUCTION in self.stages
            and PipelineStage.APPROVAL not in self.stages
        ):
            msg = "production approval requires an explicit approval stage"
            raise ValueError(msg)
        if PipelineStage.ROLLBACK in self.stages and self.rollback is RollbackStrategy.NONE:
            msg = "a rollback stage with no rollback strategy is contradictory"
            raise ValueError(msg)
        return self


class ExposureLayer(StrEnum):
    """The canonical internet-to-database path (FR-049)."""

    INTERNET = "internet"
    DNS = "dns"
    CDN = "cdn"
    WAF = "waf"
    LOAD_BALANCER = "load_balancer"
    REVERSE_PROXY = "reverse_proxy"
    PUBLIC_SUBNET = "public_subnet"
    PRIVATE_SUBNET = "private_subnet"
    APPLICATION_LAYER = "application_layer"
    DATABASE_LAYER = "database_layer"
    CACHE = "cache"
    QUEUE = "queue"
    MONITORING = "monitoring"


class DeploymentPlan(BaseModel):
    """A deployment architecture."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    target: DeploymentTarget
    strategy: DeploymentStrategy
    tls_termination: TlsTermination
    exposure_layers: tuple[ExposureLayer, ...] = Field(min_length=1)
    pipeline: PipelinePolicy | None = None
    rollback_strategy: RollbackStrategy = RollbackStrategy.MANUAL
    health_check_path: str | None = Field(default=None, max_length=256)
    secrets_source: str | None = Field(default=None, max_length=256)
    container_registry: str | None = Field(default=None, max_length=256)
    notes: str | None = Field(default=None, max_length=1_000)

    @field_validator("health_check_path")
    @classmethod
    def _absolute_path(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("/"):
            msg = "health_check_path must be an absolute path"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _ordering(self) -> Self:
        order = list(ExposureLayer)
        positions = [order.index(layer) for layer in self.exposure_layers]
        if positions != sorted(positions):
            msg = "exposure layers must follow the internet-to-database order"
            raise ValueError(msg)

        if ExposureLayer.DATABASE_LAYER in self.exposure_layers and (
            ExposureLayer.INTERNET in self.exposure_layers
            and ExposureLayer.PUBLIC_SUBNET in self.exposure_layers
            and ExposureLayer.PRIVATE_SUBNET not in self.exposure_layers
        ):
            msg = "a database layer reachable without a private subnet is a segmentation error"
            raise ValueError(msg)
        return self

    @property
    def layers_by_tier(self) -> dict[NetworkTier, tuple[ExposureLayer, ...]]:
        """Group the exposure path by the network tier it belongs to."""
        mapping: dict[NetworkTier, tuple[ExposureLayer, ...]] = {
            NetworkTier.INTERNET: (
                ExposureLayer.INTERNET,
                ExposureLayer.DNS,
                ExposureLayer.CDN,
                ExposureLayer.WAF,
            ),
            NetworkTier.EDGE: (ExposureLayer.LOAD_BALANCER, ExposureLayer.REVERSE_PROXY),
            NetworkTier.APPLICATION: (
                ExposureLayer.PUBLIC_SUBNET,
                ExposureLayer.PRIVATE_SUBNET,
                ExposureLayer.APPLICATION_LAYER,
            ),
            NetworkTier.DATA: (
                ExposureLayer.DATABASE_LAYER,
                ExposureLayer.CACHE,
                ExposureLayer.QUEUE,
            ),
            NetworkTier.MANAGEMENT: (ExposureLayer.MONITORING,),
        }
        return {
            tier: tuple(layer for layer in layers if layer in self.exposure_layers)
            for tier, layers in mapping.items()
            if any(layer in self.exposure_layers for layer in layers)
        }
