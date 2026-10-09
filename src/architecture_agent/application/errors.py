"""Application errors and their mapping to the MCP boundary.

The MCP boundary must return actionable messages for *expected* failures and reveal
nothing for *unexpected* ones. This module is the single place that decides which
is which, and the single place where a message is scrubbed before it can reach a
log line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import ValidationError

from architecture_agent.infrastructure.observability.redaction import redact_text

_MAX_FIELD_ERRORS: Final = 8
_MAX_MESSAGE_CHARS: Final = 2_000


@dataclass(frozen=True, slots=True)
class FieldError:
    """A single, human-readable input problem."""

    location: str
    message: str

    def render(self) -> str:
        """Render as ``field: problem``."""
        return f"{self.location}: {self.message}"


class ApplicationError(Exception):
    """Base class for failures that callers are expected to handle.

    Raising this (rather than an arbitrary exception) is what makes a failure
    visible to the client instead of masked by the MCP error boundary.
    """

    code: str = "application_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """Initialise with a message that is scrubbed before it is ever stored."""
        sanitized = _sanitize(message)
        super().__init__(sanitized)
        self.message = sanitized
        if code is not None:
            self.code = code


class InvalidInputError(ApplicationError):
    """The caller supplied input the domain refuses."""

    code = "input_rejected"


class UnsupportedRequestError(ApplicationError):
    """The request is well formed but cannot be served as asked."""

    code = "unsupported_request"


class RenderingError(ApplicationError):
    """A diagram could not be produced."""

    code = "renderer_failure"


@dataclass(frozen=True, slots=True)
class ErrorReport:
    """A structured description of a failure, safe to return or to log."""

    code: str
    message: str
    field_errors: tuple[FieldError, ...] = ()
    context: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        """Render the report as a single message."""
        if not self.field_errors:
            return self.message
        detail = "; ".join(error.render() for error in self.field_errors)
        return f"{self.message}: {detail}"

    def as_payload(self) -> dict[str, Any]:
        """Render as a JSON-safe mapping for structured logs."""
        payload: dict[str, Any] = {"error_code": self.code, "error_message": self.message}
        if self.field_errors:
            payload["field_errors"] = [
                {"location": error.location, "message": error.message}
                for error in self.field_errors
            ]
        if self.context:
            payload["error_context"] = self.context
        return payload


def _sanitize(message: str) -> str:
    """Scrub credentials from a message and bound its length."""
    return redact_text(message)[:_MAX_MESSAGE_CHARS]


def describe_validation_error(error: ValidationError, *, prefix: str = "") -> ErrorReport:
    """Translate a Pydantic validation failure into an :class:`ErrorReport`.

    Field locations are joined with dots so a nested model reads as
    ``architecture.components.0.name``. The offending value is deliberately
    omitted: it is echoed by Pydantic at the protocol boundary anyway, and a
    rejected value has no place in a log line.
    """
    prefix = prefix.strip(".") if prefix else ""
    field_errors: list[FieldError] = []
    for detail in error.errors()[:_MAX_FIELD_ERRORS]:
        location = ".".join(str(part) for part in detail.get("loc", ()))
        if prefix:
            location = f"{prefix}.{location}" if location else prefix
        message = str(detail.get("msg", "invalid value"))
        field_errors.append(FieldError(location=location or "(root)", message=_sanitize(message)))

    omitted = len(error.errors()) - len(field_errors)
    if omitted > 0:
        field_errors.append(
            FieldError(location="(root)", message=f"{omitted} further problem(s) omitted")
        )

    return ErrorReport(
        code=InvalidInputError.code,
        message=f"{len(error.errors())} validation problem(s) rejected the input",
        field_errors=tuple(field_errors),
    )


def report_for(error: BaseException) -> ErrorReport:
    """Build an :class:`ErrorReport` for any exception.

    Only :class:`ApplicationError` and :class:`ValidationError` produce
    client-actionable reports. Everything else is reported as an opaque internal
    failure: the caller must log it with ``exc_info`` and return nothing useful,
    which is exactly what ``mask_error_details`` then hides.
    """
    if isinstance(error, ApplicationError):
        return ErrorReport(code=error.code, message=error.message)
    if isinstance(error, ValidationError):
        return describe_validation_error(error)
    return ErrorReport(
        code="internal_error",
        message="An internal error occurred. The failure has been logged with a request id.",
    )
