"""Readiness check for a deployment.

Verifies that the process can start and be configured correctly. Intended to run
as a container ``HEALTHCHECK`` and as a CI smoke test.

Exits ``0`` when ready, ``1`` otherwise, and prints one ``key: value`` line per
check. It never prints a secret: settings are inspected through the model, not by
dumping the environment.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from architecture_agent.config.settings import Settings, get_settings
from architecture_agent.infrastructure.observability.logging import configure_logging, get_logger


class CheckStatus(StrEnum):
    """Outcome of a single readiness check."""

    OK = "ok"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of one named check."""

    name: str
    status: CheckStatus
    detail: str

    def render(self) -> str:
        """Render as a single ``name: status detail`` line."""
        return f"{self.name}: {self.status.value} {self.detail}".rstrip()


def check_imports() -> CheckResult:
    """Confirm the package imports and reports a version."""
    import architecture_agent

    version = architecture_agent.__version__
    if version.endswith("unknown"):
        return CheckResult("imports", CheckStatus.FAIL, f"version is {version}")
    return CheckResult("imports", CheckStatus.OK, f"version {version}")


def check_configuration(settings: Settings) -> CheckResult:
    """Confirm settings loaded from a real source."""
    environment = settings.environment
    if not environment:
        return CheckResult("configuration", CheckStatus.FAIL, "environment is empty")
    return CheckResult("configuration", CheckStatus.OK, f"environment {environment}")


def check_logging(settings: Settings) -> CheckResult:
    """Confirm logging can be configured at the requested level."""
    configure_logging(
        level=settings.log_level.value,
        service_name=settings.service_name,
        json_output=settings.log_format.value == "json",
        include_stacktraces=settings.log_include_stacktraces,
        redact_logs=settings.security.redact_logs,
    )
    get_logger("architecture_agent.health").debug("logging configured", extra={"status": "ok"})
    return CheckResult("logging", CheckStatus.OK, f"{settings.log_format.value} on stderr")


def check_security_posture(settings: Settings) -> CheckResult:
    """Confirm execution tools are disabled and redaction is on."""
    problems: list[str] = []
    if settings.security.allow_execution_tools:
        problems.append("execution tools are enabled")
    if not settings.security.redact_logs:
        problems.append("log redaction is disabled")
    if problems:
        return CheckResult("security_posture", CheckStatus.FAIL, "; ".join(problems))
    allowlist = (
        "unrestricted"
        if not settings.security.allowed_domains
        else f"{len(settings.security.allowed_domains)} domains"
    )
    return CheckResult("security_posture", CheckStatus.OK, f"read-only, egress {allowlist}")


def check_llm_credentials(settings: Settings) -> CheckResult:
    """Report whether an explicit API key is configured, without revealing it."""
    provider = settings.llm.provider
    if settings.llm.api_key is not None:
        return CheckResult("llm_credentials", CheckStatus.OK, f"{provider} key configured")
    if settings.is_production:
        return CheckResult(
            "llm_credentials",
            CheckStatus.FAIL,
            f"{provider} key not set; production requires an explicit key",
        )
    return CheckResult(
        "llm_credentials",
        CheckStatus.OK,
        f"{provider} key not set; falling back to the provider environment",
    )


def build_checks(settings: Settings) -> list[Callable[[], CheckResult]]:
    """Return the readiness checks in execution order."""
    return [
        check_imports,
        lambda: check_configuration(settings),
        lambda: check_logging(settings),
        lambda: check_security_posture(settings),
        lambda: check_llm_credentials(settings),
    ]


def run(settings: Settings | None = None) -> list[CheckResult]:
    """Run every check and return the results."""
    resolved = settings or get_settings()
    return [check() for check in build_checks(resolved)]


def main() -> int:
    """Print the results and return a process exit code."""
    results = run()
    for result in results:
        print(result.render())
    return 0 if all(result.status is CheckStatus.OK for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
