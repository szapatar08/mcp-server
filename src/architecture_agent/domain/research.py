"""Research, source provenance, knowledge freshness and trust classification.

Retrieved web content is untrusted input. These models exist so that every claim
the agent makes can be traced back to a classified source with a retrieval
timestamp, and so that stale dynamic knowledge is visibly marked as stale.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from enum import IntEnum, StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(tz=UTC)


class KnowledgeCategory(StrEnum):
    """Whether a fact is unlikely or likely to change over time."""

    STABLE = "stable"
    DYNAMIC = "dynamic"


class ConfidenceLevel(StrEnum):
    """How much weight a piece of evidence may carry."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    SPECULATIVE = "speculative"


class SourceTier(IntEnum):
    """Authority ranking of a source, ordered from most to least trusted.

    Search engine ranking is deliberately not an input: a well-ranked blog post
    is not authoritative documentation.
    """

    OFFICIAL_DOCUMENTATION = 1
    OFFICIAL_STANDARD = 2
    OFFICIAL_REPOSITORY = 3
    OFFICIAL_ENGINEERING_BLOG = 4
    RECOGNIZED_ORGANIZATION = 5
    QUALIFIED_PUBLICATION = 6
    COMMUNITY = 7

    @property
    def label(self) -> str:
        """Human readable description of the tier."""
        return _TIER_LABELS[self]


_TIER_LABELS: dict[SourceTier, str] = {
    SourceTier.OFFICIAL_DOCUMENTATION: "Official documentation",
    SourceTier.OFFICIAL_STANDARD: "Official standard or government publication",
    SourceTier.OFFICIAL_REPOSITORY: "Official source repository",
    SourceTier.OFFICIAL_ENGINEERING_BLOG: "Vendor engineering blog",
    SourceTier.RECOGNIZED_ORGANIZATION: "Recognized technical organization",
    SourceTier.QUALIFIED_PUBLICATION: "Qualified technical publication",
    SourceTier.COMMUNITY: "Community source",
}

_DOC_HOSTS: frozenset[str] = frozenset(
    {
        "aws.amazon.com",
        "docs.aws.amazon.com",
        "azure.microsoft.com",
        "learn.microsoft.com",
        "docs.microsoft.com",
        "cloud.google.com",
        "docs.cloud.google.com",
        "kubernetes.io",
        "docs.docker.com",
        "postgresql.org",
        "dev.mysql.com",
        "redis.io",
        "nginx.org",
        "istio.io",
        "opentelemetry.io",
        "prometheus.io",
        "grafana.com",
        "docs.python.org",
        "developer.mozilla.org",
        "docs.github.com",
    }
)

_STANDARD_HOSTS: frozenset[str] = frozenset(
    {
        "www.rfc-editor.org",
        "rfc-editor.org",
        "www.ietf.org",
        "datatracker.ietf.org",
        "www.nist.gov",
        "csrc.nist.gov",
        "www.iso.org",
        "www.w3.org",
        "docs.oasis-open.org",
        "www.oasis-open.org",
        "www.cisa.gov",
        "nvd.nist.gov",
        "www.cve.org",
    }
)

_VENDOR_BLOG_HOSTS: frozenset[str] = frozenset(
    {
        "aws.amazon.com",
        "cloud.google.com",
        "azure.microsoft.com",
        "netflixtechblog.com",
        "blog.cloudflare.com",
        "engineering.fb.com",
        "testing.googleblog.com",
        "kubernetes.io",
    }
)

_ORGANIZATION_HOSTS: frozenset[str] = frozenset(
    {
        "owasp.org",
        "cheatsheetseries.owasp.org",
        "www.cncf.io",
        "cncf.io",
        "martinfowler.com",
        "www.thoughtworks.com",
        "sre.google",
        "www.ieee.org",
        "www.paloaltonetworks.com",
        "unit42.paloaltonetworks.com",
    }
)

_PUBLICATION_HOSTS: frozenset[str] = frozenset(
    {
        "www.infoq.com",
        "infoq.com",
        "thenewstack.io",
        "www.oreilly.com",
        "lwn.net",
    }
)

