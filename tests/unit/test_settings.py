"""Configuration behaviour: environment wiring, validation and secret handling."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from architecture_agent.config.settings import (
    EmbeddingProvider,
    LLMProvider,
    LLMSettings,
    LogFormat,
    LogLevel,
    McpTransport,
    SearchProvider,
    SecuritySettings,
    Settings,
    get_settings,
)

pytestmark = pytest.mark.unit


class TestEnvironmentWiring:
    """Settings must come from the environment, not from code."""

    def test_defaults_are_production_safe(self, settings: Settings) -> None:
        assert settings.environment == "development"
        assert settings.log_level is LogLevel.INFO
        assert settings.log_format is LogFormat.JSON
        assert settings.service_name == "architecture-agent"
        assert settings.mcp.transport is McpTransport.STDIO
        assert settings.search.provider is SearchProvider.DUCKDUCKGO
        assert settings.knowledge.embedding_provider is EmbeddingProvider.LOCAL

    def test_prefixed_and_nested_environment_overrides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ARCH_AGENT_ENVIRONMENT", "production")
        monkeypatch.setenv("ARCH_AGENT_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("ARCH_AGENT_LLM__PROVIDER", "openai")
        monkeypatch.setenv("ARCH_AGENT_LLM__MODEL", "gpt-4.1")
        monkeypatch.setenv("ARCH_AGENT_LLM__TEMPERATURE", "0.7")
        monkeypatch.setenv("ARCH_AGENT_MCP__PORT", "9001")

        settings = Settings(_env_file=None)

        assert settings.environment == "production"
        assert settings.is_production is True
        assert settings.log_level is LogLevel.DEBUG
        assert settings.llm.provider is LLMProvider.OPENAI
        assert settings.llm.model == "gpt-4.1"
        assert settings.llm.temperature == pytest.approx(0.7)
        assert settings.mcp.port == 9001

    def test_unprefixed_variables_are_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM__PROVIDER", "ollama")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used")

        settings = Settings(_env_file=None)

        assert settings.llm.provider is LLMProvider.ANTHROPIC
        assert settings.llm.api_key is None

    def test_settings_are_immutable(self, settings: Settings) -> None:
        with pytest.raises(ValidationError):
            settings.environment = "production"  # type: ignore[misc]

    def test_get_settings_is_cached(self) -> None:
        assert get_settings() is get_settings()


class TestSecretHandling:
    """Credentials must never be printable."""

    def test_api_key_is_masked_in_repr(self) -> None:
        llm = LLMSettings(api_key=SecretStr("sk-super-secret-value"))

        assert "sk-super-secret-value" not in repr(llm)
        assert "**********" in repr(llm)
        assert llm.api_key is not None
        assert llm.api_key.get_secret_value() == "sk-super-secret-value"

    def test_model_dump_keeps_the_secret_usable_but_not_plain(self) -> None:
        llm = LLMSettings(api_key=SecretStr("sk-super-secret-value"))

        assert llm.model_dump()["api_key"] != "sk-super-secret-value"
        assert llm.model_dump(mode="json")["api_key"] != "sk-super-secret-value"

    def test_settings_repr_does_not_leak_the_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARCH_AGENT_LLM__API_KEY", "sk-leak-me")

        settings = Settings(_env_file=None)

        assert "sk-leak-me" not in repr(settings)


class TestValidation:
    """Invalid configuration must fail at construction, not at first use."""

    @pytest.mark.parametrize(
        ("temperature", "message"),
        [(-0.1, "greater than or equal to 0"), (2.5, "less than or equal to 2")],
    )
    def test_temperature_bounds(self, temperature: float, message: str) -> None:
        with pytest.raises(ValidationError, match=message):
            LLMSettings(temperature=temperature)

    def test_base_url_must_be_absolute_http(self) -> None:
        assert LLMSettings(base_url="https://proxy.internal/v1/").base_url == (
            "https://proxy.internal/v1"
        )
        with pytest.raises(ValidationError, match="absolute http"):
            LLMSettings(base_url="proxy.internal")

    def test_chunk_overlap_must_be_smaller_than_chunk_size(self) -> None:
        with pytest.raises(ValidationError, match="must be smaller than"):
            Settings(_env_file=None).knowledge.__class__(
                chunk_size_tokens=128, chunk_overlap_tokens=128
            )

    def test_mcp_instructions_blank_becomes_none(self) -> None:
        assert Settings(_env_file=None).mcp.__class__(instructions="   ").instructions is None


class TestSecuritySettings:
    """The agent's own egress and posture controls."""

    def test_empty_allowlist_permits_everything(self) -> None:
        security = SecuritySettings()

        assert security.is_domain_allowed("docs.aws.amazon.com") is True

    def test_allowlist_matches_exact_and_subdomains(self) -> None:
        security = SecuritySettings(allowed_domains=("AWS.amazon.com", ".Cloudflare.com"))

        assert security.is_domain_allowed("aws.amazon.com") is True
        assert security.is_domain_allowed("docs.aws.amazon.com") is True
        assert security.is_domain_allowed("cloudflare.com") is True
        assert security.is_domain_allowed("evil-cloudflare.com") is False
        assert security.is_domain_allowed("aws.amazon.com.attacker.test") is False

    def test_allowlist_rejects_urls_and_normalises_entries(self) -> None:
        with pytest.raises(ValidationError, match="bare hostnames"):
            SecuritySettings(allowed_domains=("https://docs.aws.amazon.com",))
        assert SecuritySettings(allowed_domains=(" Docs.AWS.Amazon.com ", "")).allowed_domains == (
            "docs.aws.amazon.com",
        )

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", ()),
            ("   ", ()),
            ("a.com,b.com", ("a.com", "b.com")),
            (" a.com , b.com ", ("a.com", "b.com")),
            ("a.com,,b.com", ("a.com", "b.com")),
        ],
    )
    def test_the_allowlist_survives_how_an_operator_writes_it(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: tuple[str, ...]
    ) -> None:
        """An empty variable means "not configured" and must not stop the process.

        Pydantic-settings JSON-decodes tuple fields, so an unset variable arriving as
        an empty string fails to parse. An empty value is also the most natural way
        to express "not configured", and the documented format is comma-separated,
        which JSON decoding rejects as well.
        """
        monkeypatch.setenv("ARCH_AGENT_SECURITY__ALLOWED_DOMAINS", raw)

        assert Settings(_env_file=None).security.allowed_domains == expected

    def test_execution_tools_are_disabled_by_default(self, settings: Settings) -> None:
        assert settings.security.allow_execution_tools is False
        assert settings.security.redact_logs is True
