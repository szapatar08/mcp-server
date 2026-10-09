"""Web research: the search port, the fetch port, and the research use case.

Ports are declared here because the use case needs them. DuckDuckGo and the HTTP
fetcher live in ``infrastructure/``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlparse

from pydantic import HttpUrl

from architecture_agent.application.errors import ApplicationError
from architecture_agent.application.guard import GuardedEvidence, UntrustedContentGuard
from architecture_agent.config.settings import SecuritySettings, Settings
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


class ResearchError(ApplicationError):
    """Research could not be completed."""

    code = "research_failed"


class FetchBlockedError(ResearchError):
    """A URL outside the configured allowlist was requested."""

    code = "fetch_blocked"


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One raw search result, before classification."""

    url: str
    title: str
    snippet: str
    rank: int


class SearchClient(Protocol):
    """Finds candidate URLs for a query."""

    provider: str

    def search(self, query: str, *, max_results: int) -> tuple[SearchHit, ...]:
        """Return ranked hits. Rank is advisory and never affects authority."""
        ...


class PageFetcher(Protocol):
    """Retrieves a document's text."""

    def fetch(self, url: str, *, max_chars: int) -> str:
        """Return the page text.

        Raises:
            FetchBlockedError: if the host is not allowed.
        """
        ...


class GuardedFetcher:
    """A fetcher that enforces the egress allowlist before any network call."""

    def __init__(self, inner: PageFetcher, security: SecuritySettings) -> None:
        """Wrap ``inner`` with the allowlist check."""
        self._inner = inner
        self._security = security

    def fetch(self, url: str, *, max_chars: int) -> str:
        """Fetch ``url`` if its host is permitted."""
        host = (urlparse(url).hostname or "").lower()
        if not host:
            msg = f"cannot fetch {url!r}: no host in the URL"
            raise FetchBlockedError(msg)
        if not self._security.is_domain_allowed(host):
            msg = f"refusing to fetch {host!r}: not in ARCH_AGENT_SECURITY__ALLOWED_DOMAINS"
            raise FetchBlockedError(msg)
        return self._inner.fetch(url, max_chars=max_chars)


