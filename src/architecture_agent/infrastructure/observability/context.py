"""Cross-cutting request context propagated through logs and tool calls."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Final

_REQUEST_ID: Final[ContextVar[str | None]] = ContextVar(
    "architecture_agent_request_id", default=None
)
_OPERATION: Final[ContextVar[str | None]] = ContextVar("architecture_agent_operation", default=None)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Ambient identifiers describing the unit of work in flight."""

    request_id: str
    operation: str | None = None

    def as_log_fields(self) -> dict[str, Any]:
        """Render the context as structured logging fields."""
        fields: dict[str, Any] = {"request_id": self.request_id}
        if self.operation is not None:
            fields["operation"] = self.operation
        return fields


def new_request_id() -> str:
    """Generate a fresh correlation identifier."""
    return uuid.uuid4().hex


def current_request_id() -> str | None:
    """Return the correlation identifier bound to the current context."""
    return _REQUEST_ID.get()


def current_operation() -> str | None:
    """Return the operation name bound to the current context."""
    return _OPERATION.get()


def bind_request_context(request_id: str) -> Token[str | None]:
    """Bind a request id to the current context."""
    return _REQUEST_ID.set(request_id)


def unbind_request_context(token: Token[str | None]) -> None:
    """Restore the request id that was bound before ``token`` was created."""
    _REQUEST_ID.reset(token)


def bind_operation(operation: str) -> Token[str | None]:
    """Bind an operation name to the current context."""
    return _OPERATION.set(operation)


def unbind_operation(token: Token[str | None]) -> None:
    """Restore the operation name that was bound before ``token`` was created."""
    _OPERATION.reset(token)


def current_context() -> RequestContext:
    """Return the ambient request context, creating an id when absent."""
    request_id = current_request_id()
    return RequestContext(request_id=request_id or new_request_id())


@contextmanager
def request_scope(operation: str | None = None) -> Iterator[RequestContext]:
    """Bind a request context for the duration of the block.

    A request id is generated when the caller has not already bound one, so
    every log line inside the block is correlatable.
    """
    request_token: Token[str | None] | None = None
    if _REQUEST_ID.get() is None:
        request_token = _REQUEST_ID.set(new_request_id())

    operation_token = bind_operation(operation) if operation is not None else None
    try:
        yield current_context()
    finally:
        if operation_token is not None:
            _OPERATION.reset(operation_token)
        if request_token is not None:
            _REQUEST_ID.reset(request_token)
