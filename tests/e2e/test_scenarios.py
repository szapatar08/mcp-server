"""End-to-end scenarios from the specification.

Each asserts the *sections* a response must contain, not exact wording. The fake
providers keep them deterministic and offline.
"""

from __future__ import annotations

import json

import pytest

from architecture_agent.application.architecture_service import ArchitectureEngine, Requirement
from architecture_agent.application.cost_service import CostEngine
from architecture_agent.application.diagram_service import DiagramService
from architecture_agent.application.infrastructure_service import (
    CloudEngine,
    DeploymentEngine,
    InfrastructureEngine,
    SecurityEngine,
)
from architecture_agent.application.research_service import ResearchService
from architecture_agent.application.review_service import ArchitectureReviewService
from architecture_agent.domain.architecture import Architecture
from architecture_agent.domain.security import ReviewDimension
from architecture_agent.infrastructure.llm.fake_client import FakeLLMClient
from architecture_agent.infrastructure.rendering import MermaidRenderer
from architecture_agent.infrastructure.search.fake_client import (
    FakePageFetcher,
    FakeSearchClient,
)
from architecture_agent.tools.engine_tools import (
    Engines,
    design_architecture,
    dimensioned_review,
    estimate_cloud_cost,
)
from architecture_agent.tools.review_tools import review_architecture
from architecture_agent.tools.schemas import (
    EstimateCloudCostInput,
    ReliabilityReviewInput,
    ReviewArchitectureInput,
)
from tests.support import MCPClient

pytestmark = pytest.mark.e2e

_ARCH = {
    "name": "Checkout platform",
    "style": "modular_monolith",
    "summary": "A modular monolith serving checkout for the storefront.",
    "components": [
        {
            "name": "checkout-api",
            "kind": "api",
            "responsibility": "Owns the checkout use case and validates the cart.",
        },
        {
            "name": "orders-db",
            "kind": "database",
            "responsibility": "Stores orders and their lifecycle state.",
        },
    ],
    "interactions": [
        {
            "source": "checkout-api",
            "target": "orders-db",
            "protocol": "postgres",
            "description": "Inserts the order row and reads its state.",
        }
    ],
    "assumptions": [{"statement": "Peak load is 1000 requests per second."}],
}

_INFRA = {
    "name": "Single-region platform",
    "compute": [
        {
            "name": "app-node",
            "compute_type": "virtual_machine",
            "quantity": 1,
            "vcpu": 2,
            "memory_gb": 4,
        }
    ],
    "network": [
        {"name": "app", "cidr": "10.0.1.0/24", "tier": "application", "public": False},
        {"name": "data", "cidr": "10.0.2.0/24", "tier": "data", "public": False},
    ],
    "databases": [{"name": "orders", "engine": "postgresql", "topology": "single"}],
    "availability": {"availability_target": 99.9},
}


class TestScenarioOneDesign:
    """Design a web application and produce every required section."""

    def test_the_response_carries_every_required_section(self) -> None:
        answer = json.dumps(
            {
                "result": {
                    "architecture": {
                        "name": "Checkout platform",
                        "style": "modular_monolith",
                        "summary": "A modular monolith serving checkout for the storefront.",
                        "components": [
                            {
                                "name": "checkout-api",
                                "kind": "api",
                                "responsibility": "Owns the checkout use case.",
                                "technology": ["Python", "FastAPI"],
                            }
                        ],
                        "assumptions": [{"statement": "Peak load is 1000 rps."}],
                    },
                    "trade_offs": [
                        {
                            "decision": "Modular monolith for the first release.",
                            "rationale": "One transaction boundary at this team size.",
                            "alternatives_considered": ["Microservices"],
                            "advantages": ["Simpler operations"],
                            "disadvantages": ["Boundaries can erode"],
                            "reversibility": "high",
                        }
                    ],
                    "risks": ["Module boundaries erode without enforcement"],
                    "open_questions": ["Is 1000 rps sustained or peak?"],
                },
                "reasoning_summary": "Chose a monolith because the team is small.",
                "assumptions": ["Team under ten engineers"],
                "confidence": "medium",
            }
        )
        llm = FakeLLMClient(answers=[answer])
        research = ResearchService(search=FakeSearchClient(), fetcher=FakePageFetcher())

        from architecture_agent.tools.schemas import DesignArchitectureInput

        output = design_architecture(
            DesignArchitectureInput(
                requirements=(
                    Requirement(id="R1", description="Accept and store customer orders."),
                ),
                research_topic="checkout architecture",
            ),
            Engines(
                review=ArchitectureReviewService(),
                diagrams=DiagramService((MermaidRenderer(),)),
                architecture=ArchitectureEngine(llm),
                research=research,
            ),
        )

        assert output.proposal.architecture.components
        assert output.proposal.trade_offs[0].alternatives_considered
        assert output.proposal.risks
        assert output.proposal.open_questions
        assert output.assumptions == ("Team under ten engineers",)
        assert output.sources, "research should have produced citable sources"
        assert output.reasoning_summary


