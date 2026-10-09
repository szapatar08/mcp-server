"""Source trust classification, freshness and research provenance (FR-017 to FR-019)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from architecture_agent.domain.research import (
    ConfidenceLevel,
    FreshnessPolicy,
    KnowledgeCategory,
    ResearchRequest,
    ResearchResult,
    SourceDocument,
    SourceTier,
    TierClassifier,
    assess_confidence,
    content_hash,
    utc_now,
)

pytestmark = pytest.mark.unit


class TestTierClassification:
    """FR-019: authority comes from provenance, never from search ranking."""

    @pytest.fixture
    def classifier(self) -> TierClassifier:
        return TierClassifier()

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (
                "https://docs.aws.amazon.com/AmazonEC2/latest/UserGuide/ec2-vpc.html",
                SourceTier.OFFICIAL_DOCUMENTATION,
            ),
            ("https://kubernetes.io/docs/concepts/overview/", SourceTier.OFFICIAL_DOCUMENTATION),
            (
                "https://learn.microsoft.com/en-us/azure/architecture/",
                SourceTier.OFFICIAL_DOCUMENTATION,
            ),
            ("https://cloud.google.com/compute/docs", SourceTier.OFFICIAL_DOCUMENTATION),
        ],
    )
    def test_vendor_documentation_is_top_tier(
        self, classifier: TierClassifier, url: str, expected: SourceTier
    ) -> None:
        assert classifier.classify(url) is expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://csrc.nist.gov/pubs/sp/800/53/r5/final",
            "https://www.rfc-editor.org/rfc/rfc9110.html",
            "https://nvd.nist.gov/vuln/detail/CVE-2026-1234",
        ],
    )
    def test_standards_bodies_are_second_tier(self, classifier: TierClassifier, url: str) -> None:
        assert classifier.classify(url) is SourceTier.OFFICIAL_STANDARD

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://github.com/kubernetes/kubernetes", SourceTier.OFFICIAL_REPOSITORY),
            ("https://github.com/aws/aws-sdk-go-v2", SourceTier.OFFICIAL_REPOSITORY),
            ("https://github.com/some-random-user/homebrew-tap", SourceTier.COMMUNITY),
            ("https://gitlab.com/nginx/nginx", SourceTier.OFFICIAL_REPOSITORY),
        ],
    )
    def test_repositories_are_official_only_for_known_owners(
        self, classifier: TierClassifier, url: str, expected: SourceTier
    ) -> None:
        assert classifier.classify(url) is expected

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://netflixtechblog.com/...", SourceTier.OFFICIAL_ENGINEERING_BLOG),
            ("https://owasp.org/www-project-top-ten/", SourceTier.RECOGNIZED_ORGANIZATION),
            (
                "https://cheatsheetseries.owasp.org/cheatsheets/...",
                SourceTier.RECOGNIZED_ORGANIZATION,
            ),
            ("https://www.infoq.com/articles/...", SourceTier.QUALIFIED_PUBLICATION),
        ],
    )
    def test_secondary_sources_are_ranked_below_documentation(
        self, classifier: TierClassifier, url: str, expected: SourceTier
    ) -> None:
        assert classifier.classify(url) is expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://stackoverflow.com/questions/1",
            "https://random-personal-blog.test/post",
            "https://medium.com/@someone/designing-at-scale",
            "not-a-url",
        ],
    )
    def test_unknown_hosts_fall_back_to_community(
        self, classifier: TierClassifier, url: str
    ) -> None:
        assert classifier.classify(url) is SourceTier.COMMUNITY

    def test_operator_configured_hosts_are_trusted(self) -> None:
        classifier = TierClassifier(extra_official_hosts=frozenset({"internal.wiki"}))

        assert (
            classifier.classify("https://internal.wiki/arch") is SourceTier.OFFICIAL_DOCUMENTATION
        )

    def test_classification_ignores_case_and_www_prefix(self, classifier: TierClassifier) -> None:
        assert (
            classifier.classify("https://WWW.Kubernetes.io/docs/")
            is SourceTier.OFFICIAL_DOCUMENTATION
        )

    def test_every_tier_has_a_label(self) -> None:
        labels = {tier.label for tier in SourceTier}

        assert len(labels) == len(SourceTier)


class TestConfidence:
    """Confidence requires authority *and* corroboration for weak sources."""

    def test_standards_are_always_high_confidence(self) -> None:
        assert assess_confidence(SourceTier.OFFICIAL_STANDARD) is ConfidenceLevel.HIGH

    def test_single_official_doc_is_high_confidence(self) -> None:
        assert assess_confidence(SourceTier.OFFICIAL_DOCUMENTATION) is ConfidenceLevel.HIGH

    def test_single_unofficial_repository_is_medium(self) -> None:
        assert assess_confidence(SourceTier.OFFICIAL_REPOSITORY) is ConfidenceLevel.MEDIUM
        assert (
            assess_confidence(SourceTier.OFFICIAL_REPOSITORY, corroborating_sources=1)
            is ConfidenceLevel.HIGH
        )

    def test_two_vendor_blogs_are_high_confidence_but_one_is_not(self) -> None:
        assert assess_confidence(SourceTier.OFFICIAL_ENGINEERING_BLOG) is ConfidenceLevel.MEDIUM
        assert (
            assess_confidence(SourceTier.OFFICIAL_ENGINEERING_BLOG, corroborating_sources=2)
            is ConfidenceLevel.HIGH
        )

    def test_an_uncorroborated_community_source_is_speculative(self) -> None:
        assert assess_confidence(SourceTier.COMMUNITY) is ConfidenceLevel.SPECULATIVE
        assert (
            assess_confidence(SourceTier.COMMUNITY, corroborating_sources=2) is ConfidenceLevel.LOW
        )


class TestFreshnessPolicy:
    """Dynamic knowledge must expire; stable knowledge must not churn."""

    def test_dynamic_documents_become_stale(self) -> None:
        policy = FreshnessPolicy(category=KnowledgeCategory.DYNAMIC, max_age_days=7)

        assert policy.is_stale(utc_now() - timedelta(days=8)) is True
        assert policy.is_stale(utc_now() - timedelta(days=1)) is False

    def test_comparison_requires_timezone_aware_datetimes(self) -> None:
        from datetime import datetime

        policy = FreshnessPolicy(category=KnowledgeCategory.DYNAMIC, max_age_days=7)

        with pytest.raises(ValueError, match="timezone-aware"):
            policy.is_stale(datetime(2026, 1, 1))  # noqa: DTZ001

    def test_age_is_measurable(self) -> None:
        policy = FreshnessPolicy(category=KnowledgeCategory.STABLE, max_age_days=365)

        assert policy.age_of(utc_now() - timedelta(days=2)).days == 2

    def test_policy_category_must_match_the_request(self) -> None:
        with pytest.raises(ValidationError, match="must match the request category"):
            ResearchRequest(
                query="aws ec2 pricing",
                topic="pricing",
                category=KnowledgeCategory.DYNAMIC,
                freshness_policy=FreshnessPolicy(
                    category=KnowledgeCategory.STABLE, max_age_days=30
                ),
            )


class TestSourceDocument:
    """Provenance metadata is mandatory and validated."""

    @pytest.fixture
    def source(self) -> SourceDocument:
        return SourceDocument(
            url="https://docs.aws.amazon.com/ec2/pricing",
            title="Amazon EC2 On-Demand Pricing",
            tier=SourceTier.OFFICIAL_DOCUMENTATION,
            confidence=ConfidenceLevel.HIGH,
            category=KnowledgeCategory.DYNAMIC,
            topic="pricing",
            retrieved_at=utc_now(),
            content_hash=content_hash("page body"),
            provider="duckduckgo",
        )

    def test_provenance_is_carried(self, source: SourceDocument) -> None:
        assert source.host == "docs.aws.amazon.com"
        assert source.content_hash == content_hash("page body")

    def test_timestamps_must_be_timezone_aware(self) -> None:
        from datetime import datetime

        with pytest.raises(ValidationError, match="timezone-aware"):
            SourceDocument(
                url="https://docs.aws.amazon.com/ec2",
                title="Unaware retrieval timestamp",
                tier=SourceTier.OFFICIAL_DOCUMENTATION,
                confidence=ConfidenceLevel.HIGH,
                category=KnowledgeCategory.DYNAMIC,
                topic="compute",
                retrieved_at=datetime(2026, 1, 1),  # noqa: DTZ001
            )

    @pytest.mark.parametrize("value", ["abc123", "A" * 64, "z" * 64])
    def test_content_hash_must_be_a_sha256_digest(self, value: str) -> None:
        with pytest.raises(ValidationError, match="SHA-256"):
            SourceDocument(
                url="https://docs.aws.amazon.com/ec2",
                title="Invalid content hash",
                tier=SourceTier.OFFICIAL_DOCUMENTATION,
                confidence=ConfidenceLevel.HIGH,
                category=KnowledgeCategory.DYNAMIC,
                topic="compute",
                retrieved_at=utc_now(),
                content_hash=value,
            )

    def test_content_hash_detects_unchanged_documents(self) -> None:
        assert content_hash("same body") == content_hash("same body")
        assert content_hash("same body") != content_hash("changed body")

    def test_staleness_is_evaluated_against_a_policy(self) -> None:
        aged = SourceDocument(
            url="https://docs.aws.amazon.com/ec2/pricing",
            title="A pricing page retrieved a while ago",
            tier=SourceTier.OFFICIAL_DOCUMENTATION,
            confidence=ConfidenceLevel.HIGH,
            category=KnowledgeCategory.DYNAMIC,
            topic="pricing",
            retrieved_at=utc_now() - timedelta(days=10),
        )

        assert aged.is_stale(FreshnessPolicy(category=KnowledgeCategory.DYNAMIC, max_age_days=7))
        assert not aged.is_stale(
            FreshnessPolicy(category=KnowledgeCategory.DYNAMIC, max_age_days=30)
        )


class TestResearchResult:
    """Partial failure must be distinguishable from "no information exists"."""

    def test_a_degraded_result_must_explain_itself(self) -> None:
        request = ResearchRequest(query="lambda pricing 2026", topic="pricing")
        sources = (
            SourceDocument(
                url="https://aws.amazon.com/lambda/pricing/",
                title="AWS Lambda Pricing",
                tier=SourceTier.OFFICIAL_DOCUMENTATION,
                confidence=ConfidenceLevel.HIGH,
                category=KnowledgeCategory.DYNAMIC,
                topic="pricing",
                retrieved_at=utc_now(),
            ),
        )

        with pytest.raises(ValidationError, match="must explain the degradation"):
            ResearchResult(request=request, sources=sources, degraded=True)

        result = ResearchResult(
            request=request,
            sources=sources,
            degraded=True,
            degradation_reason="Search provider rate limited the request after 2 results.",
        )

        assert result.degraded is True

    def test_a_reason_without_degradation_is_incoherent(self) -> None:
        request = ResearchRequest(query="lambda pricing 2026", topic="pricing")

        with pytest.raises(ValidationError, match="requires degraded=True"):
            ResearchResult(request=request, degradation_reason="Something went wrong somewhere.")

    def test_result_size_is_bounded_by_the_request(self) -> None:
        request = ResearchRequest(query="ec2 pricing", topic="pricing", max_results=1)
        sources = tuple(
            SourceDocument(
                url=f"https://docs.aws.amazon.com/ec2/page-{index}",
                title=f"EC2 page {index}",
                tier=SourceTier.OFFICIAL_DOCUMENTATION,
                confidence=ConfidenceLevel.HIGH,
                category=KnowledgeCategory.DYNAMIC,
                topic="pricing",
                retrieved_at=utc_now(),
            )
            for index in range(2)
        )

        with pytest.raises(ValidationError, match="more sources than the request allowed"):
            ResearchResult(request=request, sources=sources)

    def test_confidence_and_authority_are_summarised(self) -> None:
        request = ResearchRequest(query="ec2 vs lambda", topic="compute")
        sources = (
            SourceDocument(
                url="https://random-blog.test/ec2-vs-lambda",
                title="A community opinion",
                tier=SourceTier.COMMUNITY,
                confidence=ConfidenceLevel.SPECULATIVE,
                category=KnowledgeCategory.DYNAMIC,
                topic="compute",
                retrieved_at=utc_now(),
            ),
            SourceDocument(
                url="https://docs.aws.amazon.com/ec2",
                title="EC2 documentation",
                tier=SourceTier.OFFICIAL_DOCUMENTATION,
                confidence=ConfidenceLevel.HIGH,
                category=KnowledgeCategory.DYNAMIC,
                topic="compute",
                retrieved_at=utc_now(),
            ),
        )
        result = ResearchResult(request=request, sources=sources)

        assert result.best_confidence is ConfidenceLevel.HIGH
        assert len(result.authoritative_sources()) == 1

    def test_searched_at_must_be_timezone_aware(self) -> None:
        from datetime import datetime

        with pytest.raises(ValidationError, match="timezone-aware"):
            ResearchResult(
                request=ResearchRequest(query="ec2 pricing", topic="pricing"),
                searched_at=datetime(2026, 1, 1),  # noqa: DTZ001
            )

    def test_authoritative_sources_accepts_a_custom_threshold(self) -> None:
        request = ResearchRequest(query="ecs vs eks", topic="compute")
        sources = (
            SourceDocument(
                url="https://docs.aws.amazon.com/eks",
                title="EKS documentation",
                tier=SourceTier.OFFICIAL_DOCUMENTATION,
                confidence=ConfidenceLevel.HIGH,
                category=KnowledgeCategory.STABLE,
                topic="compute",
                retrieved_at=utc_now(),
            ),
            SourceDocument(
                url="https://owasp.org/container-security/",
                title="Container security guidance",
                tier=SourceTier.RECOGNIZED_ORGANIZATION,
                confidence=ConfidenceLevel.LOW,
                category=KnowledgeCategory.STABLE,
                topic="security",
                retrieved_at=utc_now(),
            ),
        )

        result = ResearchResult(request=request, sources=sources)

        assert len(result.authoritative_sources()) == 1
        assert len(result.authoritative_sources(SourceTier.RECOGNIZED_ORGANIZATION)) == 2

    def test_domain_models_reject_unknown_fields(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            SourceDocument(  # type: ignore[call-arg]
                url="https://docs.aws.amazon.com/ec2",
                title="A document with an unexpected field",
                tier=SourceTier.OFFICIAL_DOCUMENTATION,
                confidence=ConfidenceLevel.HIGH,
                category=KnowledgeCategory.STABLE,
                topic="compute",
                retrieved_at=utc_now(),
                trust_level="high",
            )

    def test_an_empty_result_is_speculative(self) -> None:
        result = ResearchResult(request=ResearchRequest(query="obscure topic", topic="misc"))

        assert result.best_confidence is ConfidenceLevel.SPECULATIVE
        assert result.authoritative_sources() == ()
