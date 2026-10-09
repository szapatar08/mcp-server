"""Architecture review tool.

Translates an MCP call into the review use case and back. This layer owns MCP
shape: input mapping, output shaping and error conversion. It owns no rules.
"""

from __future__ import annotations

from architecture_agent.application.errors import InvalidInputError
from architecture_agent.application.review_service import (
    ArchitectureReviewService,
    ReviewRequest,
)
from architecture_agent.domain.infrastructure import AvailabilityObjective
from architecture_agent.domain.security import ArchitectureReview, ReviewDimension
from architecture_agent.tools.schemas import (
    ReviewArchitectureInput,
    ReviewArchitectureOutput,
)

_ALWAYS_REVIEWED: tuple[ReviewDimension, ...] = (
    ReviewDimension.SECURITY,
    ReviewDimension.RELIABILITY,
    ReviewDimension.OBSERVABILITY,
    ReviewDimension.SCALABILITY,
)


def review_architecture(
    request: ReviewArchitectureInput,
    service: ArchitectureReviewService,
) -> ReviewArchitectureOutput:
    """Review an architecture and return scores, findings and evidence gaps.

    Args:
        request: The material under review.
        service: The review use case.

    Returns:
        The review, a one-line summary and whether the review was complete.

    Raises:
        InvalidInputError: if the availability inputs are internally inconsistent.
    """
    availability = _availability(request)
    review = service.review(
        ReviewRequest(
            architecture=request.architecture,
            infrastructure=request.infrastructure,
            deployment=request.deployment,
            availability=availability,
            cloud_provider=request.cloud_provider,
        )
    )
    return _to_output(review)


def _availability(request: ReviewArchitectureInput) -> AvailabilityObjective | None:
    """Build an availability objective, or ``None`` when none was supplied."""
    supplied = (
        request.availability_target,
        request.rto_minutes,
        request.rpo_minutes,
        request.multi_region,
    )
    if all(value is None or value is False for value in supplied):
        return None

    if request.availability_target is None:
        msg = (
            "availability_target is required when rto_minutes, rpo_minutes or "
            "multi_region are supplied"
        )
        raise InvalidInputError(msg)

    return AvailabilityObjective(
        availability_target=request.availability_target,
        rto_minutes=request.rto_minutes,
        rpo_minutes=request.rpo_minutes,
        multi_region=request.multi_region,
    )


def _to_output(review: ArchitectureReview) -> ReviewArchitectureOutput:
    """Shape a review for the MCP boundary."""
    unreviewed = [
        dimension.value
        for dimension in _ALWAYS_REVIEWED
        if dimension not in {score.dimension for score in review.scores}
    ]
    complete = not unreviewed and not review.evidence_gaps
    summary = _summarize(review, complete, tuple(unreviewed))
    return ReviewArchitectureOutput(
        review=review,
        summary=summary,
        complete=complete,
    )


def _summarize(review: ArchitectureReview, complete: bool, unreviewed: tuple[str, ...]) -> str:
    """Build a one-line outcome that states what was and was not reviewed."""
    blocking = review.blocking_findings
    headline = (
        f"{len(blocking)} blocking finding(s) across "
        f"{len({finding.dimension for finding in blocking})} dimension(s); "
        f"overall {review.overall_score()}/10 across {len(review.scores)} scored dimension(s)."
        if blocking
        else (
            f"No blocking findings; overall {review.overall_score()}/10 across "
            f"{len(review.scores)} scored dimension(s)."
        )
    )
    if complete:
        return headline

    omitted = f", not scored: {', '.join(unreviewed)}" if unreviewed else ""
    return (
        f"{headline} This review is incomplete{omitted}; "
        f"{len(review.evidence_gaps)} evidence gap(s) are listed and the remaining "
        "dimensions were not scored rather than scored badly."
    )