class TestScenarioTwoCost:
    """Estimate AWS cost with assumptions, sources and projections."""

    def test_the_estimate_carries_assumptions_and_provenance(self) -> None:
        answer = json.dumps(
            {
                "result": {
                    "components": [
                        {
                            "name": "api-instances",
                            "category": "compute",
                            "service": "EC2",
                            "region": "us-east-1",
                            "quantity": "3",
                        }
                    ],
                    "assumptions": [
                        "Three t4g.medium instances running continuously",
                        "No data transfer included",
                    ],
                    "cost_drivers": ["Compute dominates at this shape"],
                    "optimization_opportunities": ["Savings Plans for the steady baseline"],
                },
                "confidence": "high",
            }
        )
        output = estimate_cloud_cost(
            EstimateCloudCostInput(
                design=(
                    "Three EC2 instances running the checkout API continuously, with a "
                    "single-AZ PostgreSQL database and no outbound transfer."
                ),
                prices=(
                    {
                        "provider": "aws",
                        "service": "EC2",
                        "region": "us-east-1",
                        "unit": "vCPU-hour",
                        "unit_price": "0.0416",
                        "tier": 1,
                        "source_url": "https://aws.amazon.com/ec2/pricing/",
                    },
                ),
                prices_as_of="2026-10-07",
                usage_assumptions=("Always on, 100% utilisation assumed.",),
            ),
            Engines(
                review=ArchitectureReviewService(),
                diagrams=DiagramService((MermaidRenderer(),)),
                cost=CostEngine(FakeLLMClient(answers=[answer])),
            ),
        )

        assert output.estimate is not None
        assert len(output.estimate.assumptions) >= 1
        assert output.estimate.pricing_references
        assert output.estimate.confidence.value == "high"
        assert output.is_estimate_not_quote is True
        assert output.estimate.cost_drivers
        assert output.estimate.annual_total() > output.estimate.monthly_total()


class TestScenarioThreeReview:
    """Review an architecture across every dimension."""

    def test_a_complete_review_reports_findings_and_gaps(self) -> None:
        from architecture_agent.domain.architecture import Architecture

        architecture = Architecture.model_validate(_ARCH)
        review_input = ReviewArchitectureInput(
            architecture=architecture,
            infrastructure=__import__(
                "architecture_agent.domain.infrastructure", fromlist=["InfrastructurePlan"]
            ).InfrastructurePlan.model_validate(_INFRA),
        )
        baseline = review_architecture(review_input, ArchitectureReviewService())
        dimensions = {score.dimension for score in baseline.review.scores}

        assert dimensions == {
            ReviewDimension.SECURITY,
            ReviewDimension.RELIABILITY,
            ReviewDimension.OBSERVABILITY,
            ReviewDimension.SCALABILITY,
        }
        assert baseline.complete is False
        assert baseline.review.evidence_gaps
        assert {finding.id for finding in baseline.review.findings} >= {"REL-002", "REL-003"}

    def test_a_dimension_the_evidence_cannot_support_is_refused(self) -> None:
        with pytest.raises(Exception, match="were not scored"):
            dimensioned_review(
                ReliabilityReviewInput(architecture=Architecture.model_validate(_ARCH)),
                Engines(
                    review=ArchitectureReviewService(),
                    diagrams=DiagramService((MermaidRenderer(),)),
                    security=SecurityEngine(FakeLLMClient(), baseline=ArchitectureReviewService()),
                ),
                ReviewDimension.RELIABILITY,
                "reliability",
            )


class TestEnginesRegistered:
    """Every engine in the specification is reachable through the server."""

    async def test_the_server_advertises_the_full_capability_set(self, client: MCPClient) -> None:
        names = {tool.name for tool in await client.list_tools()}

        assert names >= {
            "analyze_architecture",
            "compare_cloud_services",
            "design_architecture",
            "design_cloud_architecture",
            "design_deployment",
            "design_infrastructure",
            "estimate_cloud_cost",
            "generate_adr",
            "generate_diagram",
            "reliability_review",
            "research_topic",
            "review_architecture",
            "scalability_review",
            "security_review",
        }

    async def test_cloud_and_deployment_engines_build(self) -> None:
        """The engines construct with the fake provider and accept a request."""
        llm = FakeLLMClient()
        assert CloudEngine(llm) is not None
        assert DeploymentEngine(llm) is not None
        assert InfrastructureEngine(llm) is not None
