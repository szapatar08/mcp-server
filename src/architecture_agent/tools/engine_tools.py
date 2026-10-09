"""Tool handlers for the model-assisted engines.

Each handler is a thin adapter: convert a schema into a use-case call, wrap the
result, and attach the model's own account of what it did. No handler contains an
architectural rule; if one needs one, it belongs in the application layer.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from architecture_agent.application.architecture_service import (
    ArchitectureEngine,
    Constraint,
    Requirement,
)
from architecture_agent.application.cost_service import (
    CostEngine,
    PriceBook,
)
from architecture_agent.application.guard import GuardedEvidence, UntrustedContentGuard
from architecture_agent.application.infrastructure_service import (
    CloudEngine,
    DeploymentEngine,
    InfrastructureEngine,
    ReviewDraft,
    SecurityEngine,
)
from architecture_agent.application.knowledge_service import KnowledgeService
from architecture_agent.application.llm_service import ReasoningOutcome
from architecture_agent.application.research_service import ResearchService
from architecture_agent.domain.infrastructure import AvailabilityObjective
from architecture_agent.domain.research import (
    ConfidenceLevel,
    FreshnessPolicy,
    KnowledgeCategory,
    ResearchRequest,
    SourceTier,
)
from architecture_agent.domain.security import ReviewDimension
from architecture_agent.tools.review_tools import review_architecture as review_use_case
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
    ResearchTopicInput,
    ResearchTopicOutput,
    ReviewDeploymentInput,
    ReviewDeploymentOutput,
    ReviewInfrastructureInput,
    ReviewInfrastructureOutput,
    SearchKnowledgeInput,
    SearchKnowledgeOutput,
    UpdateKnowledgeInput,
    UpdateKnowledgeOutput,
)

_NEGATIVE_INJECTION_KINDS = ("instruction_override", "prompt_exfiltration", "command_execution")


@dataclass(frozen=True, slots=True)
class Engines:
    """Every application service the tools depend on."""

    review: Any
    diagrams: Any
    architecture: ArchitectureEngine | None = None
    infrastructure: InfrastructureEngine | None = None
    cloud: CloudEngine | None = None
    deployment: DeploymentEngine | None = None
    security: SecurityEngine | None = None
    research: ResearchService | None = None
    knowledge: KnowledgeService | None = None
    cost: CostEngine | None = None


def _reasoning_fields(outcome: ReasoningOutcome, evidence: GuardedEvidence) -> dict[str, Any]:
    """Flatten a model outcome plus guarded evidence into output fields."""
    return {
        "reasoning_summary": outcome.reasoning_summary,
        "assumptions": outcome.assumptions,
        "model_confidence": outcome.confidence,
        "model": outcome.model,
        "provider": outcome.provider,
        "degraded": outcome.degraded,
        "degradation_reason": outcome.degradation_reason,
        "injection_attempts": tuple(
            str(attempt)
            for attempt in evidence.injections
            if attempt.kind.value in _NEGATIVE_INJECTION_KINDS
        ),
        "sources": tuple(block.url for block in evidence.blocks),
    }


def _require(service: object | None, name: str) -> Any:
    """Return a configured engine or explain why it is unavailable."""
    from architecture_agent.application.errors import UnsupportedRequestError

    if service is None:
        msg = (
            f"{name} is not configured. It needs an LLM provider and a search provider; "
            "check ARCH_AGENT_LLM__PROVIDER and ARCH_AGENT_SEARCH__PROVIDER."
        )
        raise UnsupportedRequestError(msg)
    return service


def _evidence_for(
    research: ResearchService | None, topic: str | None
) -> tuple[GuardedEvidence, Any]:
    """Research ``topic`` and return guarded evidence plus the research result."""
    guard = UntrustedContentGuard()
    if research is None or not topic:
        return guard.guard_many(()), None
    request = ResearchRequest(
        query=f"{topic} official documentation pricing capabilities",
        topic=topic,
        category=KnowledgeCategory.DYNAMIC,
        max_results=5,
        require_official_sources=True,
    )
    result, evidence = research.research_with_evidence(request)
    return evidence, result


def _knowledge_evidence(
    knowledge: KnowledgeService | None, query: str
) -> tuple[GuardedEvidence, tuple[str, ...]]:
    """Retrieve from the knowledge base and return guarded evidence plus citations."""
    if knowledge is None:
        return UntrustedContentGuard().guard_many(()), ()
    result = knowledge.retrieve(query=query, limit=4)
    return (
        UntrustedContentGuard().guard_many(
            tuple((chunk.url, chunk.text, chunk.tier, chunk.confidence) for chunk in result.chunks)
        ),
        result.citations(),
    )


# --------------------------------------------------------------------------- review


def review_architecture(request: Any, engines: Engines) -> Any:
    """Delegate to the deterministic review tool."""
    return review_use_case(request, engines.review)


def review_infrastructure(
    request: ReviewInfrastructureInput, engines: Engines
) -> ReviewInfrastructureOutput:
    """Ask for observations the deterministic infrastructure checks cannot make."""
    engine = _require(engines.infrastructure, "infrastructure review")
    outcome = engine.review(plan=request.plan)
    return ReviewInfrastructureOutput(
        review=_review_draft(outcome),
        **_reasoning_fields(outcome, UntrustedContentGuard().guard_many(())),
    )


def _review_draft(outcome: ReasoningOutcome) -> ReviewDraft:
    """Validate the model's observations."""
    return outcome.as_model(ReviewDraft)


