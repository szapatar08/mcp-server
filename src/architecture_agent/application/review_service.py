"""Architecture review use case.

This is the *deterministic* half of FR-020: the checks a machine can make without
guessing. The reasoning half, which needs an LLM, arrives in Milestone 4 and will
call this service to supply evidence rather than replace it.

The governing rule is FR-020's "do not invent objective precision where evidence is
insufficient". Concretely:

* A dimension is scored **only** when the request supplies the inputs that
  dimension needs. Anything missing becomes an ``evidence_gap``, not a low score.
* Confidence is derived from whether a finding blocks, not from the number of
  findings. A dimension with no inputs is never scored as mediocre; it is omitted.
* Every finding cites the input it was derived from, so a reader can check it.

Findings are declared in :data:`_CATALOGUE` rather than inline at each call site,
so the prose a user reads is defined once and can be reviewed as a set.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from architecture_agent.domain.architecture import Architecture, ComponentKind
from architecture_agent.domain.cloud import CloudProvider
from architecture_agent.domain.deployment import (
    DeploymentPlan,
    ExposureLayer,
    RollbackStrategy,
    TlsTermination,
)
from architecture_agent.domain.infrastructure import (
    AvailabilityObjective,
    DatabaseTopology,
    InfrastructurePlan,
)
from architecture_agent.domain.security import (
    ArchitectureReview,
    DimensionScore,
    ReviewDimension,
    ReviewFinding,
    Severity,
)

_SEVERITY_PENALTY: Final[dict[Severity, float]] = {
    Severity.CRITICAL: 2.5,
    Severity.HIGH: 1.5,
    Severity.MEDIUM: 0.75,
    Severity.LOW: 0.25,
    Severity.INFO: 0.0,
}

_TEN_NINES: Final = 99.99
_HIGH_AVAILABILITY: Final = 99.9

_INSECURE_PROTOCOLS: Final[frozenset[str]] = frozenset(
    {"http", "tcp", "udp", "redis", "amqp", "kafka"}
)
_SENSITIVE_CLASSIFICATIONS: Final[frozenset[str]] = frozenset({"confidential", "restricted", "pii"})
_QUORUM_TOPOLOGIES: Final[frozenset[DatabaseTopology]] = frozenset(
    {
        DatabaseTopology.MULTI_AZ_PRIMARY_REPLICA,
        DatabaseTopology.CLUSTER,
        DatabaseTopology.SHARDED,
    }
)
_SAFE_TLS_TERMINATIONS: Final[frozenset[TlsTermination]] = frozenset(
    {
        TlsTermination.AT_EDGE_CDN,
        TlsTermination.AT_LOAD_BALANCER,
        TlsTermination.AT_REVERSE_PROXY,
        TlsTermination.AT_INGRESS,
        TlsTermination.END_TO_END,
    }
)
_SAFE_ROLLBACKS: Final[frozenset[RollbackStrategy]] = frozenset(
    {RollbackStrategy.AUTOMATIC, RollbackStrategy.MANUAL}
)


class ReviewScope(StrEnum):
    """How much of the review the caller wants.

    ``FAST`` runs only the checks that need the architecture alone. ``FULL``
    additionally uses whatever optional plans were supplied.
    """

    FAST = "fast"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """The material under review.

    Omitting an optional field is a statement that the information is unavailable.
    That is different from a statement that the architecture happens to be fine, and
    the difference is what keeps the review honest.
    """

    architecture: Architecture
    infrastructure: InfrastructurePlan | None = None
    deployment: DeploymentPlan | None = None
    availability: AvailabilityObjective | None = None
    cloud_provider: CloudProvider | None = None
    scope: ReviewScope = ReviewScope.FULL


@dataclass(frozen=True, slots=True)
class FindingText:
    """The fixed prose for one finding type."""

    title: str
    description: str
    recommendation: str
    dimension: ReviewDimension
    category: str
    effort: str


def _text(
    title: str,
    description: str,
    recommendation: str,
    *,
    dimension: ReviewDimension,
    category: str,
    effort: str = "medium",
) -> FindingText:
    """Build catalogue prose."""
    return FindingText(
        title=title,
        description=description,
        recommendation=recommendation,
        dimension=dimension,
        category=category,
        effort=effort,
    )


_CATALOGUE: Final[dict[str, FindingText]] = {
    "SEC-001": _text(
        "Confidential data crosses an unencrypted protocol",
        "One or more interactions carry confidential or restricted data over a "
        "protocol that provides no transport encryption.",
        "Terminate TLS at the earliest safe hop and mark internal hops as encrypted in transit.",
        dimension=ReviewDimension.SECURITY,
        category="encryption_in_transit",
    ),
    "SEC-002": _text(
        "A data store is modelled as an external dependency",
        "A database or object store is modelled as an external dependency, which "
        "places it outside the trust boundary.",
        "Place the store behind a private subnet and reach it only through a service.",
        dimension=ReviewDimension.SECURITY,
        category="network_exposure",
    ),
    "SEC-003": _text(
        "TLS is terminated inside the trust boundary",
        "The deployment plan places TLS termination behind the load balancer, so "
        "plaintext crosses the internal network.",
        "Terminate TLS at the earliest hop the architecture allows.",
        dimension=ReviewDimension.SECURITY,
        category="encryption_in_transit",
        effort="low",
    ),
    "SEC-004": _text(
        "Internet traffic reaches the application without a load balancer",
        "The exposure path places the internet directly in front of the "
        "application, with no load balancing, TLS termination or health check.",
        "Insert a load balancer or edge proxy so TLS, rate limits and health checks have a home.",
        dimension=ReviewDimension.SECURITY,
        category="network_exposure",
        effort="low",
    ),
    "SEC-005": _text(
        "The data tier is reachable without a private subnet",
        "The exposure path reaches the database layer without a private subnet "
        "between the application tier and the data tier.",
        "Place the data tier in a private subnet with no route from the public tier.",
        dimension=ReviewDimension.SECURITY,
        category="segmentation",
    ),
    "REL-001": _text(
        "A high-risk external dependency has no documented fallback",
        "A third party classified as high or critical risk has no stated behaviour "
        "when it is unavailable, slow or compromised.",
        "Document the degradation path, or remove the dependency, before production.",
        dimension=ReviewDimension.RELIABILITY,
        category="external_dependency",
    ),
    "REL-002": _text(
        "A data store cannot survive an availability zone failure",
        "The availability target is high but the data store topology has no standby, "
        "so a single zone loss becomes a data outage.",
        "Adopt a primary-replica or clustered topology, or lower the target explicitly.",
        dimension=ReviewDimension.RELIABILITY,
        category="single_point_of_failure",
    ),
    "REL-003": _text(
        "A data store has no automated backups",
        "A data store in the plan has automated backups disabled, so recovery depends "
        "on an operator running a manual procedure.",
        "Enable automated backups and verify that a restore has been tested.",
        dimension=ReviewDimension.RELIABILITY,
        category="missing_backup",
        effort="low",
    ),
    "REL-005": _text(
        "Recovery objectives are missing",
        "An availability target is declared without an RTO or RPO, so recovery can "
        "be neither measured nor tested against it.",
        "State the RTO and RPO the business will actually accept, then test against them.",
        dimension=ReviewDimension.RELIABILITY,
        category="disaster_recovery",
        effort="low",
    ),
    "REL-006": _text(
        "The deployment has no rollback strategy",
        "The deployment plan declares no rollback strategy, so a bad release cannot be reverted.",
        "Define an automatic rollback trigger and a tested revert procedure.",
        dimension=ReviewDimension.RELIABILITY,
        category="no_rollback",
    ),
    "SCAL-001": _text(
        "A compute unit is a single fixed instance",
        "A compute unit has a fixed scaling model and a single unit, so any load "
        "increase or node failure becomes an outage.",
        "Use an autoscaling group spanning at least two instances.",
        dimension=ReviewDimension.SCALABILITY,
        category="scalability",
    ),
    "OBS-001": _text(
        "The observability plan does not cover every required signal",
        "The infrastructure plan does not provide every telemetry signal needed to "
        "operate and audit the system.",
        "Add logs, metrics and alerting at minimum, then traces, health checks and "
        "dashboards as the system grows.",
        dimension=ReviewDimension.OBSERVABILITY,
        category="observability",
    ),
}


def finding_text(identifier: str) -> FindingText:
    """Return the catalogue entry for ``identifier``.

    Raises:
        KeyError: if the identifier is not a known finding. Every finding the
            service raises must have published prose.
    """
    return _CATALOGUE[identifier]


def _finding(
    identifier: str,
    *,
    severity: Severity,
    evidence: str,
) -> ReviewFinding:
    """Build a review finding from the catalogue plus observed evidence."""
    text = _CATALOGUE[identifier]
    return ReviewFinding(
        id=identifier,
        title=text.title,
        dimension=text.dimension,
        severity=severity,
        description=text.description,
        evidence=evidence,
        recommendation=text.recommendation,
        category=text.category,
        confidence="high",
        remediation_effort=text.effort,
    )


@dataclass(frozen=True, slots=True)
class _Rule:
    """One deterministic check."""

    dimension: ReviewDimension
    apply: Callable[[ReviewRequest], tuple[ReviewFinding, ...]]


class ArchitectureReviewService:
    """Produce a structured, evidence-backed architecture review."""

    def review(self, request: ReviewRequest) -> ArchitectureReview:
        """Review an architecture and return scores, findings and evidence gaps.

        Args:
            request: The material under review.

        Returns:
            An :class:`ArchitectureReview`. Dimensions whose inputs were not
            supplied are omitted from ``scores`` and named in ``evidence_gaps``, so
            absence of evidence is never presented as a mediocre score.
        """
        rules = self._applicable_rules(request)
        findings = tuple(finding for rule in rules for finding in rule.apply(request))
        scores, gaps = self._score(request, findings, rules)
        return ArchitectureReview(
            subject=request.architecture.name,
            scores=scores,
            findings=findings,
            recommendations=_recommendations(findings),
            evidence_gaps=gaps,
        )

    def _applicable_rules(self, request: ReviewRequest) -> tuple[_Rule, ...]:
        """Select the rules enabled by the material supplied."""
        rules: list[_Rule] = [
            _Rule(ReviewDimension.SECURITY, _check_transport_security),
            _Rule(ReviewDimension.SECURITY, _check_external_dependency_risk),
            _Rule(ReviewDimension.SECURITY, _check_external_data_store),
        ]

        if request.infrastructure is not None:
            rules.extend(
                (
                    _Rule(ReviewDimension.RELIABILITY, _check_database_resilience),
                    _Rule(ReviewDimension.OBSERVABILITY, _check_observability),
                    _Rule(ReviewDimension.SCALABILITY, _check_scaling_model),
                )
            )

        if request.availability is not None:
            rules.append(_Rule(ReviewDimension.RELIABILITY, _check_availability_target))

        if request.deployment is not None:
            rules.extend(
                (
                    _Rule(ReviewDimension.SECURITY, _check_tls_termination),
                    _Rule(ReviewDimension.RELIABILITY, _check_rollback),
                    _Rule(ReviewDimension.SECURITY, _check_exposure_path),
                )
            )

        return tuple(rules)

    def _score(
        self,
        request: ReviewRequest,
        findings: tuple[ReviewFinding, ...],
        rules: tuple[_Rule, ...],
    ) -> tuple[tuple[DimensionScore, ...], tuple[str, ...]]:
        """Score only the dimensions the supplied evidence can support."""
        covered = {rule.dimension for rule in rules}
        scorable = sorted(
            (dimension for dimension in covered if self._has_inputs(request, dimension)),
            key=lambda item: item.value,
        )
        scores = tuple(self._score_dimension(dimension, findings) for dimension in scorable)
        return scores, _evidence_gaps(request)

    def _has_inputs(self, request: ReviewRequest, dimension: ReviewDimension) -> bool:
        """Whether the request supplies enough to judge ``dimension``."""
        match dimension:
            case ReviewDimension.SECURITY:
                return True
            case (
                ReviewDimension.RELIABILITY
                | ReviewDimension.SCALABILITY
                | ReviewDimension.OBSERVABILITY
            ):
                return request.infrastructure is not None
            case _:
                return False

    def _score_dimension(
        self, dimension: ReviewDimension, findings: tuple[ReviewFinding, ...]
    ) -> DimensionScore:
        """Score a dimension from its findings, floored at zero.

        The score is a severity-weighted penalty sum, not a judgement. It is
        reproducible, and every input to it is a finding a reader can check.
        """
        relevant = tuple(finding for finding in findings if finding.dimension is dimension)
        penalty = sum(_SEVERITY_PENALTY[finding.severity] for finding in relevant)
        score = max(0.0, round(10.0 - penalty, 1))
        blocking = tuple(
            finding.id
            for finding in relevant
            if finding.severity in {Severity.CRITICAL, Severity.HIGH}
        )
        return DimensionScore(
            dimension=dimension,
            score=score,
            confidence="high" if blocking else "medium",
            justification=_justify(dimension, relevant, score),
            blocking_findings=blocking,
        )


def _recommendations(findings: tuple[ReviewFinding, ...]) -> tuple[str, ...]:
    """Order recommendations by severity, most severe first, without duplicates."""
    recommendations: list[str] = []
    for finding in sorted(findings, key=lambda item: (-item.severity.rank, item.id)):
        if finding.recommendation and finding.recommendation not in recommendations:
            recommendations.append(f"{finding.id}: {finding.recommendation}")
    return tuple(recommendations)


def _justify(dimension: ReviewDimension, findings: tuple[ReviewFinding, ...], score: float) -> str:
    """Explain a score in terms of the findings that produced it."""
    if not findings:
        return (
            f"No findings were raised for {dimension.value}; scored {score}/10 from the "
            "evidence supplied. That is not a claim the dimension is optimal, only that "
            "nothing in the supplied material contradicts it."
        )
    counts: dict[Severity, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    breakdown = ", ".join(
        f"{count} {severity.value}"
        for severity, count in sorted(counts.items(), key=lambda item: -item[0].rank)
    )
    return (
        f"Scored {score}/10 after subtracting severity weights for {len(findings)} "
        f"finding(s): {breakdown}."
    )


def _evidence_gaps(request: ReviewRequest) -> tuple[str, ...]:
    """Name the evidence that would be needed to complete the review."""
    gaps: list[str] = []

    if request.infrastructure is None:
        gaps.append(
            "No infrastructure plan supplied: reliability, scalability and "
            "observability could not be reviewed."
        )
    else:
        gaps.append(
            "No traffic profile supplied: scalability was judged from topology alone, "
            "not from measured or forecast load."
        )
        if not request.infrastructure.availability.slo_description:
            gaps.append(
                "No service level objective supplied: the availability score reflects "
                "the target only, not whether it is met."
            )

    if request.deployment is None:
        gaps.append(
            "No deployment plan supplied: TLS termination, rollback strategy and the "
            "exposure path could not be reviewed."
        )

    if request.availability is None:
        gaps.append(
            "No availability objective supplied: single points of failure cannot be "
            "weighed against an RTO or RPO."
        )

    if request.cloud_provider is None:
        gaps.append("No cloud provider supplied: cost and vendor lock-in were not reviewed.")

    if not request.architecture.assumptions:
        gaps.append(
            "No assumptions were declared with the architecture, so the reviewer cannot "
            "tell intended scope from oversight."
        )

    gaps.append(
        "No codebase or team evidence supplied: performance and maintainability were not reviewed."
    )

    return tuple(gaps)


def _check_transport_security(request: ReviewRequest) -> tuple[ReviewFinding, ...]:
    """Flag interactions whose protocol carries sensitive data in the clear."""
    insecure = [
        interaction
        for interaction in request.architecture.interactions
        if interaction.protocol.lower() in _INSECURE_PROTOCOLS
        and interaction.data_classification.lower() in _SENSITIVE_CLASSIFICATIONS
    ]
    if not insecure:
        return ()
    listed = ", ".join(f"{item.source}->{item.target} ({item.protocol})" for item in insecure[:5])
    return (
        _finding(
            "SEC-001",
            severity=Severity.HIGH,
            evidence=f"Unencrypted interactions carrying sensitive data: {listed}.",
        ),
    )


def _check_external_dependency_risk(request: ReviewRequest) -> tuple[ReviewFinding, ...]:
    """Flag high-risk third parties that have no fallback."""
    risky = [
        dependency
        for dependency in request.architecture.external_dependencies
        if dependency.risk.value in {"high", "critical"} and not dependency.fallback
    ]
    if not risky:
        return ()
    listed = ", ".join(f"{item.name} ({item.risk.value})" for item in risky[:5])
    return (
        _finding(
            "REL-001",
            severity=Severity.HIGH,
            evidence=f"Dependencies without a fallback: {listed}.",
        ),
    )


def _check_external_data_store(request: ReviewRequest) -> tuple[ReviewFinding, ...]:
    """Flag data stores modelled as reachable from outside the trust boundary."""
    exposed = [
        component
        for component in request.architecture.components
        if component.kind in {ComponentKind.DATABASE, ComponentKind.OBJECT_STORE}
        and component.is_external
    ]
    if not exposed:
        return ()
    listed = ", ".join(component.name for component in exposed)
    return (
        _finding(
            "SEC-002",
            severity=Severity.HIGH,
            evidence=f"Externally modelled data stores: {listed}.",
        ),
    )


def _effective_target(request: ReviewRequest) -> float | None:
    """Return the availability target the review should weigh topology against.

    The request's objective wins when supplied: it is the stated requirement. The
    infrastructure plan carries its own objective, but it has a default, and
    letting that default silently override a stated 99.99% would quietly
    mis-weight every reliability finding.
    """
    if request.availability is not None:
        return request.availability.availability_target
    if request.infrastructure is not None:
        return request.infrastructure.availability.availability_target
    return None


def _check_database_resilience(request: ReviewRequest) -> tuple[ReviewFinding, ...]:
    """Flag data stores that cannot survive a zone failure or a bad day."""
    plan = request.infrastructure
    if plan is None:
        return ()

    findings: list[ReviewFinding] = []
    target = _effective_target(request)
    if target is None:
        return ()

    for database in plan.databases:
        if database.topology not in _QUORUM_TOPOLOGIES and target >= _HIGH_AVAILABILITY:
            findings.append(
                _finding(
                    "REL-002",
                    severity=Severity.HIGH if target >= _TEN_NINES else Severity.MEDIUM,
                    evidence=(
                        f"database={database.name}, topology={database.topology.value}, "
                        f"target={target}%."
                    ),
                )
            )
        if not database.automated_backups:
            findings.append(
                _finding(
                    "REL-003",
                    severity=Severity.HIGH,
                    evidence=f"database={database.name}, automated_backups=False.",
                )
            )

    return tuple(findings)


def _check_observability(request: ReviewRequest) -> tuple[ReviewFinding, ...]:
    """Flag telemetry the plan does not provide."""
    plan = request.infrastructure
    if plan is None:
        return ()

    stack = plan.observability
    missing = stack.missing_signals()
    if not missing:
        return ()

    listed = ", ".join(sorted(signal.value for signal in missing))
    return (
        _finding(
            "OBS-001",
            severity=Severity.MEDIUM if stack.is_operable else Severity.HIGH,
            evidence=f"Missing observability signals: {listed}.",
        ),
    )


def _check_scaling_model(request: ReviewRequest) -> tuple[ReviewFinding, ...]:
    """Flag compute that cannot absorb growth."""
    plan = request.infrastructure
    if plan is None:
        return ()

    return tuple(
        _finding(
            "SCAL-001",
            severity=Severity.HIGH,
            evidence=f"unit={unit.name}, scaling=fixed, quantity={unit.quantity}.",
        )
        for unit in plan.compute
        if unit.scaling.value == "fixed" and unit.quantity < 2
    )


def _check_availability_target(request: ReviewRequest) -> tuple[ReviewFinding, ...]:
    """Flag an availability target declared without recovery objectives.

    A target of 99.99% or better without multi-region design is not checked here:
    :class:`AvailabilityObjective` refuses to be constructed in that state, so
    there is nothing to review.
    """
    target = request.availability
    if target is None:
        return ()

    if not target.rto_minutes or not target.rpo_minutes:
        return (
            _finding(
                "REL-005",
                severity=Severity.MEDIUM,
                evidence=(
                    f"target={target.availability_target}%, rto={target.rto_minutes}, "
                    f"rpo={target.rpo_minutes}."
                ),
            ),
        )
    return ()


def _check_tls_termination(request: ReviewRequest) -> tuple[ReviewFinding, ...]:
    """Flag a deployment that terminates TLS inside the trust boundary."""
    plan = request.deployment
    if plan is None or plan.tls_termination in _SAFE_TLS_TERMINATIONS:
        return ()

    return (
        _finding(
            "SEC-003",
            severity=Severity.MEDIUM,
            evidence=f"tls_termination={plan.tls_termination.value}.",
        ),
    )


def _check_rollback(request: ReviewRequest) -> tuple[ReviewFinding, ...]:
    """Flag a deployment with no way back."""
    plan = request.deployment
    if plan is None or plan.rollback_strategy in _SAFE_ROLLBACKS:
        return ()

    return (
        _finding(
            "REL-006",
            severity=Severity.HIGH,
            evidence=f"rollback={plan.rollback_strategy.value}.",
        ),
    )


def _check_exposure_path(request: ReviewRequest) -> tuple[ReviewFinding, ...]:
    """Flag an exposure path that skips a protective layer."""
    plan = request.deployment
    if plan is None:
        return ()

    layers = plan.exposure_layers
    findings: list[ReviewFinding] = []

    if ExposureLayer.INTERNET in layers and ExposureLayer.LOAD_BALANCER not in layers:
        findings.append(
            _finding(
                "SEC-004",
                severity=Severity.MEDIUM,
                evidence="exposure_layers=internet -> application without a load balancer.",
            )
        )

    if ExposureLayer.DATABASE_LAYER in layers and ExposureLayer.PRIVATE_SUBNET not in layers:
        findings.append(
            _finding(
                "SEC-005",
                severity=Severity.CRITICAL,
                evidence="exposure_layers includes database_layer without private_subnet.",
            )
        )

    return tuple(findings)
