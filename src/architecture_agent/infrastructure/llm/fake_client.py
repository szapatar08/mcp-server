"""Deterministic LLM adapter for tests and offline operation.

Not a mock: this is the ``fake`` provider the configuration exposes. It returns
canned answers from a scripted queue, so a test can assert on a full reasoning
pipeline with no network and no credential, and the agent remains usable on a
machine with no provider configured.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from architecture_agent.application.llm_service import (
    LLMRequest,
    LLMResponse,
    StructuredOutputError,
)
from architecture_agent.domain.research import ConfidenceLevel

Provider = str
ScriptedAnswer = str | Callable[[LLMRequest], str]


@dataclass(slots=True)
class FakeLLMClient:
    """A scripted model.

    Attributes:
        provider: The provider name reported in responses.
        calls: Every request received, in order. Tests assert on this rather than
            on mock call counts so they can inspect the actual prompt.
    """

    provider: Provider = "fake"
    answers: list[ScriptedAnswer] = field(default_factory=list)
    model: str = "fake-deterministic"
    calls: list[LLMRequest] = field(default_factory=list)
    fail_with: Exception | None = None

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Return the next scripted answer.

        Raises:
            RuntimeError: if ``fail_with`` is set, to exercise the failure path.
            StructuredOutputError: if the script is exhausted and a schema was
                required. An exhausted script is a test bug, so it fails loudly
                rather than inventing an answer.
        """
        self.calls.append(request)
        if self.fail_with is not None:
            raise self.fail_with

        if not self.answers:
            if request.response_model is not None:
                msg = (
                    "the fake provider ran out of scripted answers while a "
                    "response model was required"
                )
                raise StructuredOutputError(msg)
            return LLMResponse(
                content="The fake provider has no scripted answer.",
                model=self.model,
                provider=self.provider,
            )

        answer = self.answers.pop(0)
        content = answer(request) if callable(answer) else answer
        return LLMResponse(
            content=content,
            model=self.model,
            provider=self.provider,
            confidence=_confidence_of(content),
        )


def envelope(result: dict[str, object], **overrides: object) -> str:
    """Build a well-formed envelope body for a scripted answer."""
    payload: dict[str, object] = {"result": result}
    payload.update(overrides)
    return json.dumps(payload)


def _confidence_of(content: str) -> ConfidenceLevel:
    """Report confidence in a scripted answer.

    Always ``LOW``: the fake provider's answers are fixtures, not evidence, and a
    test that treats one as authoritative would be testing the wrong thing.
    """
    del content
    return ConfidenceLevel.LOW


def scripted(*answers: ScriptedAnswer) -> FakeLLMClient:
    """Convenience constructor for a client with a fixed script."""
    return FakeLLMClient(answers=list(answers))


def constant(text: str) -> Callable[[LLMRequest], str]:
    """Return a scripted answer function that ignores the request."""
    return lambda _request: text


def echo_effective(content: str) -> Callable[[LLMRequest], str]:
    """Return a scripted answer echoing the prompt, for structure-presence tests."""

    def _answer(request: LLMRequest) -> str:
        _ = request.messages
        return content

    return _answer


def assert_single_script(client: FakeLLMClient) -> None:
    """Assert the script was consumed exactly once."""
    if client.answers:
        msg = f"{len(client.answers)} scripted answer(s) were never requested"
        raise AssertionError(msg)


__all__: Sequence[str] = (
    "FakeLLMClient",
    "ScriptedAnswer",
    "assert_single_script",
    "constant",
    "echo_effective",
    "envelope",
    "scripted",
)