def review_deployment(request: ReviewDeploymentInput, engines: Engines) -> ReviewDeploymentOutput:
    """Ask for release-safety observations on a deployment plan."""
    engine = _require(engines.deployment, "deployment review")
    outcome = engine.review(plan=request.plan)
    return ReviewDeploymentOutput(
        review=_review_draft(outcome),
        **_reasoning_fields(outcome, UntrustedContentGuard().guard_many(())),
    )


def dimensioned_review(
    request: Any,
    engines: Engines,
    dimension: ReviewDimension,
    label: str,
) -> DimensionedReviewOutput:
    """Run the deterministic baseline plus a model pass focused on one dimension."""
    engine = _require(engines.security, f"{label} review")
    from architecture_agent.application.review_service import ReviewRequest

    availability = _availability(request)
    review_request = ReviewRequest(
        architecture=request.architecture,
        infrastructure=request.infrastructure,
        deployment=request.deployment,
        availability=availability,
        cloud_provider=getattr(request, "cloud_provider", None),
    )
    evidence, _ = _evidence_for(engines.research, request.research_topic)
    baseline, draft, outcome = engine.review(
        request=review_request,
        dimensions=(dimension,),
        frameworks=request.frameworks,
        evidence=evidence.render(),
    )
    return DimensionedReviewOutput(
        review=baseline,
        observations=draft,
        **_reasoning_fields(outcome, evidence),
    )


def _availability(request: Any) -> AvailabilityObjective | None:
    """Build an availability objective from whichever fields were supplied."""
    supplied = (
        request.availability_target,
        request.rto_minutes,
        request.rpo_minutes,
        request.multi_region,
    )
    if all(value is None or value is False for value in supplied):
        return None
    from architecture_agent.application.errors import InvalidInputError

    if request.availability_target is None:
        msg = (
            "availability_target is required when recovery objectives or multi_region are supplied"
        )
        raise InvalidInputError(msg)
    return AvailabilityObjective(
        availability_target=request.availability_target,
        rto_minutes=request.rto_minutes,
        rpo_minutes=request.rpo_minutes,
        multi_region=request.multi_region,
    )


# --------------------------------------------------------------------------- architecture


def design_architecture(
    request: DesignArchitectureInput, engines: Engines
) -> DesignArchitectureOutput:
    """Design an architecture from requirements, research first for dynamic facts."""
    engine = _require(engines.architecture, "architecture design")
    evidence, _ = _evidence_for(engines.research, request.research_topic)
    citations = tuple(block.url for block in evidence.blocks)

    if request.use_knowledge_base and engines.knowledge is not None:
        knowledge_evidence, knowledge_citations = _knowledge_evidence(
            engines.knowledge, " ".join(item.description for item in request.requirements)
        )
        evidence = GuardedEvidence(
            blocks=evidence.blocks + knowledge_evidence.blocks,
            nonce=secrets.token_hex(8),
        )
        citations = citations + knowledge_citations

    requirements = request.requirements
    proposal, outcome = engine.design(
        requirements=requirements,
        constraints=tuple(Constraint(description=item) for item in request.constraints),
        non_functionals=request.non_functionals,
        evidence=evidence.render(),
    )
    fields = _reasoning_fields(outcome, evidence)
    fields["sources"] = tuple(dict.fromkeys(citations))
    return DesignArchitectureOutput(proposal=proposal, **fields)


