"""MCP tool input and output schemas for every registered tool.

These are the public contract. Each is a named Pydantic model with
``extra="forbid"`` so a changed field is a changed API, caught by the contract
snapshot rather than discovered by a client.

Optional inputs are meaningful: an omitted optional field means "the caller does
not know", which the engines report as an evidence gap. A guessed value produces a
number or score that looks measured but is not, so the schemas ask for what is
known and nothing more.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from architecture_agent.application.architecture_service import (
    ArchitectureAnalysis,
    DesignProposal,
    Requirement,
)
from architecture_agent.application.cost_service import BudgetLine, CostGap, PriceQuote
from architecture_agent.application.infrastructure_service import (
    CloudDraft,
    ComparisonDraft,
    DeploymentDraft,
    InfrastructureDraft,
    ReviewDraft,
)
from architecture_agent.domain.architecture import Architecture, ArchitectureDecisionRecord
from architecture_agent.domain.cloud import (
    CloudProvider,
    CloudService,
    ComparisonCriterion,
)
from architecture_agent.domain.cost import BudgetScenario, CostEstimate
from architecture_agent.domain.deployment import DeploymentPlan
from architecture_agent.domain.diagram import Diagram, DiagramKind, DiagramLanguage
from architecture_agent.domain.infrastructure import InfrastructurePlan
from architecture_agent.domain.research import (
    ConfidenceLevel,
    KnowledgeCategory,
    ResearchResult,
)
from architecture_agent.domain.security import (
    ArchitectureReview,
    ComplianceFramework,
)


class _Schema(BaseModel):
    """Shared configuration for every published schema."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class _ReasoningOut(_Schema):
    """Fields shared by every model-assisted response."""

    reasoning_summary: str = Field(
        default="",
        description="The model's own summary of its reasoning. Never hidden chain-of-thought.",
    )
    assumptions: tuple[str, ...] = Field(
        default=(), description="Assumptions the model stated it made."
    )
    model_confidence: ConfidenceLevel = Field(
        default=ConfidenceLevel.MEDIUM,
        description="Confidence the model claimed. Not the same as evidence quality.",
    )
    model: str = Field(default="", description="Model identifier that produced the answer.")
    provider: str = Field(default="", description="LLM provider that produced the answer.")
    degraded: bool = Field(
        default=False,
        description=(
            "True when research or retrieval failed; the answer used less evidence than requested."
        ),
    )
    degradation_reason: str | None = None
    injection_attempts: tuple[str, ...] = Field(
        default=(),
        description=(
            "Instruction-shaped content found inside retrieved sources. Reported as "
            "evidence and never followed."
        ),
    )
    sources: tuple[str, ...] = Field(default=(), description="URLs the answer relied on.")


# --------------------------------------------------------------------------- review


class ReviewArchitectureInput(_Schema):
    """Material to review."""

    architecture: Architecture
    infrastructure: InfrastructurePlan | None = Field(
        default=None,
        description=(
            "Enables reliability, scalability and observability checks. Without it "
            "those dimensions are not scored."
        ),
    )
    deployment: DeploymentPlan | None = Field(
        default=None, description="Enables TLS, rollback and exposure-path checks."
    )
    availability_target: float | None = Field(
        default=None, ge=90.0, le=100.0, description="Availability target in percent."
    )
    rto_minutes: int | None = Field(default=None, ge=0, le=100_000)
    rpo_minutes: int | None = Field(default=None, ge=0, le=100_000)
    multi_region: bool = False
    cloud_provider: CloudProvider | None = None


class ReviewArchitectureOutput(_Schema):
    """A structured review with scores, findings and the evidence that is missing."""

    review: ArchitectureReview
    summary: str
    complete: bool = Field(description="False when a dimension could not be scored.")


# --------------------------------------------------------------------------- diagram


