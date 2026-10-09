"""Typed configuration for the Architecture Agent.

Every environment-specific value and every credential enters the system through
this module. Nothing else may read ``os.environ`` directly.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENV_PREFIX = "ARCH_AGENT_"


class LogLevel(StrEnum):
    """Supported log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogFormat(StrEnum):
    """Supported log serialization formats."""

    JSON = "json"
    TEXT = "text"


class LLMProvider(StrEnum):
    """LLM providers supported by the agent adapter layer."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    OLLAMA = "ollama"
    FAKE = "fake"


class SearchProvider(StrEnum):
    """Web research providers."""

    DUCKDUCKGO = "duckduckgo"
    FAKE = "fake"


class EmbeddingProvider(StrEnum):
    """Embedding backends for the knowledge layer."""

    LOCAL = "local"
    OPENAI = "openai"
    FAKE = "fake"


class McpTransport(StrEnum):
    """MCP transports supported by the server."""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable-http"


class LLMSettings(BaseModel):
    """LLM provider configuration.

    ``api_key`` is optional: when omitted the provider adapter is expected to
    fall back to the vendor's own environment variable. The value is a
    :class:`~pydantic.SecretStr` so it can never leak through ``repr`` or logs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: LLMProvider = LLMProvider.ANTHROPIC
    model: str = Field(default="claude-sonnet-4-5", min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, ge=256, le=200_000)
    timeout_seconds: float = Field(default=120.0, gt=0.0, le=600.0)
    api_key: SecretStr | None = None
    base_url: str | None = None

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(("http://", "https://")):
            msg = "base_url must be an absolute http(s) URL"
            raise ValueError(msg)
        return value.rstrip("/")


class SearchSettings(BaseModel):
    """Web research configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: SearchProvider = SearchProvider.DUCKDUCKGO
    max_results: int = Field(default=5, ge=1, le=25)
    timeout_seconds: float = Field(default=20.0, gt=0.0, le=300.0)
    region: str = Field(default="wt-wt", min_length=2, max_length=16)


class CacheSettings(BaseModel):
    """Cache TTLs in seconds.

    Dynamic domains (pricing, versions) get shorter TTLs than stable ones.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    search_ttl_seconds: int = Field(default=3600, ge=0)
    knowledge_ttl_seconds: int = Field(default=86_400, ge=0)
    pricing_ttl_seconds: int = Field(default=21_600, ge=0)
    max_entries: int = Field(default=512, ge=1, le=100_000)


class KnowledgeSettings(BaseModel):
    """Knowledge ingestion and retrieval configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    directory: Path = Path(".knowledge")
    chunk_size_tokens: int = Field(default=512, ge=64, le=8192)
    chunk_overlap_tokens: int = Field(default=64, ge=0)
    embedding_provider: EmbeddingProvider = EmbeddingProvider.LOCAL
    embedding_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2", min_length=1)

    @field_validator("chunk_overlap_tokens")
    @classmethod
    def _validate_overlap(cls, value: int, info: object) -> int:
        data = getattr(info, "data", {})
        size = data.get("chunk_size_tokens")
        if isinstance(size, int) and value >= size:
            msg = "chunk_overlap_tokens must be smaller than chunk_size_tokens"
            raise ValueError(msg)
        return value


class MCPSettings(BaseModel):
    """MCP server configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transport: McpTransport = McpTransport.STDIO
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65_535)
    instructions: str | None = None

    @field_validator("instructions")
    @classmethod
    def _validate_instructions(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value


class SecuritySettings(BaseModel):
    """Security posture of the agent itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_domains: Annotated[tuple[str, ...], NoDecode] = ()
    max_fetched_characters: int = Field(default=200_000, ge=1_000)
    redact_logs: bool = True
    allow_execution_tools: bool = False

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def _parse_domains(cls, value: object) -> object:
        """Accept a comma-separated string, and treat empty as "not configured".

        Two real problems are solved here. Pydantic-settings JSON-decodes tuple
        fields, so an unset variable arriving as an empty string fails to parse and
        the process will not start — yet an empty value is the most natural way to
        express "not configured", which is also the default. And the documented
        format in ``.env.example`` is comma-separated, which JSON decoding rejects
        anyway. ``NoDecode`` turns the automatic decoding off so this validator owns
        the parsing.
        """
        if isinstance(value, str):
            return tuple(part for part in (item.strip() for item in value.split(",")) if part)
        if isinstance(value, list | tuple | set | frozenset):
            return tuple(str(item) for item in value)
        return value

    @field_validator("allowed_domains")
    @classmethod
    def _normalize_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for raw in value:
            domain = raw.strip().lower().lstrip(".")
            if not domain:
                continue
            if "/" in domain or "://" in domain:
                msg = f"allowed_domains must be bare hostnames, got {raw!r}"
                raise ValueError(msg)
            normalized.append(domain)
        return tuple(dict.fromkeys(normalized))

    def is_domain_allowed(self, host: str) -> bool:
        """Return whether ``host`` is permitted for outbound fetches.

        An empty allowlist means "no restriction", matching the behaviour of a
        local research tool. Callers that need egress control must configure it.
        """
        if not self.allowed_domains:
            return True
        candidate = host.strip().lower().rstrip(".")
        return any(
            candidate == allowed or candidate.endswith(f".{allowed}")
            for allowed in self.allowed_domains
        )


class Settings(BaseSettings):
    """Root configuration object.

    Logging is configured from flat fields (``ARCH_AGENT_LOG_LEVEL``) rather than
    from a nested section, because it is the knob operators tune most often and
    the flat form avoids an awkward double-underscore variable name. Every other
    concern is nested under its own section.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: str = Field(default="development", min_length=1, max_length=32)
    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.JSON
    log_include_stacktraces: bool = False
    service_name: str = Field(default="architecture-agent", min_length=1, max_length=128)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)

    @property
    def is_production(self) -> bool:
        """Whether the process believes it runs in production."""
        return self.environment.lower() in {"prod", "production"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