def analyze_architecture(
    request: AnalyzeArchitectureInput, engines: Engines
) -> AnalyzeArchitectureOutput:
    """Analyse a described architecture."""
    engine = _require(engines.architecture, "architecture analysis")
    evidence, _ = _evidence_for(engines.research, request.research_topic)
    analysis, outcome = engine.analyse(
        description=request.description,
        architecture=request.architecture,
        evidence=evidence.render(),
    )
    return AnalyzeArchitectureOutput(analysis=analysis, **_reasoning_fields(outcome, evidence))


def generate_adr(request: GenerateAdrInput, engines: Engines) -> GenerateAdrOutput:
    """Draft an architecture decision record."""
    engine = _require(engines.architecture, "ADR generation")
    record, outcome = engine.draft_adr(
        title=request.title,
        context=request.context,
        decision=request.decision or "",
        alternatives=request.alternatives,
    )
    if request.next_id is not None and record.id != request.next_id:
        record = record.model_copy(update={"id": request.next_id})
    return GenerateAdrOutput(
        record=record,
        **_reasoning_fields(outcome, UntrustedContentGuard().guard_many(())),
    )


# --------------------------------------------------------------------------- infrastructure


def design_infrastructure(
    request: DesignInfrastructureInput, engines: Engines
) -> DesignInfrastructureOutput:
    """Design internal infrastructure."""
    engine = _require(engines.infrastructure, "infrastructure design")
    evidence, _ = _evidence_for(engines.research, request.research_topic)
    plan, outcome = engine.design(
        requirements=request.requirements,
        constraints=tuple(Constraint(description=item) for item in request.constraints),
        evidence=evidence.render(),
    )
    return DesignInfrastructureOutput(plan=plan, **_reasoning_fields(outcome, evidence))


def design_cloud_architecture(
    request: DesignCloudArchitectureInput, engines: Engines
) -> DesignCloudArchitectureOutput:
    """Design a provider-specific cloud architecture."""
    engine = _require(engines.cloud, "cloud design")
    topic = request.research_topic or f"{request.provider.value} architecture"
    evidence, _ = _evidence_for(engines.research, topic)
    draft, outcome = engine.design(
        provider=request.provider,
        regions=request.regions,
        requirements=request.requirements,
        constraints=tuple(Constraint(description=item) for item in request.constraints),
        evidence=evidence.render(),
    )
    return DesignCloudArchitectureOutput(draft=draft, **_reasoning_fields(outcome, evidence))


def compare_cloud_services(
    request: CompareCloudServicesInput, engines: Engines
) -> CompareCloudServicesOutput:
    """Compare cloud services and recommend one."""
    engine = _require(engines.cloud, "service comparison")
    evidence, _ = _evidence_for(engines.research, request.research_topic)
    draft, outcome = engine.compare(
        question=request.question,
        services=request.services,
        criteria=request.criteria,
        evidence=evidence.render(),
    )
    return CompareCloudServicesOutput(draft=draft, **_reasoning_fields(outcome, evidence))


def design_deployment(request: DesignDeploymentInput, engines: Engines) -> DesignDeploymentOutput:
    """Design a deployment and delivery strategy."""
    engine = _require(engines.deployment, "deployment design")
    evidence, _ = _evidence_for(engines.research, request.provider)
    draft, outcome = engine.design(
        requirements=request.requirements,
        constraints=tuple(Constraint(description=item) for item in request.constraints),
        evidence=evidence.render(),
    )
    return DesignDeploymentOutput(draft=draft, **_reasoning_fields(outcome, evidence))


# --------------------------------------------------------------------------- research


def research_topic(request: ResearchTopicInput, engines: Engines) -> ResearchTopicOutput:
    """Research a topic and report whether the evidence supports a claim."""
    research = _require(engines.research, "research")
    query = request.query or f"{request.topic} official documentation"
    research_request = ResearchRequest(
        query=query,
        topic=request.topic,
        category=request.category,
        max_results=request.max_results,
        require_official_sources=request.require_official_sources,
        freshness_policy=FreshnessPolicy(category=KnowledgeCategory.DYNAMIC, max_age_days=7),
    )
    result = research.research(research_request, policy=research_request.freshness_policy)
    authoritative = sum(
        1 for source in result.sources if source.tier <= SourceTier.OFFICIAL_STANDARD
    )
    sufficient = authoritative > 0 and not result.degraded
    return ResearchTopicOutput(
        result=result,
        authoritative_source_count=authoritative,
        sufficient_for_a_claim=sufficient,
        note=(
            "Research failed; no dynamic claim can rest on this."
            if result.degraded
            else (
                "Official sources were retrieved."
                if sufficient
                else "No official source was retrieved; treat any dynamic claim as unverified."
            )
        ),
    )


