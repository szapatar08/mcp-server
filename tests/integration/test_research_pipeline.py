"""Research, guard and engine integration against the fake providers."""

from __future__ import annotations

import json

import pytest

from architecture_agent.application.architecture_service import (
    ArchitectureEngine,
    Requirement,
)
from architecture_agent.application.cost_service import CostEngine, PriceBook, PriceQuote
from architecture_agent.application.errors import InvalidInputError, UnsupportedRequestError
from architecture_agent.application.research_service import (
    FetchBlockedError,
    GuardedFetcher,
    ResearchService,
)
from architecture_agent.domain.research import (
    KnowledgeCategory,
    ResearchRequest,
    SourceTier,
    utc_now,
)
from architecture_agent.infrastructure.llm.fake_client import FakeLLMClient
from architecture_agent.infrastructure.search.fake_client import (
    FakePageFetcher,
    FakeSearchClient,
    hostile_url,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def research() -> ResearchService:
    return ResearchService(search=FakeSearchClient(), fetcher=FakePageFetcher())


def _request(**overrides: object) -> ResearchRequest:
    base: dict[str, object] = {
        "query": "aws lambda pricing",
        "topic": "lambda-pricing",
        "category": KnowledgeCategory.DYNAMIC,
        "max_results": 6,
    }
    base.update(overrides)
    return ResearchRequest(**base)


class TestResearch:
    """Classification, degradation and evidence collection."""

    def test_authority_comes_from_provenance_not_rank(self, research: ResearchService) -> None:
        result = research.research(_request())

        tiers = {source.tier for source in result.sources}
        assert SourceTier.OFFICIAL_DOCUMENTATION in tiers
        assert any(source.tier is SourceTier.COMMUNITY for source in result.sources)
        official_first = [s.tier <= SourceTier.OFFICIAL_STANDARD for s in result.sources]
        assert result.best_confidence is not None
        assert official_first  # at least one authoritative source was collected

    def test_official_only_research_excludes_community(self, research: ResearchService) -> None:
        result = research.research(_request(require_official_sources=True))

        assert result.sources
        assert all(source.tier <= SourceTier.OFFICIAL_STANDARD for source in result.sources)

    def test_a_failing_provider_produces_a_degraded_result(self, research: ResearchService) -> None:
        failing = ResearchService(search=FakeSearchClient(fail=True), fetcher=FakePageFetcher())
        result = failing.research(_request())

        assert result.degraded is True
        assert "duckduckgo" in (result.degradation_reason or "") or "failed" in (
            result.degradation_reason or ""
        )
        assert result.sources == ()

    def test_evidence_is_guarded_and_the_hostile_page_is_reported(
        self, research: ResearchService
    ) -> None:
        _result, evidence = research.research_with_evidence(_request())

        assert evidence.is_clean is False
        rendered = evidence.render()
        assert "EVIDENCE" in rendered
        assert hostile_url() in rendered
        assert "neutralised" in rendered

    def test_a_guarded_block_keeps_the_url_for_citation(self, research: ResearchService) -> None:
        result, evidence = research.research_with_evidence(_request())

        urls = {block.url for block in evidence.blocks}
        assert urls <= {str(source.url) for source in result.sources}


class TestEgressGuard:
    """T5: the allowlist is enforced before any fetch."""

    def test_an_unrestricted_configuration_permits_every_host(self) -> None:
        from architecture_agent.config.settings import SecuritySettings

        guarded = GuardedFetcher(FakePageFetcher(), SecuritySettings())

        assert guarded.fetch("https://attacker.test/lambda-pricing-cheap", max_chars=100)

    def test_a_blocked_host_is_refused(self) -> None:
        from architecture_agent.config.settings import SecuritySettings

        guarded = GuardedFetcher(
            FakePageFetcher(), SecuritySettings(allowed_domains=("docs.aws.amazon.com",))
        )

        with pytest.raises(FetchBlockedError, match="ALLOWED_DOMAINS"):
            guarded.fetch("https://attacker.test/lambda-pricing-cheap", max_chars=100)

        assert guarded.fetch("https://docs.aws.amazon.com/lambda/pricing/", max_chars=100)

    def test_a_url_without_a_host_is_refused(self) -> None:
        from architecture_agent.config.settings import SecuritySettings

        guarded = GuardedFetcher(FakePageFetcher(), SecuritySettings())

        with pytest.raises(FetchBlockedError, match="no host"):
            guarded.fetch("not-a-url", max_chars=100)


class TestEngineWithEvidence:
    """The engine receives evidence as data and reports injection attempts."""

    def test_a_design_receives_the_evidence_block(self) -> None:
        research = ResearchService(search=FakeSearchClient(), fetcher=FakePageFetcher())
        _result, evidence = research.research_with_evidence(_request())

        captured: list[str] = []

        def capture(request: object) -> str:
            captured.append(
                str(getattr(request, "system", "")) + str(getattr(request, "messages", ""))
            )
            return json.dumps(
                {
                    "result": {
                        "architecture": {
                            "name": "Lambda consumer",
                            "summary": "An architecture derived from the retrieved evidence.",
                            "components": [
                                {
                                    "name": "worker",
                                    "kind": "service",
                                    "responsibility": "Consumes queued jobs.",
                                }
                            ],
                        }
                    },
                    "reasoning_summary": "Priced from retrieved documentation.",
                    "confidence": "medium",
                }
            )

        engine = ArchitectureEngine(FakeLLMClient(answers=[capture]))
        proposal, outcome = engine.design(
            requirements=(Requirement(id="R1", description="Consume queued jobs."),),
            evidence=evidence.render(),
        )

        assert proposal.architecture.name == "Lambda consumer"
        assert "EVIDENCE" in captured[0]
        assert outcome.confidence.value == "medium"

    def test_design_requires_requirements(self) -> None:
        engine = ArchitectureEngine(FakeLLMClient())

        with pytest.raises(InvalidInputError, match="at least one requirement"):
            engine.design(requirements=())


class TestCostRefusal:
    """A total must never rest on an unsourced figure."""

    def test_no_prices_is_a_refusal(self) -> None:
        with pytest.raises(InvalidInputError, match="no retrieved prices"):
            CostEngine(FakeLLMClient()).estimate(
                design="Three EC2 instances" * 5, prices=PriceBook()
            )

    def test_unmatched_services_are_reported_not_priced(self) -> None:
        prices = PriceBook(
            quotes=(
                PriceQuote(
                    provider="aws",
                    service="Athena",
                    region="eu-west-1",
                    unit="TB scanned",
                    unit_price="5",
                    tier=SourceTier.OFFICIAL_DOCUMENTATION,
                ),
            ),
            as_of=utc_now().isoformat(),
        )
        answer = json.dumps(
            {
                "result": {
                    "components": [
                        {
                            "name": "app",
                            "service": "EC2",
                            "region": "us-east-1",
                            "quantity": "3",
                        }
                    ],
                    "assumptions": ["3 instances"],
                }
            }
        )

        with pytest.raises(InvalidInputError, match="could be priced"):
            CostEngine(FakeLLMClient(answers=[answer])).estimate(
                design="A design long enough to satisfy the validation rule.",
                prices=prices,
            )

    def test_a_quoted_component_carries_its_reference(self) -> None:
        prices = PriceBook(
            quotes=(
                PriceQuote(
                    provider="aws",
                    service="EC2",
                    region="us-east-1",
                    unit="vCPU-hour",
                    unit_price="0.0416",
                    tier=SourceTier.OFFICIAL_DOCUMENTATION,
                    source_url="https://aws.amazon.com/ec2/pricing/",
                ),
            ),
            as_of=utc_now().isoformat(),
        )
        answer = json.dumps(
            {
                "result": {
                    "components": [
                        {
                            "name": "app",
                            "category": "compute",
                            "service": "EC2",
                            "region": "us-east-1",
                            "quantity": "3",
                        }
                    ],
                    "assumptions": ["3 t4g instances, always on"],
                    "cost_drivers": ["compute"],
                },
                "confidence": "high",
            }
        )
        estimate, _outcome, gaps = CostEngine(FakeLLMClient(answers=[answer])).estimate(
            design="Three EC2 instances for the API tier.", prices=prices
        )

        assert estimate.monthly_total().quantize(estimate.monthly_total()) >= 0
        assert estimate.pricing_references
        assert estimate.pricing_references[0].tier is SourceTier.OFFICIAL_DOCUMENTATION
        assert gaps == ()


class TestUnavailableEngines:
    """An engine that is not configured must explain itself, not crash."""

    def test_a_missing_engine_is_reported_clearly(self) -> None:
        from architecture_agent.tools.engine_tools import _require

        with pytest.raises(UnsupportedRequestError, match="not configured"):
            _require(None, "architecture design")
