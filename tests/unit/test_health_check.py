"""Tests for the deployment readiness check."""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from scripts.health_check import (
    CheckStatus,
    build_checks,
    check_configuration,
    check_imports,
    check_llm_credentials,
    check_logging,
    check_security_posture,
    main,
    run,
)

from architecture_agent.config.settings import SecuritySettings, Settings

pytestmark = pytest.mark.unit


@pytest.fixture
def settings() -> Settings:
    """Settings built without reading a ``.env`` file."""
    return Settings(_env_file=None)


def _with_security(settings: Settings, **changes: object) -> Settings:
    """Return ``settings`` with an updated security section."""
    return settings.model_copy(update={"security": settings.security.model_copy(update=changes)})


def _with_key(settings: Settings, secret: str) -> Settings:
    """Return ``settings`` with an explicit LLM API key."""
    return settings.model_copy(
        update={"llm": settings.llm.model_copy(update={"api_key": SecretStr(secret)})}
    )


class TestChecks:
    """Each check must fail for its own reason and no other."""

    def test_imports_reports_a_resolved_version(self) -> None:
        result = check_imports()

        assert result.status is CheckStatus.OK
        assert result.name == "imports"

    def test_configuration_requires_a_non_empty_environment(self, settings: Settings) -> None:
        assert check_configuration(settings).status is CheckStatus.OK

        empty = settings.model_copy(update={"environment": ""})
        result = check_configuration(empty)

        assert result.status is CheckStatus.FAIL
        assert result.detail == "environment is empty"

    def test_logging_is_configurable(self, settings: Settings, isolated_logging: None) -> None:
        assert check_logging(settings).status is CheckStatus.OK

    def test_security_posture_flags_enabled_execution_tools(self, settings: Settings) -> None:
        assert check_security_posture(settings).status is CheckStatus.OK

        result = check_security_posture(_with_security(settings, allow_execution_tools=True))

        assert result.status is CheckStatus.FAIL
        assert "execution tools are enabled" in result.detail

    def test_security_posture_flags_disabled_redaction(self, settings: Settings) -> None:
        assert (
            check_security_posture(_with_security(settings, redact_logs=False)).status
            is CheckStatus.FAIL
        )

    def test_an_egress_allowlist_is_reported_as_a_count(self, settings: Settings) -> None:
        restricted = settings.model_copy(
            update={"security": SecuritySettings(allowed_domains=("docs.aws.amazon.com",))}
        )

        assert "1 domains" in check_security_posture(restricted).detail

    def test_credentials_are_reported_without_revealing_them(self, settings: Settings) -> None:
        result = check_llm_credentials(_with_key(settings, "sk-live-123"))

        assert result.status is CheckStatus.OK
        assert "sk-live-123" not in result.detail
        assert "key configured" in result.detail

    def test_a_missing_key_is_acceptable_outside_production(self, settings: Settings) -> None:
        result = check_llm_credentials(settings)

        assert result.status is CheckStatus.OK
        assert "falling back" in result.detail

    def test_a_missing_key_fails_in_production(self, settings: Settings) -> None:
        production = settings.model_copy(update={"environment": "production"})

        result = check_llm_credentials(production)

        assert result.status is CheckStatus.FAIL
        assert "production requires an explicit key" in result.detail


class TestRun:
    """The aggregate must exit non-zero when any check fails."""

    def test_all_checks_pass_on_default_settings(
        self, settings: Settings, isolated_logging: None
    ) -> None:
        results = run(settings)

        assert [result.name for result in results] == [
            "imports",
            "configuration",
            "logging",
            "security_posture",
            "llm_credentials",
        ]
        assert all(result.status is CheckStatus.OK for result in results)

    def test_check_order_is_stable(self, settings: Settings) -> None:
        first = [check.__name__ for check in build_checks(settings)]
        second = [check.__name__ for check in build_checks(settings)]

        assert first == second
        assert first[0] == "check_imports"

    def test_main_returns_zero_when_ready(
        self,
        settings: Settings,
        isolated_logging: None,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr("scripts.health_check.get_settings", lambda: settings)

        assert main() == 0
        assert "imports: ok" in capsys.readouterr().out

    def test_main_returns_one_when_not_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        broken = Settings(_env_file=None).model_copy(update={"environment": "production"})

        monkeypatch.setattr("scripts.health_check.get_settings", lambda: broken)

        assert main() == 1

    def test_output_never_contains_a_secret(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        isolated_logging: None,
    ) -> None:
        configured = _with_key(Settings(_env_file=None), "sk-live-secret")
        monkeypatch.setattr("scripts.health_check.get_settings", lambda: configured)

        main()

        assert "sk-live-secret" not in capsys.readouterr().out
