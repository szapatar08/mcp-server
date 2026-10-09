"""MCP tool registration.

The seam between the public contract and the application layer. Each handler is a
thin adapter: validate, delegate, shape, convert errors. No tool contains an
architectural rule, and every tool is read-only — this server analyses, reviews,
designs, researches and generates. It never executes a command or modifies
infrastructure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from architecture_agent.application.diagram_service import DiagramService
from architecture_agent.application.errors import ApplicationError, report_for
from architecture_agent.application.review_service import ArchitectureReviewService
from architecture_agent.domain.security import ReviewDimension
from architecture_agent.infrastructure.observability.logging import get_logger, log_operation
from architecture_agent.tools.diagram_tools import generate_diagram as generate_diagram_use_case
from architecture_agent.tools.engine_tools import (
    Engines,
)
from architecture_agent.tools.engine_tools import (
    analyze_architecture as _analyze_architecture,
)
from architecture_agent.tools.engine_tools import (
    analyze_budget as _analyze_budget,
)
from architecture_agent.tools.engine_tools import (
    compare_cloud_services as _compare_cloud_services,
)
from architecture_agent.tools.engine_tools import (
    design_architecture as _design_architecture,
)
from architecture_agent.tools.engine_tools import (
    design_cloud_architecture as _design_cloud_architecture,
)
from architecture_agent.tools.engine_tools import (
    design_deployment as _design_deployment,
)
from architecture_agent.tools.engine_tools import (
    design_infrastructure as _design_infrastructure,
)
from architecture_agent.tools.engine_tools import (
    dimensioned_review as _dimensioned_review,
)
from architecture_agent.tools.engine_tools import (
    estimate_cloud_cost as _estimate_cloud_cost,
)
from architecture_agent.tools.engine_tools import (
    generate_adr as _generate_adr,
)
from architecture_agent.tools.engine_tools import (
    research_topic as _research_topic,
)
from architecture_agent.tools.engine_tools import (
    review_deployment as _review_deployment,
)
from architecture_agent.tools.engine_tools import (
    review_infrastructure as _review_infrastructure,
)
from architecture_agent.tools.engine_tools import (
    search_architecture_knowledge as _search_architecture_knowledge,
)
from architecture_agent.tools.engine_tools import (
    update_knowledge as _update_knowledge,
)
from architecture_agent.tools.review_tools import (
    review_architecture as review_architecture_use_case,
)
from architecture_agent.tools.schemas import (
    AnalyzeArchitectureInput,
    AnalyzeArchitectureOutput,
    AnalyzeBudgetInput,
    AnalyzeBudgetOutput,
    CompareCloudServicesInput,
    CompareCloudServicesOutput,
    DesignArchitectureInput,
    DesignArchitectureOutput,
    DesignCloudArchitectureInput,
    DesignCloudArchitectureOutput,
    DesignDeploymentInput,
    DesignDeploymentOutput,
    DesignInfrastructureInput,
    DesignInfrastructureOutput,
    DimensionedReviewOutput,
    EstimateCloudCostInput,
    EstimateCloudCostOutput,
    GenerateAdrInput,
    GenerateAdrOutput,
    GenerateDiagramInput,
    GenerateDiagramOutput,
    ReliabilityReviewInput,
    ResearchTopicInput,
    ResearchTopicOutput,
    ReviewArchitectureInput,
    ReviewArchitectureOutput,
    ReviewDeploymentInput,
    ReviewDeploymentOutput,
    ReviewInfrastructureInput,
    ReviewInfrastructureOutput,
    ScalabilityReviewInput,
    SearchKnowledgeInput,
    SearchKnowledgeOutput,
    SecurityReviewInput,
    UpdateKnowledgeInput,
    UpdateKnowledgeOutput,
)

_log = get_logger("architecture_agent.mcp.tools")

_READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True}

#: ``update_knowledge`` writes to the local knowledge store, so it is the one tool
#: that is not read-only. The write is confined to the operator's own database
#: file: it cannot reach a customer system. Declaring it read-only would be false
#: metadata, and a client that trusts ``readOnlyHint`` to decide whether a call is
#: safe would be misled.
_LOCAL_WRITE = {"readOnlyHint": False, "idempotentHint": True, "openWorldHint": False}

_TOOL_NAMES: tuple[str, ...] = (
    "analyze_architecture",
    "analyze_budget",
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
    "review_deployment",
    "review_infrastructure",
    "scalability_review",
    "search_architecture_knowledge",
    "security_review",
    "update_knowledge",
)


@dataclass(frozen=True, slots=True)
class ToolServices:
    """The application services the tools depend on.

    Passed explicitly rather than resolved from a module-level singleton, so the
    server has no hidden state and a test can substitute a service without
    touching the registry.
    """

    review: ArchitectureReviewService
    diagrams: DiagramService
    engines: Engines

    @property
    def uses_llm(self) -> bool:
        """Whether any model-backed engine is configured."""
        return self.engines.architecture is not None


def register_tools(server: FastMCP, services: ToolServices) -> None:
    """Register every tool on ``server``.

    Args:
        server: The FastMCP server to register on.
        services: The application services to delegate to.
    """
    from architecture_agent.mcp import resources as resource_module

    resource_module.record_tool_names(tuple(sorted(_TOOL_NAMES)))

    @server.tool(
        name="review_architecture",
        title="Review an architecture",
        tags={"architecture", "review"},
        description=(
            "Review an architecture against security, reliability, scalability and "
            "observability. Deterministic: every finding cites the input it was derived "
            "from. Dimensions whose inputs were not supplied are NOT scored and are "
            "reported as evidence gaps, so an incomplete review is never mistaken for a "
            "poor score."
        ),
        annotations=_READ_ONLY,
    )
    def review_architecture(request: ReviewArchitectureInput) -> ReviewArchitectureOutput:
        return _invoke(
            "review_architecture",
            lambda: review_architecture_use_case(request, services.review),
        )

    @server.tool(
        name="generate_diagram",
        title="Generate a UML or C4 diagram",
        tags={"architecture", "diagram"},
        description=(
            "Render an architecture as a syntactically validated Mermaid or PlantUML "
            "diagram: C4 context, container, component and code views, plus class, "
            "sequence, component and deployment views, and a network topology when an "
            "infrastructure plan is supplied. Component names are sanitised so input "
            "cannot alter diagram structure; when a label had to be altered the response "
            "reports lossy=true. Activity and state diagrams are not supported: they "
            "cannot be derived without inventing steps or states."
        ),
        annotations=_READ_ONLY,
    )
    def generate_diagram(request: GenerateDiagramInput) -> GenerateDiagramOutput:
        return _invoke(
            "generate_diagram",
            lambda: generate_diagram_use_case(request, services.diagrams),
        )

    @server.tool(
        name="design_architecture",
        title="Design an architecture",
        tags={"architecture", "design"},
        description=(
            "Design a system architecture from functional and non-functional requirements. "
            "Researches current facts before designing and refuses to invent a price, "
            "version or capability that is not in the retrieved evidence. Returns typed "
            "components and interactions, the trade-offs behind the design, the risks that "
            "remain, and the questions it could not answer."
        ),
        annotations=_READ_ONLY,
    )
    def design_architecture(request: DesignArchitectureInput) -> DesignArchitectureOutput:
        return _invoke(
            "design_architecture", lambda: _design_architecture(request, services.engines)
        )

    @server.tool(
        name="analyze_architecture",
        title="Analyse an architecture",
        tags={"architecture", "review"},
        description=(
            "Analyse a described architecture and identify its components, communication "
            "patterns, architectural style, single points of failure, bottlenecks, security "
            "risks, operational risks, cost concerns and architectural smells. Only claims "
            "the supplied description supports are returned."
        ),
        annotations=_READ_ONLY,
    )
    def analyze_architecture(request: AnalyzeArchitectureInput) -> AnalyzeArchitectureOutput:
        return _invoke(
            "analyze_architecture", lambda: _analyze_architecture(request, services.engines)
        )

    @server.tool(
        name="generate_adr",
        title="Generate an architecture decision record",
        tags={"architecture", "documentation"},
        description=(
            "Generate a numbered, immutable Architecture Decision Record with context, "
            "decision, alternatives, consequences and risks. Rejects a record without "
            "alternatives, because an ADR with no alternatives is not a decision record."
        ),
        annotations=_READ_ONLY,
    )
    def generate_adr(request: GenerateAdrInput) -> GenerateAdrOutput:
        return _invoke("generate_adr", lambda: _generate_adr(request, services.engines))

    @server.tool(
        name="design_infrastructure",
        title="Design internal infrastructure",
        tags={"infrastructure", "design"},
        description=(
            "Design compute, network segmentation, storage, databases and observability "
            "for stated requirements. Data tiers are never modelled as internet reachable "
            "and every database is required to declare backups and an availability target."
        ),
        annotations=_READ_ONLY,
    )
    def design_infrastructure(request: DesignInfrastructureInput) -> DesignInfrastructureOutput:
        return _invoke(
            "design_infrastructure", lambda: _design_infrastructure(request, services.engines)
        )

    @server.tool(
        name="review_infrastructure",
        title="Review an infrastructure plan",
        tags={"infrastructure", "review"},
        description=(
            "Review an infrastructure plan for sizing adequacy against its availability "
            "target, capacity gaps and cost drivers, and name what the plan does not state "
            "and therefore cannot be judged on."
        ),
        annotations=_READ_ONLY,
    )
    def review_infrastructure(request: ReviewInfrastructureInput) -> ReviewInfrastructureOutput:
        return _invoke(
            "review_infrastructure", lambda: _review_infrastructure(request, services.engines)
        )

    @server.tool(
        name="design_cloud_architecture",
        title="Design a cloud architecture",
        tags={"cloud", "design"},
        description=(
            "Design a provider-specific cloud architecture for AWS, Azure or GCP, naming "
            "concrete services and justifying each. A service or capability that the "
            "retrieved evidence does not confirm is reported as an open question rather "
            "than assumed."
        ),
        annotations=_READ_ONLY,
    )
    def design_cloud_architecture(
        request: DesignCloudArchitectureInput,
    ) -> DesignCloudArchitectureOutput:
        return _invoke(
            "design_cloud_architecture",
            lambda: _design_cloud_architecture(request, services.engines),
        )

    @server.tool(
        name="compare_cloud_services",
        title="Compare cloud services",
        tags={"cloud", "comparison"},
        description=(
            "Compare services against functionality, scalability, complexity, operational "
            "overhead, reliability, security, cost, team expertise, vendor lock-in, maturity "
            "and portability, then recommend one and state the circumstances that would "
            "change the recommendation."
        ),
        annotations=_READ_ONLY,
    )
    def compare_cloud_services(request: CompareCloudServicesInput) -> CompareCloudServicesOutput:
        return _invoke(
            "compare_cloud_services", lambda: _compare_cloud_services(request, services.engines)
        )

    @server.tool(
        name="design_deployment",
        title="Design a deployment strategy",
        tags={"deployment", "design"},
        description=(
            "Design a deployment strategy, exposure path, TLS termination, delivery pipeline "
            "and rollback. The database layer must sit behind a private subnet and a rollback "
            "strategy is mandatory."
        ),
        annotations=_READ_ONLY,
    )
    def design_deployment(request: DesignDeploymentInput) -> DesignDeploymentOutput:
        return _invoke("design_deployment", lambda: _design_deployment(request, services.engines))

    @server.tool(
        name="review_deployment",
        title="Review a deployment plan",
        tags={"deployment", "review"},
        description=(
            "Review release safety: what a rollback would and would not recover, what a "
            "canary would actually detect, and where the plan is silent. Does not assume a "
            "rollback was ever tested."
        ),
        annotations=_READ_ONLY,
    )
    def review_deployment(request: ReviewDeploymentInput) -> ReviewDeploymentOutput:
        return _invoke("review_deployment", lambda: _review_deployment(request, services.engines))

    @server.tool(
        name="security_review",
        title="Security review",
        tags={"security", "review"},
        description=(
            "Review authentication, authorization, IAM, secrets, TLS, network isolation, "
            "public exposure, dependency and container security, and least privilege. "
            "Scores come from deterministic checks; the model adds observations it can "
            "support and lists conclusions it could not justify."
        ),
        annotations=_READ_ONLY,
    )
    def security_review(request: SecurityReviewInput) -> DimensionedReviewOutput:
        return _invoke(
            "security_review",
            lambda: _dimensioned_review(
                request, services.engines, ReviewDimension.SECURITY, "security"
            ),
        )

    @server.tool(
        name="reliability_review",
        title="Reliability and disaster recovery review",
        tags={"reliability", "review"},
        description=(
            "Identify single points of failure, missing redundancy, missing backups, "
            "untested restores, missing failover, weak health checks, insufficient RTO or "
            "RPO, missing rollback and missing monitoring, weighed against the availability "
            "target and recovery objectives you supply."
        ),
        annotations=_READ_ONLY,
    )
    def reliability_review(request: ReliabilityReviewInput) -> DimensionedReviewOutput:
        return _invoke(
            "reliability_review",
            lambda: _dimensioned_review(
                request, services.engines, ReviewDimension.RELIABILITY, "reliability"
            ),
        )

    @server.tool(
        name="scalability_review",
        title="Scalability review",
        tags={"scalability", "review"},
        description=(
            "Review horizontal and vertical scaling, database and network bottlenecks, "
            "caching, queues, asynchronous processing, load balancing and partitioning. "
            "Without a traffic profile, scalability is judged from topology alone and that "
            "limitation is reported."
        ),
        annotations=_READ_ONLY,
    )
    def scalability_review(request: ScalabilityReviewInput) -> DimensionedReviewOutput:
        return _invoke(
            "scalability_review",
            lambda: _dimensioned_review(
                request, services.engines, ReviewDimension.SCALABILITY, "scalability"
            ),
        )

    @server.tool(
        name="estimate_cloud_cost",
        title="Estimate cloud cost",
        tags={"cost"},
        description=(
            "Estimate monthly cost from retrieved prices for a design. Every unit price must "
            "come from the price book you supply; a resource with no retrieved price is "
            "reported as unpriced rather than estimated. The result is an estimate with "
            "stated assumptions, bounds and confidence, never a quote."
        ),
        annotations=_READ_ONLY,
    )
    def estimate_cloud_cost(request: EstimateCloudCostInput) -> EstimateCloudCostOutput:
        return _invoke(
            "estimate_cloud_cost", lambda: _estimate_cloud_cost(request, services.engines)
        )

    @server.tool(
        name="analyze_budget",
        title="Analyse budget scenarios",
        tags={"cost"},
        description=(
            "Project an estimate across development, staging, production, high availability, "
            "disaster recovery and growth scenarios, stating the multiplier and rationale for "
            "each so a reader can disagree with the assumption rather than the arithmetic."
        ),
        annotations=_READ_ONLY,
    )
    def analyze_budget(request: AnalyzeBudgetInput) -> AnalyzeBudgetOutput:
        return _invoke("analyze_budget", lambda: _analyze_budget(request, services.engines))

    @server.tool(
        name="research_topic",
        title="Research a technical topic",
        tags={"research"},
        description=(
            "Search current information for a topic and return classified sources with URL, "
            "retrieval timestamp and trust tier. Authority comes from provenance, never from "
            "search ranking. Reports whether official documentation was found, and whether "
            "research itself failed."
        ),
        annotations=_READ_ONLY,
    )
    def research_topic(request: ResearchTopicInput) -> ResearchTopicOutput:
        return _invoke("research_topic", lambda: _research_topic(request, services.engines))

    @server.tool(
        name="search_architecture_knowledge",
        title="Search the knowledge base",
        tags={"research", "knowledge"},
        description=(
            "Search locally ingested architecture knowledge and return chunks with citations "
            "and their source confidence. An empty result is reported as empty, not as a "
            "statement that nothing exists."
        ),
        annotations=_READ_ONLY,
    )
    def search_architecture_knowledge(request: SearchKnowledgeInput) -> SearchKnowledgeOutput:
        return _invoke(
            "search_architecture_knowledge",
            lambda: _search_architecture_knowledge(request, services.engines),
        )

    @server.tool(
        name="update_knowledge",
        title="Update the knowledge base",
        tags={"research", "knowledge"},
        description=(
            "Ingest documents into the knowledge base. Ingestion is idempotent on content "
            "hash: unchanged documents are skipped and reported, so a scheduled refresh does "
            "not grow the corpus with duplicates. This is the only tool that writes "
            "anywhere, and only to the operator's own local knowledge database."
        ),
        annotations=_LOCAL_WRITE,
    )
    def update_knowledge(request: UpdateKnowledgeInput) -> UpdateKnowledgeOutput:
        return _invoke("update_knowledge", lambda: _update_knowledge(request, services.engines))


def _invoke[ResultT](operation: str, call: Callable[[], ResultT]) -> ResultT:
    """Run a use case, converting failures into MCP-safe errors.

    Expected failures become an actionable :class:`ToolError` carrying a scrubbed,
    field-level message. Unexpected failures are logged with a traceback and
    re-raised so FastMCP's ``mask_error_details`` hides them from the client.
    """
    try:
        with log_operation(_log, operation) as outcome:
            result = call()
            outcome["status"] = "ok"
            return result
    except ApplicationError as error:
        report = report_for(error)
        _log.warning(
            "tool.rejected",
            extra={"operation": operation, "status": "rejected", **report.as_payload()},
        )
        raise ToolError(report.render()) from None
    except Exception as error:
        report = report_for(error)
        _log.error(
            "tool.failed",
            extra={"operation": operation, "status": "error", **report.as_payload()},
            exc_info=True,
        )
        raise


def build_default_services(knowledge_directory: Any = None) -> ToolServices:
    """Build the production service graph.

    The composition root. Every dependency is constructed here and nowhere else,
    which is what keeps the application layer free of construction logic. Engines
    whose configuration is absent are left as ``None``, and the tools explain that
    rather than failing opaquely.
    """
    from architecture_agent.application.architecture_service import ArchitectureEngine
    from architecture_agent.application.cost_service import CostEngine
    from architecture_agent.application.infrastructure_service import (
        CloudEngine,
        DeploymentEngine,
        InfrastructureEngine,
        SecurityEngine,
    )
    from architecture_agent.application.knowledge_service import KnowledgeService
    from architecture_agent.application.research_service import (
        ResearchService,
        build_research_service,
    )
    from architecture_agent.config.settings import get_settings
    from architecture_agent.infrastructure.llm.langchain_client import build_client
    from architecture_agent.infrastructure.rendering import MermaidRenderer, PlantUmlRenderer
    from architecture_agent.knowledge.embeddings import build_embedder
    from architecture_agent.knowledge.storage import SqliteVectorStore

    settings = get_settings()
    review = ArchitectureReviewService()
    diagrams = DiagramService((MermaidRenderer(), PlantUmlRenderer()))

    llm: Any = None
    try:
        llm = build_client(settings.llm)
    except ApplicationError:
        llm = None

    research: ResearchService | None
    try:
        research = build_research_service(settings)
    except Exception:
        research = None

    knowledge: KnowledgeService | None = None
    if knowledge_directory is not None:
        embedder = build_embedder(
            settings.knowledge.embedding_provider.value, settings.knowledge.embedding_model
        )
        store = SqliteVectorStore(knowledge_directory, embedder)
        knowledge = KnowledgeService(
            store,
            embedder,
            chunk_tokens=settings.knowledge.chunk_size_tokens,
            overlap_tokens=settings.knowledge.chunk_overlap_tokens,
        )

    security = SecurityEngine(llm, baseline=review) if llm is not None else None
    engines = Engines(
        review=review,
        diagrams=diagrams,
        architecture=ArchitectureEngine(llm) if llm is not None else None,
        infrastructure=InfrastructureEngine(llm) if llm is not None else None,
        cloud=CloudEngine(llm) if llm is not None else None,
        deployment=DeploymentEngine(llm) if llm is not None else None,
        security=security,
        research=research,
        knowledge=knowledge,
        cost=CostEngine(llm) if llm is not None else None,
    )
    return ToolServices(review=review, diagrams=diagrams, engines=engines)