class GenerateDiagramInput(_Schema):
    """What to render, and in which language."""

    architecture: Architecture
    diagram_kind: DiagramKind = DiagramKind.C4_CONTAINER
    language: DiagramLanguage = DiagramLanguage.MERMAID
    infrastructure: InfrastructurePlan | None = Field(
        default=None, description="Required for a network_topology diagram."
    )
    title: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=2_000)


class GenerateDiagramOutput(_Schema):
    """A validated diagram plus a Markdown rendering."""

    diagram: Diagram
    markdown: str
    lossy: bool = Field(description="True when labels had to be altered to keep the diagram valid.")


# --------------------------------------------------------------------------- research


class ResearchTopicInput(_Schema):
    """A research question about dynamic facts."""

    topic: str = Field(min_length=2, max_length=128, description="What is being researched.")
    query: str | None = Field(
        default=None,
        max_length=512,
        description="Search query. Defaults to the topic plus an official-documentation suffix.",
    )
    max_results: int = Field(default=5, ge=1, le=20)
    require_official_sources: bool = Field(
        default=True,
        description="Exclude anything above official documentation or standard level.",
    )
    category: KnowledgeCategory = KnowledgeCategory.DYNAMIC
    fetch_pages: bool = Field(
        default=True,
        description="Retrieve page bodies. When false, only titles and snippets are collected.",
    )


class ResearchTopicOutput(_Schema):
    """Retrieved sources and whether the evidence was sufficient."""

    result: ResearchResult
    authoritative_source_count: int
    sufficient_for_a_claim: bool = Field(
        description="False when nothing at official level was retrieved, or research failed."
    )
    note: str


class SearchKnowledgeInput(_Schema):
    """A query against the local knowledge base."""

    query: str = Field(min_length=2, max_length=512)
    topic: str | None = Field(default=None, max_length=128)
    limit: int = Field(default=5, ge=1, le=25)


class SearchKnowledgeOutput(_Schema):
    """Retrieved chunks with citations."""

    citations: tuple[str, ...]
    excerpts: tuple[str, ...]
    best_confidence: ConfidenceLevel
    degraded: bool = False
    degradation_reason: str | None = None


class UpdateKnowledgeInput(_Schema):
    """Documents to ingest into the knowledge base."""

    topic: str = Field(min_length=2, max_length=128)
    urls: tuple[str, ...] = Field(min_length=1, max_length=25)
    documents: tuple[str, ...] = Field(
        default=(),
        max_length=25,
        description="Optional explicit document bodies, aligned with urls. Use when the "
        "caller already has the text and no fetch is wanted.",
    )


class UpdateKnowledgeOutput(_Schema):
    """What the ingestion run did."""

    ingested: int
    skipped_unchanged: int = Field(
        description="Documents whose content hash matched the last ingestion."
    )
    rejected: int
    chunks_written: int
    stats: dict[str, int]
    reasons: tuple[str, ...] = ()


# --------------------------------------------------------------------------- architecture


class DesignArchitectureInput(_Schema):
    """What to design and the constraints it must respect."""

    requirements: tuple[Requirement, ...] = Field(min_length=1, max_length=50)
    constraints: tuple[str, ...] = Field(default=(), max_length=50)
    non_functionals: tuple[str, ...] = Field(default=(), max_length=50)
    provider: CloudProvider | None = Field(
        default=None, description="Research provider-specific facts before designing."
    )
    research_topic: str | None = Field(
        default=None, max_length=128, description="Topic to research for dynamic facts."
    )
    use_knowledge_base: bool = Field(
        default=False, description="Also retrieve from the local knowledge base."
    )


class DesignArchitectureOutput(_ReasoningOut):
    """A validated design proposal plus the model's own account of it."""

    proposal: DesignProposal
    valid: bool = Field(
        default=True,
        description="Always true: an invalid proposal is rejected rather than returned.",
    )


class AnalyzeArchitectureInput(_Schema):
    """A description, and optionally a structured model, to analyse."""

    description: str = Field(min_length=10, max_length=20_000)
    architecture: Architecture | None = Field(
        default=None, description="Improves the analysis; without it, only the prose is used."
    )
    research_topic: str | None = Field(default=None, max_length=128)


