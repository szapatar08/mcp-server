"""Security, reliability and review findings.

One finding model serves security, reliability, scalability and observability
reviews so that MCP responses share a single, predictable shape.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Severity(StrEnum):
    """Impact ordering for review findings."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Numeric rank, higher means more severe."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


class FindingCategory(StrEnum):
    """Security review taxonomy aligned with common assurance frameworks."""

    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    IAM = "iam"
    SECRETS_MANAGEMENT = "secrets_management"
    ENCRYPTION_IN_TRANSIT = "encryption_in_transit"
    ENCRYPTION_AT_REST = "encryption_at_rest"
    NETWORK_EXPOSURE = "network_exposure"
    SEGMENTATION = "segmentation"
    API_SECURITY = "api_security"
    DEPENDENCY_SUPPLY_CHAIN = "dependency_supply_chain"
    CONTAINER_SECURITY = "container_security"
    CLOUD_POSTURE = "cloud_posture"
    LEAST_PRIVILEGE = "least_privilege"
    AUDIT_LOGGING = "audit_logging"
    DATA_PROTECTION = "data_protection"


class ComplianceFramework(StrEnum):
    """External references the agent may ground a review in."""

    OWASP_TOP_10 = "owasp_top_10"
    OWASP_ASVS = "owasp_asvs"
    NIST_SSP_800_53 = "nist_sp_800_53"
    NIST_CSF = "nist_csf"
    CIS_BENCHMARK = "cis_benchmark"
    PCI_DSS = "pci_dss"
    ISO_27001 = "iso_27001"
    SOC2 = "soc2"


class ReliabilityIssue(StrEnum):
    """Reliability failure modes (FR-012)."""

    SINGLE_POINT_OF_FAILURE = "single_point_of_failure"
    MISSING_REDUNDANCY = "missing_redundancy"
    MISSING_BACKUP = "missing_backup"
    UNTESTED_RESTORE = "untested_restore"
    MISSING_FAILOVER = "missing_failover"
    WEAK_HEALTH_CHECKS = "weak_health_checks"
    INSUFFICIENT_RTO = "insufficient_rto"
    INSUFFICIENT_RPO = "insufficient_rpo"
    NO_ROLLBACK = "no_rollback"
    MISSING_MONITORING = "missing_monitoring"
    SHARED_FAILURE_DOMAIN = "shared_failure_domain"


class ReviewDimension(StrEnum):
    """Dimensions scored by an architecture review (FR-020)."""

    SECURITY = "security"
    SCALABILITY = "scalability"
    RELIABILITY = "reliability"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    COST = "cost"
    OBSERVABILITY = "observability"


class ReviewFinding(BaseModel):
    """A single, evidence-backed review observation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=3, max_length=256)
    dimension: ReviewDimension
    severity: Severity
    description: str = Field(min_length=10, max_length=4_000)
    evidence: str | None = Field(default=None, max_length=2_000)
    recommendation: str | None = Field(default=None, max_length=2_000)
    references: tuple[str, ...] = ()
    category: str | None = Field(default=None, max_length=64)
    confidence: str = Field(default="high", min_length=3, max_length=32)
    remediation_effort: str = Field(default="unknown", min_length=3, max_length=32)

    @field_validator("confidence", "remediation_effort")
    @classmethod
    def _controlled_vocabulary(cls, value: str) -> str:
        return value.strip().lower()


class DimensionScore(BaseModel):
    """A score with the reasoning that justifies it."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    dimension: ReviewDimension
    score: float = Field(ge=0.0, le=10.0)
    confidence: str = Field(default="medium", min_length=3, max_length=32)
    justification: str = Field(min_length=10, max_length=2_000)
    blocking_findings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _score_requires_reasons(self) -> Self:
        if self.score < 8.0 and not self.blocking_findings and self.confidence == "high":
            msg = "a low score with high confidence must cite blocking findings"
            raise ValueError(msg)
        return self


class ArchitectureReview(BaseModel):
    """Structured architecture review result (FR-020)."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    subject: str = Field(min_length=1, max_length=256)
    scores: tuple[DimensionScore, ...] = Field(min_length=1)
    findings: tuple[ReviewFinding, ...] = ()
    recommendations: tuple[str, ...] = ()
    evidence_gaps: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_dimensions(self) -> Self:
        dimensions = [score.dimension for score in self.scores]
        duplicates = {dim for dim in dimensions if dimensions.count(dim) > 1}
        if duplicates:
            msg = f"duplicate review dimensions: {sorted(str(d) for d in duplicates)}"
            raise ValueError(msg)
        return self

    def findings_by_severity(self, severity: Severity) -> tuple[ReviewFinding, ...]:
        """Return every finding at ``severity``."""
        return tuple(finding for finding in self.findings if finding.severity is severity)

    @property
    def critical_findings(self) -> tuple[ReviewFinding, ...]:
        """Findings that must be resolved before production."""
        return self.findings_by_severity(Severity.CRITICAL)

    @property
    def blocking_findings(self) -> tuple[ReviewFinding, ...]:
        """Findings at ``CRITICAL`` or ``HIGH`` severity."""
        return tuple(
            finding
            for finding in self.findings
            if finding.severity in {Severity.CRITICAL, Severity.HIGH}
        )

    def overall_score(self) -> float:
        """Mean score across the scored dimensions."""
        return round(sum(score.score for score in self.scores) / len(self.scores), 2)
