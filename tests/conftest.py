"""Shared test fixtures.

Tests must never require network access or provider credentials.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Callable, Iterator
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from architecture_agent.application.diagram_service import DiagramService
from architecture_agent.application.review_service import ArchitectureReviewService
from architecture_agent.config.settings import Settings, get_settings
from architecture_agent.domain.architecture import (
    Architecture,
    ArchitectureStyle,
    Assumption,
    ComponentKind,
    ExternalDependency,
    ExternalDependencyRisk,
    Interaction,
    SystemComponent,
)
from architecture_agent.domain.cloud import CloudProvider, CloudService, CloudServiceCategory
from architecture_agent.domain.cost import CostCategory, CostComponent, Currency
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
    ObservabilityStack,
)
from architecture_agent.infrastructure.rendering import MermaidRenderer, PlantUmlRenderer
from architecture_agent.mcp.server import create_server
from architecture_agent.mcp.tools import ToolServices, build_default_services


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove ambient configuration so tests observe explicit inputs only."""
    for key in list(os.environ):
        if key.startswith("ARCH_AGENT_"):
            monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Settings built without reading a ``.env`` file."""
    return Settings(_env_file=None)


@pytest.fixture
def component_factory() -> Callable[..., SystemComponent]:
    """Return a helper building a valid component with overridable fields."""
    counter = {"n": 0}

    def _factory(
        name: str | None = None,
        *,
        kind: ComponentKind = ComponentKind.SERVICE,
        is_external: bool = False,
        **overrides: object,
    ) -> SystemComponent:
        counter["n"] += 1
        return SystemComponent(
            name=name or f"component-{counter['n']}",
            kind=kind,
            responsibility="Handles the checkout flow for the order domain.",
            is_external=is_external,
            **overrides,
        )

    return _factory


@pytest.fixture
def cost_component_factory() -> Callable[..., CostComponent]:
    """Return a helper building a valid cost line item."""
    counter = {"n": 0}

    def _factory(
        *,
        name: str | None = None,
        quantity: float = 1,
        unit_price: float = 10.0,
        currency: Currency = Currency.USD,
        category: CostCategory = CostCategory.COMPUTE,
        **overrides: object,
    ) -> CostComponent:
        counter["n"] += 1
        return CostComponent(
            name=name or f"line-item-{counter['n']}",
            category=category,
            provider=CloudProvider.AWS,
            service="EC2",
            region="us-east-1",
            quantity=quantity,
            unit="vCPU-hour",
            unit_price=unit_price,
            currency=currency,
            **overrides,
        )

    return _factory


@pytest.fixture
def cloud_service_factory() -> Callable[..., CloudService]:
    """Return a helper building a valid cloud service entry."""
    counter = {"n": 0}

    def _factory(
        name: str | None = None,
        *,
        provider: CloudProvider = CloudProvider.AWS,
        category: CloudServiceCategory = CloudServiceCategory.COMPUTE,
        summary: str = "Fully managed compute service with autoscaling built in.",
        **overrides: object,
    ) -> CloudService:
        counter["n"] += 1
        return CloudService(
            provider=provider,
            name=name or f"service-{counter['n']}",
            category=category,
            summary=summary,
            **overrides,
        )

    return _factory


@pytest.fixture
def style() -> ArchitectureStyle:
    """The default architectural style used across tests."""
    return ArchitectureStyle.MODULAR_MONOLITH


@pytest.fixture
def minimal_architecture() -> Architecture:
    """A small, valid architecture used as the renderer and review baseline."""
    return Architecture(
        name="Checkout platform",
        style=ArchitectureStyle.MODULAR_MONOLITH,
        summary="A modular monolith serving checkout for the storefront.",
        components=(
            SystemComponent(
                name="storefront",
                kind=ComponentKind.CLIENT,
                responsibility="Serves the storefront and collects the cart.",
            ),
            SystemComponent(
                name="checkout-api",
                kind=ComponentKind.API,
                responsibility="Owns the checkout use case and validates the cart.",
                technology=("Python", "FastAPI"),
            ),
            SystemComponent(
                name="orders-db",
                kind=ComponentKind.DATABASE,
                responsibility="Stores orders and their lifecycle state.",
                technology=("PostgreSQL 17",),
            ),
        ),
        interactions=(
            Interaction(
                source="storefront",
                target="checkout-api",
                protocol="https",
                description="Submits the cart and receives the order confirmation.",
            ),
            Interaction(
                source="checkout-api",
                target="orders-db",
                protocol="postgres",
                description="Inserts the order and reads its current state.",
            ),
        ),
        assumptions=(
            Assumption(
                statement="Peak load is 1000 requests per second at promotion time.",
                basis="Marketing forecast for the launch campaign.",
                confidence="stated",
                impact_if_wrong="Compute sizing and the cost estimate both change.",
            ),
        ),
    )


@pytest.fixture
def external_architecture(minimal_architecture: Architecture) -> Architecture:
    """An architecture that trips the external-dependency checks."""
    return minimal_architecture.model_copy(
        update={
            "external_dependencies": (
                ExternalDependency(
                    name="Stripe Payments",
                    category="payment_processor",
                    risk=ExternalDependencyRisk.HIGH,
                    sla_known=True,
                    data_shared=("card metadata", "customer identity"),
                ),
                ExternalDependency(
                    name="SendGrid",
                    category="email_provider",
                    risk=ExternalDependencyRisk.LOW,
                    sla_known=True,
                    fallback="Retry through the internal queue.",
                ),
            )
        }
    )


@pytest.fixture
def weak_infrastructure() -> InfrastructurePlan:
    """An infrastructure plan that trips reliability and observability checks."""
    return InfrastructurePlan(
        name="Single-region platform",
        compute=(
            ComputeUnit(
                name="app-node",
                compute_type=ComputeType.VIRTUAL_MACHINE,
                quantity=1,
                vcpu=2,
                memory_gb=4,
            ),
        ),
        network=(
            NetworkSegment(
                name="public-subnet", cidr="10.0.1.0/24", tier=NetworkTier.APPLICATION, public=True
            ),
            NetworkSegment(
                name="data-subnet", cidr="10.0.2.0/24", tier=NetworkTier.DATA, public=False
            ),
        ),
        databases=(
            DatabaseUnit(
                name="orders", engine=DatabaseEngine.POSTGRESQL, topology=DatabaseTopology.SINGLE
            ),
        ),
        observability=ObservabilityStack(logs=True, metrics=True),
        availability=AvailabilityObjective(availability_target=99.9),
    )


@pytest.fixture
def healthy_infrastructure() -> InfrastructurePlan:
    """An infrastructure plan with no review findings."""
    return InfrastructurePlan(
        name="Multi-zone platform",
        compute=(
            ComputeUnit(
                name="app-nodes",
                compute_type=ComputeType.VIRTUAL_MACHINE,
                quantity=3,
                vcpu=4,
                memory_gb=16,
                scaling="horizontal",
                multi_az=True,
            ),
        ),
        network=(
            NetworkSegment(
                name="app-subnet", cidr="10.0.1.0/24", tier=NetworkTier.APPLICATION, public=False
            ),
            NetworkSegment(
                name="data-subnet", cidr="10.0.2.0/24", tier=NetworkTier.DATA, public=False
            ),
        ),
        databases=(
            DatabaseUnit(
                name="orders",
                engine=DatabaseEngine.POSTGRESQL,
                topology=DatabaseTopology.MULTI_AZ_PRIMARY_REPLICA,
                read_replica=True,
                automated_backups=True,
                point_in_time_recovery=True,
            ),
        ),
        observability=ObservabilityStack(
            logs=True,
            metrics=True,
            traces=True,
            alerting=True,
            health_checks=True,
            dashboards=True,
            slo_defined=True,
            audit_logging=True,
            retention_days=90,
        ),
        availability=AvailabilityObjective(
            availability_target=99.95, rto_minutes=30, rpo_minutes=5, multi_az=True
        ),
    )


@pytest.fixture
def deployment_plan() -> DeploymentPlan:
    """A deployment plan with a complete exposure path and a rollback strategy."""
    return DeploymentPlan(
        name="Kubernetes production deployment",
        target=DeploymentTarget.KUBERNETES,
        strategy=DeploymentStrategy.CANARY,
        tls_termination=TlsTermination.AT_LOAD_BALANCER,
        exposure_layers=(
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
        pipeline=PipelinePolicy(
            name="production delivery",
            platform="github-actions",
            stages=(
                PipelineStage.BUILD,
                PipelineStage.LINT,
                PipelineStage.UNIT_TEST,
                PipelineStage.SECURITY_SCAN,
                PipelineStage.CONTAINER_IMAGE,
                PipelineStage.DEPLOY_STAGING,
                PipelineStage.SMOKE_TEST,
                PipelineStage.APPROVAL,
                PipelineStage.DEPLOY_PRODUCTION,
                PipelineStage.ROLLBACK,
            ),
            requires_approval_for_production=True,
            rollback=RollbackStrategy.AUTOMATIC,
        ),
        rollback_strategy=RollbackStrategy.AUTOMATIC,
        secrets_source="aws-secrets-manager",
    )


@pytest.fixture
def diagram_service() -> DiagramService:
    """A diagram service with both production renderers."""
    return DiagramService((MermaidRenderer(), PlantUmlRenderer()))


@pytest.fixture
def review_service() -> ArchitectureReviewService:
    """The review use case."""
    return ArchitectureReviewService()


@pytest.fixture
def services() -> ToolServices:
    """The production service graph the MCP server builds."""
    return build_default_services()


@pytest.fixture
def isolated_logging() -> Iterator[None]:
    """Restore root logging configuration after a test mutates it."""
    import logging

    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


@pytest.fixture
def mcp_server(settings: Settings) -> FastMCP:
    """A configured MCP server, shared by the contract and security suites."""
    return create_server(settings)


@pytest.fixture
async def client(mcp_server: FastMCP) -> AsyncGenerator[Client[Any], None]:
    """An in-memory MCP client bound to the configured server."""
    async with Client(mcp_server) as bound:
        yield bound