def search_architecture_knowledge(
    request: SearchKnowledgeInput, engines: Engines
) -> SearchKnowledgeOutput:
    """Search the local knowledge base."""
    knowledge = _require(engines.knowledge, "knowledge search")
    result = knowledge.retrieve(query=request.query, topic=request.topic, limit=request.limit)
    return SearchKnowledgeOutput(
        citations=result.citations(),
        excerpts=tuple(chunk.text[:1_000] for chunk in result.chunks),
        best_confidence=result.best_confidence,
        degraded=result.degraded,
        degradation_reason=result.degradation_reason,
    )


def update_knowledge(request: UpdateKnowledgeInput, engines: Engines) -> UpdateKnowledgeOutput:
    """Ingest documents into the knowledge base, skipping unchanged content."""
    knowledge = _require(engines.knowledge, "knowledge update")
    from architecture_agent.knowledge.documents import KnowledgeDocument

    documents: list[KnowledgeDocument] = []
    for index, url in enumerate(request.urls):
        body = (
            request.documents[index]
            if index < len(request.documents)
            else (_fetch_body(engines, url) or "")
        )
        documents.append(
            KnowledgeDocument(
                url=url,
                title=url,
                text=body,
                tier=SourceTier.COMMUNITY,
                confidence=ConfidenceLevel.LOW,
                category=KnowledgeCategory.STABLE,
                topic=request.topic,
            )
        )

    report = knowledge.ingest(tuple(documents))
    return UpdateKnowledgeOutput(
        ingested=report.ingested,
        skipped_unchanged=report.skipped_unchanged,
        rejected=report.rejected,
        chunks_written=report.chunks_written,
        stats=knowledge.stats(),
        reasons=report.reasons,
    )


def _fetch_body(engines: Engines, url: str) -> str | None:
    """Fetch a body through the research service when one is configured."""
    from architecture_agent.application.research_service import PageFetcher, ResearchError

    if engines.research is None:
        return None
    fetcher: PageFetcher | None = getattr(engines.research, "fetcher", None)
    if fetcher is None:
        return None
    try:
        return fetcher.fetch(url, max_chars=20_000)
    except (ResearchError, Exception):
        return None


# --------------------------------------------------------------------------- cost


def estimate_cloud_cost(
    request: EstimateCloudCostInput, engines: Engines
) -> EstimateCloudCostOutput:
    """Estimate cost from retrieved prices, or report why it cannot."""
    engine = _require(engines.cost, "cost estimation")
    evidence, research_result = _evidence_for(engines.research, request.provider)
    prices = PriceBook(
        quotes=tuple(request.prices),
        as_of=request.prices_as_of,
    )
    estimate, outcome, gaps = engine.estimate(
        design=request.design,
        prices=prices,
        usage_assumptions=request.usage_assumptions,
        evidence=evidence.render(),
    )
    unpriced = tuple(gap.render() for gap in gaps)
    fields = _reasoning_fields(outcome, evidence)
    if research_result is not None:
        fields["degraded"] = fields["degraded"] or research_result.degraded
        if research_result.degradation_reason:
            fields["degradation_reason"] = research_result.degradation_reason
    return EstimateCloudCostOutput(estimate=estimate, unpriced=unpriced, **fields)


def analyze_budget(request: AnalyzeBudgetInput, engines: Engines) -> AnalyzeBudgetOutput:
    """Project an estimate across budget scenarios."""
    from architecture_agent.domain.cost import CostEstimate, Currency

    engine = _require(engines.cost, "budget analysis")
    baseline = CostEstimate(
        title=request.title,
        currency=Currency(str(request.currency_components[0].get("currency", "USD"))),
        components=tuple(request.currency_components),  # type: ignore[arg-type]
        assumptions=request.assumptions,
    )
    lines = engine.budget_scenarios(baseline=baseline, scenarios=request.scenarios)
    return AnalyzeBudgetOutput(
        estimate=baseline,
        lines=lines,
        note=(
            "These are projections of an estimate built from retrieved prices, not quotes. "
            "Cloud prices are dynamic; re-price before committing budget."
        ),
    )


__all__ = [
    "Engines",
    "Requirement",
    "analyze_architecture",
    "analyze_budget",
    "compare_cloud_services",
    "design_architecture",
    "design_cloud_architecture",
    "design_deployment",
    "design_infrastructure",
    "estimate_cloud_cost",
    "generate_adr",
    "research_topic",
    "review_architecture",
    "search_architecture_knowledge",
    "update_knowledge",
]
