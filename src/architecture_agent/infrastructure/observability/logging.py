"""Structured logging.

Emits one JSON object per line by default so that logs can be shipped to any
aggregation backend without a parsing agent. Every record carries the correlation
fields required for operational debugging: ``request_id``, ``operation``,
``duration_ms``, ``status`` and ``error``.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Final

from architecture_agent.infrastructure.observability import context as ctx
from architecture_agent.infrastructure.observability.redaction import redact, redact_text

_RESERVED_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

_CONFIGURED: bool = False


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as a single-line JSON object."""

    def __init__(
        self,
        *,
        service_name: str,
        include_stacktraces: bool = False,
        redact_logs: bool = True,
    ) -> None:
        """Configure the JSON formatter."""
        super().__init__()
        self._service_name = service_name
        self._include_stacktraces = include_stacktraces
        self._redact_logs = redact_logs

    def format(self, record: logging.LogRecord) -> str:
        """Serialize ``record`` to JSON."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service_name,
            "message": record.getMessage(),
        }

        request_id = ctx.current_request_id()
        if request_id is not None:
            payload["request_id"] = request_id
        operation = ctx.current_operation()
        if operation is not None:
            payload["operation"] = operation

        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value

        error = self._format_error(record)
        if error is not None:
            payload["error"] = error

        if self._redact_logs:
            safe = redact(payload)
            return json.dumps(safe, default=str, ensure_ascii=False)
        return json.dumps(payload, default=str, ensure_ascii=False)

    def _format_error(self, record: logging.LogRecord) -> dict[str, Any] | None:
        if record.exc_info is None:
            return None
        exc_type, exc_value, _ = record.exc_info
        error: dict[str, Any] = {
            "type": exc_type.__name__ if exc_type is not None else "Exception",
            "message": redact_text(str(exc_value)),
        }
        if self._include_stacktraces:
            error["stacktrace"] = redact_text(self.formatException(record.exc_info))
        return error


class TextFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    def __init__(self, *, service_name: str, redact_logs: bool = True) -> None:
        """Configure the human readable formatter."""
        super().__init__("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        self._service_name = service_name
        self._redact_logs = redact_logs

    def format(self, record: logging.LogRecord) -> str:
        """Format ``record``, prefixing correlation fields when present."""
        message = record.getMessage()
        if self._redact_logs:
            message = redact_text(message)
        prefixes = []
        request_id = ctx.current_request_id()
        if request_id is not None:
            prefixes.append(f"request_id={request_id[:8]}")
        operation = ctx.current_operation()
        if operation is not None:
            prefixes.append(f"op={operation}")
        duration = getattr(record, "duration_ms", None)
        if duration is not None:
            prefixes.append(f"took={duration}ms")
        prefix = f"[{' '.join(prefixes)}] " if prefixes else ""
        stamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds")
        return f"{stamp} {record.levelname:<8} {record.name}: {prefix}{message}"


def configure_logging(
    *,
    level: int | str = logging.INFO,
    service_name: str = "architecture-agent",
    json_output: bool = True,
    include_stacktraces: bool = False,
    redact_logs: bool = True,
) -> None:
    """Install the root log handler. Idempotent across repeated calls."""
    global _CONFIGURED

    handler = logging.StreamHandler(stream=sys.stderr)
    if json_output:
        handler.setFormatter(
            JsonFormatter(
                service_name=service_name,
                include_stacktraces=include_stacktraces,
                redact_logs=redact_logs,
            )
        )
    else:
        handler.setFormatter(TextFormatter(service_name=service_name, redact_logs=redact_logs))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    for noisy in ("httpx", "httpcore", "urllib3", "openai", "duckduckgo_search"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def is_configured() -> bool:
    """Whether :func:`configure_logging` has run in this process."""
    return _CONFIGURED


def get_logger(name: str) -> logging.Logger:
    """Return a module logger.

    Correlation fields are deliberately *not* baked into the logger. They are
    read from the ambient :mod:`context` at emit time by the formatter, so a
    record is correlated with the request actually in flight rather than with
    whichever request happened to create the logger.

    A :class:`logging.LoggerAdapter` is avoided on purpose: the standard library
    implementation of ``LoggerAdapter.process`` replaces the caller's ``extra``
    mapping instead of merging it, which silently discards the structured fields
    that tools attach to their records.
    """
    return logging.getLogger(name)


@contextmanager
def log_operation(
    logger: logging.Logger,
    operation: str,
    **fields: Any,
) -> Iterator[dict[str, Any]]:
    """Log the start and outcome of ``operation`` with its duration.

    The yielded dict is mutable: callers may add fields that will be emitted on
    the completion record.
    """
    started = time.perf_counter()
    outcome: dict[str, Any] = dict(fields)
    with ctx.request_scope(operation):
        logger.debug("operation.start", extra={"operation": operation, **outcome})
        try:
            yield outcome
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            logger.error(
                "operation.failed",
                extra={
                    "operation": operation,
                    "duration_ms": duration_ms,
                    "status": "error",
                    "error_type": type(exc).__name__,
                    **outcome,
                },
                exc_info=True,
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        logger.info(
            "operation.completed",
            extra={
                "operation": operation,
                "duration_ms": duration_ms,
                "status": outcome.pop("status", "ok"),
                **outcome,
            },
        )