class ResearchService:
    """Search, classify, fetch and guard retrieved content."""

    def __init__(
        self,
        *,
        search: SearchClient,
        fetcher: PageFetcher,
        classifier: TierClassifier | None = None,
        guard: UntrustedContentGuard | None = None,
    ) -> None:
        """Build the service.

        Args:
            search: Source of candidate URLs.
            fetcher: Retrieves page text, already wrapped in an allowlist guard.
            classifier: Source trust classifier.
            guard: Untrusted content guard.
        """
        self._search = search
        self._fetcher = fetcher
        self._classifier = classifier or TierClassifier()
        self._guard = guard or UntrustedContentGuard()

    @property
    def provider(self) -> str:
        """The configured search provider."""
        return self._search.provider

    def research(
        self, request: ResearchRequest, *, policy: FreshnessPolicy | None = None
    ) -> ResearchResult:
        """Run a research request.

        Failure is a first-class outcome: a provider that fails produces a
        ``degraded`` result rather than an exception, because "the search provider
        was down" and "no information exists" are different answers.
        """
        try:
            hits = self._search.search(request.query, max_results=request.max_results)
        except Exception as error:
            return ResearchResult(
                request=request,
                degraded=True,
                degradation_reason=(
                    f"the {self._search.provider} search provider failed: {type(error).__name__}"
                ),
            )

        sources: list[SourceDocument] = []
        corroborating = 0
        for hit in hits:
            tier = self._classifier.classify(hit.url)
            confidence = assess_confidence(tier, corroborating)
            if request.require_official_sources and tier > SourceTier.OFFICIAL_STANDARD:
                continue
            body = self._try_fetch(hit.url, policy)
            corroborating += 1
            sources.append(
                SourceDocument(
                    url=HttpUrl(hit.url),
                    title=hit.title or hit.url,
                    tier=tier,
                    confidence=confidence,
                    category=request.category,
                    topic=request.topic,
                    retrieved_at=utc_now(),
                    content_hash=content_hash(body) if body else None,
                    provider=self._search.provider,
                    excerpt=hit.snippet[:4_000] or None,
                )
            )
            if policy is not None and policy.is_stale(utc_now()):
                break

        return ResearchResult(
            request=request,
            sources=tuple(sources),
            summary=self._summarise(sources),
            searched_at=utc_now(),
        )

    def research_with_evidence(
        self, request: ResearchRequest
    ) -> tuple[ResearchResult, GuardedEvidence]:
        """Research and return guarded page text ready for a prompt."""
        try:
            hits = self._search.search(request.query, max_results=request.max_results)
        except Exception as error:
            return (
                ResearchResult(
                    request=request,
                    degraded=True,
                    degradation_reason=(
                        f"the {self._search.provider} search provider failed: "
                        f"{type(error).__name__}"
                    ),
                ),
                GuardedEvidence(),
            )

        documents: list[SourceDocument] = []
        guarded: list[tuple[str, str, SourceTier, ConfidenceLevel]] = []
        corroborating = 0

        for hit in hits:
            tier = self._classifier.classify(hit.url)
            if request.require_official_sources and tier > SourceTier.OFFICIAL_STANDARD:
                continue
            confidence = assess_confidence(tier, corroborating)
            corroborating += 1
            body = self._try_fetch(hit.url)
            documents.append(
                SourceDocument(
                    url=HttpUrl(hit.url),
                    title=hit.title or hit.url,
                    tier=tier,
                    confidence=confidence,
                    category=request.category,
                    topic=request.topic,
                    retrieved_at=utc_now(),
                    content_hash=content_hash(body) if body else None,
                    provider=self._search.provider,
                )
            )
            if body:
                guarded.append((hit.url, body, tier, confidence))

        result = ResearchResult(
            request=request,
            sources=tuple(documents),
            summary=self._summarise(documents),
            searched_at=utc_now(),
        )
        return result, self._guard.guard_many(tuple(guarded))

    def _try_fetch(self, url: str, policy: FreshnessPolicy | None = None) -> str:
        """Fetch a page, returning empty text on failure rather than raising."""
        del policy
        try:
            return self._fetcher.fetch(url, max_chars=20_000)
        except Exception:
            return ""

    @staticmethod
    def _summarise(sources: list[SourceDocument]) -> str:
        """Describe the evidence collected without interpreting it."""
        if not sources:
            return "No sources were retrieved."
        authoritative = sum(1 for source in sources if source.tier <= SourceTier.OFFICIAL_STANDARD)
        return (
            f"{len(sources)} source(s) retrieved, {authoritative} at official "
            f"documentation or standard level."
        )


class InMemoryResearchService(ResearchService):
    """A research service with no provider at all.

    Used when ``SEARCH__PROVIDER=fake`` and in tests that need deterministic
    evidence. Returns a fixed source set so the rest of the pipeline is exercisable
    without a network.
    """

    def __init__(self, documents: tuple[SourceDocument, ...] = ()) -> None:
        """Build a service returning ``documents``."""
        from architecture_agent.infrastructure.search.fake_client import (
            FakePageFetcher,
            FakeSearchClient,
        )

        super().__init__(search=FakeSearchClient(), fetcher=FakePageFetcher())
        self._documents = documents


def dynamic_policy(days: int = 7) -> FreshnessPolicy:
    """A freshness policy for facts that change, such as prices and versions."""
    return FreshnessPolicy(category=KnowledgeCategory.DYNAMIC, max_age_days=days)


def build_research_service(settings: Settings) -> ResearchService:
    """Construct the research service from configuration.

    The composition root's only knowledge of search vendors.
    """
    from architecture_agent.infrastructure.search.duckduckgo_client import build_search_client
    from architecture_agent.infrastructure.search.http_fetcher import HttpPageFetcher

    return ResearchService(
        search=build_search_client(settings.search),
        fetcher=GuardedFetcher(HttpPageFetcher(), settings.security),
    )


def stable_hash(text: str) -> str:
    """A short, stable identifier for a piece of text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def utc_timestamp() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(tz=UTC)


__all__ = [
    "FetchBlockedError",
    "FreshnessPolicy",
    "GuardedEvidence",
    "GuardedFetcher",
    "InMemoryResearchService",
    "PageFetcher",
    "ResearchError",
    "ResearchResult",
    "ResearchService",
    "SearchClient",
    "SearchHit",
    "build_research_service",
    "dynamic_policy",
    "utc_timestamp",
]