_OFFICIAL_REPOSITORY_OWNERS: frozenset[str] = frozenset(
    {
        "aws",
        "azure",
        "microsoft",
        "googleapis",
        "google-cloud-platform",
        "kubernetes",
        "docker",
        "nginx",
        "redis",
        "postgres",
        "apache",
        "hashicorp",
        "terraform-providers",
        "open-telemetry",
        "cncf",
        "istio",
        "prometheus",
        "grafana",
        "python",
        "pydantic",
        "langchain-ai",
    }
)


class TierClassifier:
    """Classifies URLs into :class:`SourceTier` values.

    Classification is host based and deliberately conservative: an unrecognised
    host falls back to :attr:`SourceTier.COMMUNITY` rather than being promoted.
    GitHub repositories are only treated as official when the owning
    organisation is a known vendor or upstream project.
    """

    def __init__(self, extra_official_hosts: frozenset[str] = frozenset()) -> None:
        """Create a classifier, optionally trusting extra documentation hosts."""
        self._extra_official_hosts = frozenset(h.lower().lstrip(".") for h in extra_official_hosts)

    def classify(self, url: str | HttpUrl) -> SourceTier:
        """Return the trust tier for ``url``."""
        normalised = self._normalise(url)
        host = (urlparse(normalised).hostname or "").lower()
        if not host:
            return SourceTier.COMMUNITY

        registrable = host[4:] if host.startswith("www.") else host

        if self._matches(registrable, self._extra_official_hosts | _DOC_HOSTS):
            return SourceTier.OFFICIAL_DOCUMENTATION
        if self._matches(registrable, _STANDARD_HOSTS):
            return SourceTier.OFFICIAL_STANDARD
        if registrable in {"github.com", "gitlab.com", "bitbucket.org"}:
            return self._classify_repository(normalised)
        if self._matches(registrable, _VENDOR_BLOG_HOSTS):
            return SourceTier.OFFICIAL_ENGINEERING_BLOG
        if self._matches(registrable, _ORGANIZATION_HOSTS):
            return SourceTier.RECOGNIZED_ORGANIZATION
        if self._matches(registrable, _PUBLICATION_HOSTS):
            return SourceTier.QUALIFIED_PUBLICATION
        return SourceTier.COMMUNITY

    def _classify_repository(self, normalised_url: str) -> SourceTier:
        path = urlparse(normalised_url).path.strip("/")
        parts = [segment for segment in path.split("/") if segment]
        owner = parts[0].lower() if parts else ""
        if owner and owner in _OFFICIAL_REPOSITORY_OWNERS:
            return SourceTier.OFFICIAL_REPOSITORY
        return SourceTier.COMMUNITY

    @staticmethod
    def _normalise(value: str | HttpUrl) -> str:
        text = str(value)
        return text if "://" in text else f"https://{text}"

    @staticmethod
    def _matches(host: str, candidates: frozenset[str]) -> bool:
        return any(host == candidate or host.endswith(f".{candidate}") for candidate in candidates)


DEFAULT_CLASSIFIER = TierClassifier()


def assess_confidence(tier: SourceTier, corroborating_sources: int = 0) -> ConfidenceLevel:
    """Derive a confidence level from source tier and corroboration breadth.

    A single community blog post is never high confidence. Two independent
    sources at least at ``OFFICIAL_ENGINEERING_BLOG`` level are.
    """
    if tier <= SourceTier.OFFICIAL_STANDARD:
        return ConfidenceLevel.HIGH
    if tier <= SourceTier.OFFICIAL_REPOSITORY:
        return ConfidenceLevel.HIGH if corroborating_sources >= 1 else ConfidenceLevel.MEDIUM
    if tier is SourceTier.OFFICIAL_ENGINEERING_BLOG:
        return ConfidenceLevel.HIGH if corroborating_sources >= 2 else ConfidenceLevel.MEDIUM
    if tier <= SourceTier.QUALIFIED_PUBLICATION:
        return ConfidenceLevel.MEDIUM if corroborating_sources >= 1 else ConfidenceLevel.LOW
    return ConfidenceLevel.LOW if corroborating_sources >= 2 else ConfidenceLevel.SPECULATIVE


