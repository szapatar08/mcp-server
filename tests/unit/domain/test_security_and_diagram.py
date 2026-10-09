"""Review findings, scoring and diagram validation (FR-014, FR-015, FR-020)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from architecture_agent.domain.diagram import (
    Diagram,
    DiagramIssue,
    DiagramKind,
    DiagramLanguage,
    ValidationSeverity,
)
from architecture_agent.domain.security import (
    ArchitectureReview,
    DimensionScore,
    FindingCategory,
    ReviewDimension,
    ReviewFinding,
    Severity,
)

pytestmark = pytest.mark.unit


def _finding(
    identifier: str,
    *,
    dimension: ReviewDimension = ReviewDimension.SECURITY,
    severity: Severity = Severity.HIGH,
    confidence: str = "high",
) -> ReviewFinding:
    return ReviewFinding(
        id=identifier,
        title=f"Finding {identifier} about the reviewed architecture",
        dimension=dimension,
        severity=severity,
        description="A description long enough to satisfy the model's minimum length.",
        evidence="Observed in the supplied deployment description.",
        recommendation="Apply the documented remediation.",
        category=FindingCategory.NETWORK_EXPOSURE,
        confidence=confidence,
    )


def _score(
    dimension: ReviewDimension,
    score: float,
    *,
    confidence: str = "medium",
    justification: str = "A justification long enough to satisfy the model constraints.",
    blocking_findings: tuple[str, ...] = (),
) -> DimensionScore:
    return DimensionScore(
        dimension=dimension,
        score=score,
        confidence=confidence,
        justification=justification,
        blocking_findings=blocking_findings,
    )


class TestSeverityOrdering:
    """Severity must sort so that reviews can rank their findings."""

    def test_severity_rank_is_ordered(self) -> None:
        assert Severity.CRITICAL.rank > Severity.HIGH.rank > Severity.MEDIUM.rank
        assert Severity.MEDIUM.rank > Severity.LOW.rank > Severity.INFO.rank


class TestArchitectureReview:
    """FR-020: structured scores with reasoning, never invented precision."""

    @pytest.fixture
    def review(self) -> ArchitectureReview:
        return ArchitectureReview(
            subject="Checkout platform v1",
            scores=(
                _score(ReviewDimension.SECURITY, 8.0),
                _score(ReviewDimension.RELIABILITY, 6.0),
                _score(ReviewDimension.OBSERVABILITY, 5.0, justification="No traces and no SLO."),
            ),
            findings=(
                _finding("SEC-001", severity=Severity.CRITICAL),
                _finding("REL-001", dimension=ReviewDimension.RELIABILITY, severity=Severity.HIGH),
                _finding("OBS-001", dimension=ReviewDimension.OBSERVABILITY, severity=Severity.LOW),
            ),
            recommendations=("Add a Multi-AZ standby for the primary database.",),
            evidence_gaps=("No traffic forecast was supplied.",),
        )

    def test_findings_are_ranked_and_grouped(self, review: ArchitectureReview) -> None:
        assert len(review.critical_findings) == 1
        assert len(review.blocking_findings) == 2
        assert review.findings_by_severity(Severity.LOW)[0].id == "OBS-001"

    def test_overall_score_is_the_mean_of_dimensions(self, review: ArchitectureReview) -> None:
        assert review.overall_score() == pytest.approx(6.33)

    def test_each_dimension_may_only_be_scored_once(self) -> None:
        with pytest.raises(ValidationError, match="duplicate review dimensions"):
            ArchitectureReview(
                subject="Doubly scored architecture",
                scores=(
                    _score(ReviewDimension.SECURITY, 7.0),
                    _score(ReviewDimension.SECURITY, 8.0),
                ),
            )

    def test_scores_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            _score(ReviewDimension.SECURITY, 11.0)
        with pytest.raises(ValidationError):
            _score(ReviewDimension.SECURITY, -1.0)

    def test_a_confident_low_score_must_cite_findings(self) -> None:
        with pytest.raises(ValidationError, match="must cite blocking findings"):
            _score(
                ReviewDimension.OBSERVABILITY,
                3.0,
                confidence="high",
                justification="There are no traces, no SLO and no alerts.",
            )

        assert _score(
            ReviewDimension.OBSERVABILITY,
            3.0,
            confidence="high",
            justification="There are no traces, no SLO and no alerts.",
            blocking_findings=("OBS-001",),
        ).blocking_findings == ("OBS-001",)

    def test_evidence_gaps_are_surfaced(self, review: ArchitectureReview) -> None:
        assert review.evidence_gaps == ("No traffic forecast was supplied.",)


class TestDiagram:
    """FR-015: generated diagrams must be syntactically valid."""

    def test_a_valid_diagram(self) -> None:
        diagram = Diagram(
            title="Container diagram",
            kind=DiagramKind.C4_CONTAINER,
            language=DiagramLanguage.MERMAID,
            source="C4Container\n  title Checkout",
        )

        assert diagram.is_valid is True
        assert diagram.code_fence_language == "mermaid"

    def test_blank_sources_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Diagram(title="Empty", kind=DiagramKind.SEQUENCE, source="   \n  ")

    def test_blocking_syntax_errors_prevent_the_diagram_from_being_returned(self) -> None:
        with pytest.raises(ValidationError, match="blocking syntax errors"):
            Diagram(
                title="Broken",
                kind=DiagramKind.SEQUENCE,
                source="sequenceDiagram\n  A ->",
                issues=(
                    DiagramIssue(
                        line=2,
                        message="expected participant or message",
                        severity=ValidationSeverity.ERROR,
                    ),
                ),
            )

    def test_warnings_do_not_make_a_diagram_invalid(self) -> None:
        diagram = Diagram(
            title="Deprecated syntax",
            kind=DiagramKind.SEQUENCE,
            source="sequenceDiagram\n  participant A",
            issues=(
                DiagramIssue(
                    line=2,
                    message="participant has no messages",
                    severity=ValidationSeverity.WARNING,
                ),
            ),
        )

        assert diagram.is_valid is True
        assert str(diagram.issues[0]).startswith("line 2:")