class AnalyzeArchitectureOutput(_ReasoningOut):
    """A structured analysis."""

    analysis: ArchitectureAnalysis


class GenerateAdrInput(_Schema):
    """A decision to record."""

    title: str = Field(min_length=5, max_length=256)
    context: str = Field(min_length=20, max_length=20_000)
    decision: str | None = Field(
        default=None, max_length=8_000, description="The decision already taken, if any."
    )
    alternatives: tuple[str, ...] = Field(default=(), max_length=20)
    next_id: str | None = Field(
        default=None,
        pattern=r"^ADR-\d{3,}$",
        description="Identifier to use. Omit to have one proposed.",
    )


class GenerateAdrOutput(_ReasoningOut):
    """A validated architecture decision record."""

    record: ArchitectureDecisionRecord


# --------------------------------------------------------------------------- infrastructure


class DesignInfrastructureInput(_Schema):
    """What to design and the constraints it must respect."""

    requirements: tuple[Requirement, ...] = Field(min_length=1, max_length=50)
    constraints: tuple[str, ...] = Field(default=(), max_length=50)
    provider: CloudProvider | None = None
    research_topic: str | None = Field(default=None, max_length=128)


class DesignInfrastructureOutput(_ReasoningOut):
    """A validated infrastructure plan."""

    plan: InfrastructurePlan


class ReviewInfrastructureInput(_Schema):
    """A plan to review."""

    plan: InfrastructurePlan


class ReviewInfrastructureOutput(_ReasoningOut):
    """Observations the deterministic checks cannot make."""

    review: ReviewDraft


class DesignCloudArchitectureInput(_Schema):
    """What to design, and where."""

    provider: CloudProvider
    regions: tuple[str, ...] = Field(min_length=1, max_length=8)
    requirements: tuple[Requirement, ...] = Field(min_length=1, max_length=50)
    constraints: tuple[str, ...] = Field(default=(), max_length=50)
    research_topic: str | None = Field(default=None, max_length=128)


class DesignCloudArchitectureOutput(_ReasoningOut):
    """A validated cloud architecture."""

    draft: CloudDraft


class CompareCloudServicesInput(_Schema):
    """What to compare, and against which criteria."""

    question: str = Field(min_length=5, max_length=512)
    services: tuple[CloudService, ...] = Field(min_length=2, max_length=8)
    criteria: tuple[ComparisonCriterion, ...] = Field(min_length=2, max_length=10)
    research_topic: str | None = Field(default=None, max_length=128)


class CompareCloudServicesOutput(_ReasoningOut):
    """A comparison with a justified recommendation."""

    draft: ComparisonDraft


class DesignDeploymentInput(_Schema):
    """What to design."""

    requirements: tuple[Requirement, ...] = Field(min_length=1, max_length=50)
    constraints: tuple[str, ...] = Field(default=(), max_length=50)
    provider: CloudProvider | None = None


class DesignDeploymentOutput(_ReasoningOut):
    """A validated deployment plan."""

    draft: DeploymentDraft


class ReviewDeploymentInput(_Schema):
    """A plan to review."""

    plan: DeploymentPlan


class ReviewDeploymentOutput(_ReasoningOut):
    """Release-safety observations."""

    review: ReviewDraft


# --------------------------------------------------------------------------- reviews


class _SecurityReviewInput(_Schema):
    """Material for a security-focused review."""

    architecture: Architecture
    infrastructure: InfrastructurePlan | None = None
    deployment: DeploymentPlan | None = None
    availability_target: float | None = Field(default=None, ge=90.0, le=100.0)
    rto_minutes: int | None = Field(default=None, ge=0, le=100_000)
    rpo_minutes: int | None = Field(default=None, ge=0, le=100_000)
    multi_region: bool = False
    frameworks: tuple[ComplianceFramework, ...] = Field(
        default=(), description="External references to ground the review in."
    )
    research_topic: str | None = Field(default=None, max_length=128)