def content_hash(content: str) -> str:
    """Return the SHA-256 hex digest used to detect unchanged documents."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class FreshnessPolicy(BaseModel):
    """How long a knowledge category stays trustworthy before re-ingestion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: KnowledgeCategory
    max_age_days: int = Field(ge=1, le=3_650)

    def age_of(self, retrieved_at: datetime) -> timedelta:
        """Return the age of ``retrieved_at`` relative to ``now``."""
        return utc_now() - retrieved_at

    def is_stale(self, retrieved_at: datetime, *, now: datetime | None = None) -> bool:
        """Whether a document retrieved at ``retrieved_at`` needs refreshing."""
        reference = now or utc_now()
        if reference.tzinfo is None or retrieved_at.tzinfo is None:
            msg = "freshness comparisons require timezone-aware datetimes"
            raise ValueError(msg)
        return reference - retrieved_at > timedelta(days=self.max_age_days)


class SourceDocument(BaseModel):
    """A single retrieved source with full provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    url: HttpUrl
    title: str = Field(min_length=1, max_length=512)
    tier: SourceTier
    confidence: ConfidenceLevel
    category: KnowledgeCategory
    topic: str = Field(min_length=1, max_length=128)
    retrieved_at: datetime
    published_at: datetime | None = None
    content_hash: str | None = None
    provider: str | None = Field(default=None, max_length=128)
    excerpt: str | None = Field(default=None, max_length=4_000)
    corroborated_by: tuple[str, ...] = ()

    @field_validator("retrieved_at", "published_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            msg = "source timestamps must be timezone-aware"
            raise ValueError(msg)
        return value

    @field_validator("content_hash")
    @classmethod
    def _validate_hash(cls, value: str | None) -> str | None:
        if value is not None and not _HASH_PATTERN.match(value):
            msg = "content_hash must be a lowercase SHA-256 hex digest"
            raise ValueError(msg)
        return value

    @property
    def host(self) -> str:
        """Hostname of the source, lowercased."""
        return (urlparse(str(self.url)).hostname or "").lower()

    def is_stale(self, policy: FreshnessPolicy) -> bool:
        """Whether this document should be re-fetched under ``policy``."""
        return policy.is_stale(self.retrieved_at)


class ResearchRequest(BaseModel):
    """A research intent issued by the application layer."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    query: str = Field(min_length=3, max_length=512)
    topic: str = Field(min_length=1, max_length=128)
    category: KnowledgeCategory = KnowledgeCategory.DYNAMIC
    max_results: int = Field(default=5, ge=1, le=25)
    require_official_sources: bool = False
    freshness_policy: FreshnessPolicy | None = None

    @field_validator("freshness_policy")
    @classmethod
    def _policy_matches_category(
        cls, value: FreshnessPolicy | None, info: object
    ) -> FreshnessPolicy | None:
        data = getattr(info, "data", {})
        category = data.get("category")
        if value is not None and category is not None and value.category != category:
            msg = "freshness_policy category must match the request category"
            raise ValueError(msg)
        return value


class ResearchResult(BaseModel):
    """Outcome of a research run.

    ``degraded`` records partial failure so that callers can tell the difference
    between "no information exists" and "the search provider was unavailable".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: ResearchRequest
    sources: tuple[SourceDocument, ...] = ()
    summary: str = ""
    searched_at: datetime = Field(default_factory=utc_now)
    degraded: bool = False
    degradation_reason: str | None = None

    @field_validator("searched_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            msg = "searched_at must be timezone-aware"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> ResearchResult:
        if len(self.sources) > self.request.max_results:
            msg = "result contains more sources than the request allowed"
            raise ValueError(msg)
        if self.degraded and not self.degradation_reason:
            msg = "degraded results must explain the degradation"
            raise ValueError(msg)
        if not self.degraded and self.degradation_reason is not None:
            msg = "degradation_reason requires degraded=True"
            raise ValueError(msg)
        return self

    @property
    def best_confidence(self) -> ConfidenceLevel:
        """Highest confidence across the collected sources."""
        rank = {level: position for position, level in enumerate(ConfidenceLevel)}
        return min(
            (source.confidence for source in self.sources),
            key=rank.__getitem__,
            default=ConfidenceLevel.SPECULATIVE,
        )

    def authoritative_sources(
        self, minimum: SourceTier = SourceTier.OFFICIAL_DOCUMENTATION
    ) -> tuple[SourceDocument, ...]:
        """Sources at or above ``minimum`` authority."""
        return tuple(source for source in self.sources if source.tier <= minimum)
