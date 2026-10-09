"""LangChain-backed LLM adapter.

The only module that imports LangChain. Everything above it depends on the
``LLMClient`` port declared in ``application/llm_service.py``.

Design notes:

* **Provider-agnostic by construction.** LangChain's ``init_chat_model`` resolves
  a provider from a name, so swapping Anthropic for OpenAI or Ollama is a
  configuration change, not a code change.
* **Structured output is parsed, not trusted.** The model is asked for JSON; the
  JSON is parsed into the envelope and validated against the domain model. A
  malformed answer is a failure, never a partial accept.
* **One retry.** A provider that returns malformed JSON once usually returns
  correct JSON when told what it did wrong. Two attempts is the limit; beyond
  that the honest answer is to report failure.
"""

from __future__ import annotations

import json
from typing import Any

from architecture_agent.application.llm_service import (
    LLMRequest,
    LLMResponse,
    ModelUnavailableError,
    StructuredOutputError,
    parse_envelope,
)
from architecture_agent.config.settings import LLMProvider, LLMSettings
from architecture_agent.domain.research import ConfidenceLevel

_RETRY_INSTRUCTION = (
    "Your previous answer was not a single valid JSON object matching the schema. "
    "Return only the JSON object, with no prose and no code fences."
)


class LangChainLLMClient:
    """An ``LLMClient`` backed by a LangChain chat model."""

    def __init__(self, settings: LLMSettings, *, max_attempts: int = 2) -> None:
        """Create the adapter.

        Args:
            settings: Provider, model and generation settings.
            max_attempts: Attempts before giving up on a structured answer.

        Raises:
            ModelUnavailableError: if the provider cannot be initialised, which is
                a configuration problem the caller must be able to act on.
        """
        self._settings = settings
        self._max_attempts = max(1, max_attempts)
        try:
            self._model = self._build_model(settings)
        except Exception as error:
            msg = f"could not initialise the {settings.provider.value} model {settings.model!r}"
            raise ModelUnavailableError(msg) from error

    @property
    def provider(self) -> str:
        """The provider name."""
        return self._settings.provider.value

    @staticmethod
    def _build_model(settings: LLMSettings) -> Any:
        from langchain.chat_models import init_chat_model

        kwargs: dict[str, Any] = {
            "model": settings.model,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "timeout": settings.timeout_seconds,
        }
        if settings.base_url:
            kwargs["base_url"] = settings.base_url
        if settings.api_key is not None:
            kwargs["api_key"] = settings.api_key.get_secret_value()
        return init_chat_model(settings.provider.value, **kwargs)

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Answer ``request``, retrying once on malformed structured output."""
        messages = self._to_messages(request)
        last_error: Exception | None = None

        for attempt in range(self._max_attempts):
            try:
                response = self._model.invoke(messages)
            except Exception as error:
                msg = f"the {self.provider} provider failed to answer"
                raise ModelUnavailableError(msg) from error

            content = _text_of(response)
            prompt_tokens, completion_tokens = _usage_of(response)
            usage = (prompt_tokens, completion_tokens)

            if request.response_model is None:
                return LLMResponse(
                    content=content,
                    model=self._settings.model,
                    provider=self.provider,
                    prompt_tokens=usage[0],
                    completion_tokens=usage[1],
                )

            try:
                envelope = parse_envelope(content)
            except StructuredOutputError as error:
                last_error = error
                if attempt + 1 >= self._max_attempts:
                    break
                messages = (
                    *messages,
                    ("user", _RETRY_INSTRUCTION),
                )
                continue

            return LLMResponse(
                content=content,
                model=self._settings.model,
                provider=self.provider,
                prompt_tokens=usage[0],
                completion_tokens=usage[1],
                confidence=envelope.confidence,
            )

        raise StructuredOutputError(
            f"the model did not return valid structured output after "
            f"{self._max_attempts} attempt(s)"
        ) from last_error

    def _to_messages(self, request: LLMRequest) -> tuple[tuple[str, str], ...]:
        """Render a request as LangChain role/content tuples."""
        return (
            ("system", request.system),
            *((message.role.value, message.content) for message in request.messages),
        )


def _text_of(response: Any) -> str:
    """Extract text from a LangChain message, including tool-call shapes."""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return json.dumps(content, default=str)


def _usage_of(response: Any) -> tuple[int, int]:
    """Extract ``(prompt_tokens, completion_tokens)`` when the provider reports them."""
    metadata = getattr(response, "usage_metadata", None)
    if not isinstance(metadata, dict):
        return (0, 0)
    return (
        int(metadata.get("input_tokens", 0)),
        int(metadata.get("output_tokens", 0)),
    )


def build_client(settings: LLMSettings) -> LangChainLLMClient | object:
    """Return an adapter for ``settings``.

    The ``fake`` provider resolves to the deterministic client so that
    configuration and tests agree on one spelling.
    """
    from architecture_agent.infrastructure.llm.fake_client import FakeLLMClient

    if settings.provider is LLMProvider.FAKE:
        return FakeLLMClient()
    return LangChainLLMClient(settings)


__all__ = ["ConfidenceLevel", "LangChainLLMClient", "build_client"]
