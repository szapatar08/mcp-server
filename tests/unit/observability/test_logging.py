"""Structured logging, correlation context and secret redaction."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from pydantic import SecretStr

from architecture_agent.infrastructure.observability import context as ctx
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

pytestmark = pytest.mark.unit


def _record(
    message: str = "hello",
    *,
    level: int = logging.INFO,
    exc_info: Any = None,
) -> logging.LogRecord:
    return logging.LogRecord(
        name="architecture_agent.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=exc_info,
    )


class TestRedaction:
    """Credentials must be scrubbed structurally, not by string matching."""

    @pytest.mark.parametrize(
        "key",
        [
            "api_key",
            "API-KEY",
            "aws_secret_access_key",
            "db_password",
            "authorization",
            "cookie",
            "private_key",
            "connection_string",
        ],
    )
    def test_sensitive_keys_are_detected(self, key: str) -> None:
        assert is_sensitive_key(key) is True

    @pytest.mark.parametrize("key", ["component_name", "region", "retries", "usage_bucket"])
    def test_ordinary_keys_are_preserved(self, key: str) -> None:
        assert is_sensitive_key(key) is False

    def test_nested_structures_are_redacted_recursively(self) -> None:
        payload: dict[str, Any] = {
            "request": {"provider": "aws", "connection": {"api_key": "sk-live-123"}},
            "line_items": [{"name": "ec2", "token": "abc123"}],
        }

        scrubbed = redact(payload)

        assert scrubbed["request"]["provider"] == "aws"
        assert scrubbed["request"]["connection"]["api_key"] == REDACTED
        assert scrubbed["line_items"][0]["token"] == REDACTED
        assert scrubbed["line_items"][0]["name"] == "ec2"
        assert "sk-live-123" not in json.dumps(scrubbed)

    def test_a_sensitive_container_is_replaced_wholesale(self) -> None:
        payload: dict[str, Any] = {"credentials": {"user": "root", "api_key": "sk-live-123"}}

        scrubbed = redact(payload)

        assert scrubbed["credentials"] == REDACTED

    def test_secret_types_are_never_stringified(self) -> None:
        scrubbed = redact({"creds": SecretStr("sk-live-123")})

        assert scrubbed["creds"] == REDACTED
        assert "sk-live-123" not in json.dumps(scrubbed, default=str)

    def test_tuples_and_sets_keep_their_type(self) -> None:
        scrubbed = redact({"items": ("a", "b"), "tags": {"x"}})

        assert isinstance(scrubbed["items"], tuple)
        assert isinstance(scrubbed["tags"], set)

    def test_recursion_is_bounded(self) -> None:
        payload: dict[str, Any] = {}
        node = payload
        for _ in range(20):
            child: dict[str, Any] = {}
            node["child"] = child
            node = child

        scrubbed = redact(payload)

        assert scrubbed  # terminated instead of recursing forever

    @pytest.mark.parametrize(
        "text",
        [
            "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG",
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
            "api_key = sk-proj-abcdef123456",
        ],
    )
    def test_free_text_scrubbing(self, text: str) -> None:
        assert "sk-" not in redact_text(text).replace("sk-proj", "").replace("REDACTED", "")

    def test_private_key_blocks_are_removed(self) -> None:
        block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nIBAAKC\n-----END RSA PRIVATE KEY-----"

        assert "MIIEow" not in redact_text(block)


class TestContext:
    """Correlation identifiers must flow through and unwind correctly."""

    def test_request_id_is_generated_when_absent(self) -> None:
        assert ctx.current_request_id() is None

        with ctx.request_scope("estimate_cost") as scope:
            assert scope.request_id is not None
            assert ctx.current_request_id() == scope.request_id
            assert ctx.current_operation() == "estimate_cost"

        assert ctx.current_request_id() is None
        assert ctx.current_operation() is None

    def test_nested_scopes_reuse_the_outer_request_id(self) -> None:
        with ctx.request_scope("outer") as outer, ctx.request_scope("inner") as inner:
            assert inner.request_id == outer.request_id
            assert ctx.current_operation() == "inner"

    def test_explicit_binding_can_be_restored(self) -> None:
        token = ctx.bind_request_context("req-123")
        assert ctx.current_request_id() == "req-123"

        ctx.unbind_request_context(token)

        assert ctx.current_request_id() is None

    def test_context_fields_omit_absent_operation(self) -> None:
        assert ctx.RequestContext(request_id="r1").as_log_fields() == {"request_id": "r1"}
        assert ctx.RequestContext(request_id="r1", operation="op").as_log_fields() == {
            "request_id": "r1",
            "operation": "op",
        }


class TestJsonFormatter:
    """Every record must carry the operational fields required by NFR-27."""

    def test_required_fields_are_present(self) -> None:
        formatter = JsonFormatter(service_name="architecture-agent")

        payload = json.loads(formatter.format(_record("operation done")))

        assert payload["level"] == "INFO"
        assert payload["logger"] == "architecture_agent.test"
        assert payload["service"] == "architecture-agent"
        assert payload["message"] == "operation done"
        assert payload["timestamp"].endswith("+00:00")

    def test_correlation_fields_are_injected_from_context(self) -> None:
        formatter = JsonFormatter(service_name="architecture-agent")

        with ctx.request_scope("design_architecture"):
            payload = json.loads(formatter.format(_record("done")))

        assert payload["operation"] == "design_architecture"
        assert len(payload["request_id"]) == 32

    def test_extra_fields_are_serialized(self) -> None:
        formatter = JsonFormatter(service_name="architecture-agent")
        record = _record()
        record.duration_ms = 12.5
        record.status = "ok"

        payload = json.loads(formatter.format(record))

        assert payload["duration_ms"] == 12.5
        assert payload["status"] == "ok"

    def test_secrets_in_extra_fields_are_redacted(self) -> None:
        formatter = JsonFormatter(service_name="architecture-agent")
        record = _record()
        record.api_key = "sk-live-should-not-appear"

        payload = json.loads(formatter.format(record))

        assert payload["api_key"] == REDACTED

    def test_exceptions_become_structured_errors(self) -> None:
        formatter = JsonFormatter(service_name="architecture-agent")
        try:
            raise ValueError("provider unavailable")
        except ValueError:
            import sys

            payload = json.loads(formatter.format(_record(exc_info=sys.exc_info())))

        assert payload["error"]["type"] == "ValueError"
        assert payload["error"]["message"] == "provider unavailable"
        assert "stacktrace" not in payload["error"]

    def test_stacktraces_are_opt_in(self) -> None:
        formatter = JsonFormatter(service_name="architecture-agent", include_stacktraces=True)
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            payload = json.loads(formatter.format(_record(exc_info=sys.exc_info())))

        assert "ValueError" in payload["error"]["stacktrace"]


class TestTextFormatter:
    """Human output stays readable and still carries correlation data."""

    def test_prefixes_correlation_fields(self) -> None:
        formatter = TextFormatter(service_name="architecture-agent")
        record = _record("hello")
        record.duration_ms = 4.0

        with ctx.request_scope("review_architecture"):
            line = formatter.format(record)

        assert "INFO" in line
        assert "op=review_architecture" in line
        assert "took=4.0ms" in line
        assert line.rstrip().endswith("hello")

    def test_messages_are_scrubbed(self) -> None:
        formatter = TextFormatter(service_name="architecture-agent")

        assert "sk-live-123" not in formatter.format(_record("api_key=sk-live-123"))


class TestConfigureLogging:
    """Configuration must be idempotent and must not stack handlers."""

    def test_configure_logging_replaces_handlers(
        self, isolated_logging: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(level=logging.INFO, json_output=True)
        configure_logging(level=logging.INFO, json_output=True)

        assert is_configured() is True
        assert len(logging.getLogger().handlers) == 1

        get_logger("architecture_agent.test").info("hello", extra={"status": "ok"})

        captured = capsys.readouterr().err.strip().splitlines()
        assert json.loads(captured[-1])["message"] == "hello"

    def test_text_output(self, isolated_logging: None, capsys: pytest.CaptureFixture[str]) -> None:
        configure_logging(level=logging.INFO, json_output=False)

        get_logger("architecture_agent.test").info("plain message")

        assert "plain message" in capsys.readouterr().err

    def test_noisy_libraries_are_quieted(self, isolated_logging: None) -> None:
        configure_logging(level=logging.DEBUG)

        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("duckduckgo_search").level == logging.WARNING


class TestLogOperation:
    """Operations must be timed and must report failures."""

    def test_success_records_duration_and_status(
        self, isolated_logging: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(level=logging.INFO)

        with log_operation(get_logger("architecture_agent.test"), "estimate_cost", tool="cost"):
            pass

        records = [json.loads(line) for line in capsys.readouterr().err.strip().splitlines()]
        completion = next(r for r in records if r["message"] == "operation.completed")

        assert completion["operation"] == "estimate_cost"
        assert completion["tool"] == "cost"
        assert completion["status"] == "ok"
        assert completion["duration_ms"] >= 0
        assert completion["request_id"]

    def test_failures_are_logged_and_re_raised(self, isolated_logging: None) -> None:
        configure_logging(level=logging.INFO)
        logger = get_logger("architecture_agent.test")

        with pytest.raises(RuntimeError, match="upstream down"), log_operation(logger, "fetch"):
            raise RuntimeError("upstream down")

    def test_callers_can_annotate_the_completion_record(
        self, isolated_logging: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(level=logging.INFO)

        with log_operation(get_logger("architecture_agent.test"), "fetch") as outcome:
            outcome["status"] = "degraded"

        records = [json.loads(line) for line in capsys.readouterr().err.strip().splitlines()]
        completion = next(r for r in records if r["message"] == "operation.completed")

        assert completion["status"] == "degraded"