class SecurityReviewInput(_SecurityReviewInput):
    """Security review of an architecture."""


class ReliabilityReviewInput(_SecurityReviewInput):
    """Reliability and disaster-recovery review of an architecture."""


class ScalabilityReviewInput(_SecurityReviewInput):
    """Scalability review of an architecture."""


class DimensionedReviewOutput(_ReasoningOut):
    """A deterministic review plus model observations."""

    review: ArchitectureReview
    observations: ReviewDraft


# --------------------------------------------------------------------------- cost


class EstimateCloudCostInput(_Schema):
    """A design, the prices to price it with, and the usage to assume."""

    design: str = Field(min_length=20, max_length=20_000)
    prices: tuple[PriceQuote, ...] = Field(
        min_length=1,
        max_length=200,
        description="Retrieved prices. An estimate without these is refused.",
    )
    prices_as_of: str = Field(
        default="", max_length=64, description="Retrieval date of the price set."
    )
    usage_assumptions: tuple[str, ...] = Field(default=(), max_length=50)
    provider: CloudProvider | None = Field(
        default=None, description="Research current pricing for this provider before estimating."
    )
    region: str | None = Field(default=None, max_length=64)


class EstimateCloudCostOutput(_ReasoningOut):
    """An estimate with provenance, or the gaps that prevented one."""

    estimate: CostEstimate | None = Field(
        default=None,
        description="Null when nothing could be priced from the retrieved prices.",
    )
    unpriced: tuple[str, ...] = Field(
        default=(), description="Resources that had no retrieved price."
    )
    is_estimate_not_quote: bool = Field(
        default=True,
        description="Always true. Cloud prices are dynamic and this figure is not a quote.",
    )


class AnalyzeBudgetInput(_Schema):
    """An estimate to project across scenarios."""

    title: str = Field(min_length=3, max_length=256)
    currency_components: tuple[dict[str, object], ...] = Field(min_length=1, max_length=200)
    assumptions: tuple[str, ...] = Field(min_length=1, max_length=50)
    scenarios: tuple[BudgetScenario, ...] = Field(min_length=1, max_length=6)


class AnalyzeBudgetOutput(_Schema):
    """Scenario projections with their multipliers stated."""

    estimate: CostEstimate
    lines: tuple[BudgetLine, ...]
    note: str = Field(description="States that these are projections of an estimate, not quotes.")


__all__ = [
    "AnalyzeArchitectureInput",
    "AnalyzeArchitectureOutput",
    "AnalyzeBudgetInput",
    "AnalyzeBudgetOutput",
    "BudgetLine",
    "CloudDraft",
    "ComparisonDraft",
    "CostGap",
    "DeploymentDraft",
    "DesignArchitectureInput",
    "DesignArchitectureOutput",
    "DesignCloudArchitectureInput",
    "DesignCloudArchitectureOutput",
    "DesignDeploymentInput",
    "DesignDeploymentOutput",
    "DesignInfrastructureInput",
    "DesignInfrastructureOutput",
    "DimensionedReviewOutput",
    "EstimateCloudCostInput",
    "EstimateCloudCostOutput",
    "GenerateAdrInput",
    "GenerateAdrOutput",
    "GenerateDiagramInput",
    "GenerateDiagramOutput",
    "InfrastructureDraft",
    "ReliabilityReviewInput",
    "ReviewArchitectureInput",
    "ReviewArchitectureOutput",
    "ReviewDeploymentInput",
    "ReviewDeploymentOutput",
    "ReviewDraft",
    "ReviewInfrastructureInput",
    "ReviewInfrastructureOutput",
    "ScalabilityReviewInput",
    "SearchKnowledgeInput",
    "SearchKnowledgeOutput",
    "SecurityReviewInput",
    "UpdateKnowledgeInput",
    "UpdateKnowledgeOutput",
]
