"""Structured logging, correlation context and secret redaction."""

from architecture_agent.infrastructure.observability.context import (
    RequestContext,
    bind_operation,
    bind_request_context,
    current_context,
    current_operation,
    current_request_id,
    new_request_id,
    request_scope,
    unbind_operation,
    unbind_request_context,
)
from architecture_agent.infrastructure.observability.logging import (
    JsonFormatter,
    TextFormatter,
    configure_logging,
    get_logger,
    is_configured,
    log_operation,
)
from architecture_agent.infrastructure.observability.redaction import (
    REDACTED,
    is_sensitive_key,
    redact,
    redact_text,
)

__all__ = [
    "REDACTED",
    "JsonFormatter",
    "RequestContext",
    "TextFormatter",
    "bind_operation",
    "bind_request_context",
    "configure_logging",
    "current_context",
    "current_operation",
    "current_request_id",
    "get_logger",
    "is_configured",
    "is_sensitive_key",
    "log_operation",
    "new_request_id",
    "redact",
    "redact_text",
    "request_scope",
    "unbind_operation",
    "unbind_request_context",
]
