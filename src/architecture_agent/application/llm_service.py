"""LLM port and the request construction the reasoning use cases depend on.

The port is declared here because the use cases need it, per ADR-001. Concrete
adapters live in ``infrastructure/llm/``; nothing in this module knows that
LangChain exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from architecture_agent.application.errors import ApplicationError
from architecture_agent.domain.research import ConfidenceLevel, SourceTier

_DEFAULT_MAX_TOKENS = 4096
_DEFAULT_TEMPERATURE = 0.2


class ReasoningError(ApplicationError):
    """The model could not produce a usable answer."""

    code = "reasoning_failed"


class ModelUnavailableError(ReasoningError):
    """The provider could not be reached or refused the request."""

    code = "model_unavailable"


class StructuredOutputError(ReasoningError):
    """The model answered, but not in the requested shape."""

    code = "structured_output_failed"


class LLMMessageRole(StrEnum):
    """Conversation roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class LLMMessage:
    """One message in a prompt."""

    role: LLMMessageRole
    content: str

    def to_dict(self) -> dict[str, str]:
        """Render as a provider-neutral mapping."""
        return {"role": self.role.value, "content": self.content}


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """A complete model call."""

    messages: tuple[LLMMessage, ...]
    system: str
    max_tokens: int = _DEFAULT_MAX_TOKENS
    temperature: float = _DEFAULT_TEMPERATURE
    response_model: type[BaseModel] | None = None
    request_id: str | None = None

    def with_evidence(self, rendered: str) -> LLMRequest:
        """Return a copy whose final user message carries rendered evidence."""
        if not rendered:
            return self
        messages = (
            *self.messages[:-1],
            LLMMessage(
                role=LLMMessageRole.USER,
                content=f"{self.messages[-1].content}\n\n{rendered}",
            ),
        )
        return LLMRequest(
            messages=messages,
            system=self.system,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            response_model=self.response_model,
            request_id=self.request_id,
        )


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """A model answer with the provenance a caller needs to trust it."""

    content: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    degraded: bool = False
    degradation_reason: str | None = None


class LLMClient(Protocol):
    """Generates text or a validated structured object."""

    provider: str

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Answer ``request``.

        Raises:
            ModelUnavailableError: if the provider cannot be reached.
            StructuredOutputError: if ``request.response_model`` is set and the
                answer does not satisfy it.
        """
        ...


class _Envelope(BaseModel):
    """Container the model is asked to fill when a schema is required."""

    model_config = ConfigDict(extra="forbid")

    result: dict[str, object] = Field(default_factory=dict)
    reasoning_summary: str = Field(default="", max_length=4_000)
    assumptions: tuple[str, ...] = ()
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM


class ReasoningOutcome(BaseModel):
    """A validated structured answer plus what the model claimed about it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    payload: dict[str, object]
    reasoning_summary: str = ""
    assumptions: tuple[str, ...] = ()
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    model: str = ""
    provider: str = ""
    sources: tuple[str, ...] = ()
    degraded: bool = False
    degradation_reason: str | None = None

    def as_model(self, model_type: type[_ModelT]) -> _ModelT:
        """Validate the payload against ``model_type``.

        Raises:
            StructuredOutputError: if the payload does not satisfy the model.
        """
        try:
            return model_type.model_validate(self.payload)
        except ValidationError as error:
            raise StructuredOutputError(
                f"the model returned a payload that does not satisfy "
                f"{model_type.__name__}: {error.error_count()} problem(s)"
            ) from error


_ModelT = TypeVar("_ModelT", bound=BaseModel)

_ENVELOPE_SCHEMA = _Envelope.model_json_schema()


def build_structured_request(
    *,
    system: str,
    instruction: str,
    context: tuple[str, ...] = (),
    response_model: type[BaseModel],
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    temperature: float = _DEFAULT_TEMPERATURE,
    request_id: str | None = None,
) -> LLMRequest:
    """Build a request that asks for JSON conforming to ``response_model``.

    The schema is embedded in the instruction rather than passed as a tool
    argument, because not every provider supports structured output and a plain
    JSON contract works everywhere.
    """
    import json

    sections = [f"Task:\n{instruction}"]
    if context:
        sections.append("Context supplied by the caller:\n" + "\n".join(context))
    sections.append(
        "Respond with a single JSON object and nothing else. It must match this "
        f"schema exactly:\n{json.dumps(_ENVELOPE_SCHEMA, indent=2)}\n"
        f"The `result` field must match this schema exactly:\n"
        f"{json.dumps(response_model.model_json_schema(), indent=2)}\n"
        "Put only reasoning you are willing to show the user in `reasoning_summary`. "
        "Never include hidden chain-of-thought."
    )
    return LLMRequest(
        messages=(LLMMessage(role=LLMMessageRole.USER, content="\n\n".join(sections)),),
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        response_model=response_model,
        request_id=request_id,
    )


def parse_envelope(text: str) -> _Envelope:
    """Parse a model answer into the envelope.

    Raises:
        StructuredOutputError: if the answer is not a JSON object.
    """
    import json

    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    try:
        payload = json.loads(candidate)
    except ValueError as error:
        raise StructuredOutputError(
            "the model did not return JSON; it must return a single JSON object"
        ) from error
    if not isinstance(payload, dict):
        raise StructuredOutputError("the model returned JSON that is not an object")
    try:
        return _Envelope.model_validate(payload)
    except ValidationError as error:
        raise StructuredOutputError(
            f"the model's JSON envelope is invalid: {error.error_count()} problem(s)"
        ) from error


__all__ = [
    "LLMClient",
    "LLMMessage",
    "LLMMessageRole",
    "LLMRequest",
    "LLMResponse",
    "ModelUnavailableError",
    "ReasoningError",
    "ReasoningOutcome",
    "SourceTier",
    "StructuredOutputError",
    "build_structured_request",
    "parse_envelope",
]
