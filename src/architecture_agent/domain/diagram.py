"""Diagram request and validation models (FR-015).

Diagram sources are generated, not fetched. They must be syntactically valid
before being returned, so the model carries its own validation issues rather
than relying on the caller to check.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DiagramKind(StrEnum):
    """Supported diagram types."""

    CLASS = "class"
    SEQUENCE = "sequence"
    COMPONENT = "component"
    DEPLOYMENT = "deployment"
    ACTIVITY = "activity"
    STATE = "state"
    C4_CONTEXT = "c4_context"
    C4_CONTAINER = "c4_container"
    C4_COMPONENT = "c4_component"
    C4_CODE = "c4_code"
    ER = "er"
    FLOWCHART = "flowchart"
    NETWORK_TOPOLOGY = "network_topology"


class DiagramLanguage(StrEnum):
    """Rendering syntax."""

    MERMAID = "mermaid"
    PLANTUML = "plantuml"


class ValidationSeverity(IntEnum):
    """Severity of a syntax problem, ordered."""

    WARNING = 1
    ERROR = 2


class DiagramIssue(BaseModel):
    """A syntax or semantic problem found in a generated diagram."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    line: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=512)
    severity: ValidationSeverity = ValidationSeverity.ERROR

    def __str__(self) -> str:
        """Render as ``line N: message``."""
        return f"line {self.line}: {self.message}"


class Diagram(BaseModel):
    """A rendered diagram source plus its validation outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=256)
    kind: DiagramKind
    language: DiagramLanguage = DiagramLanguage.MERMAID
    source: str = Field(min_length=1)
    description: str | None = Field(default=None, max_length=2_000)
    issues: tuple[DiagramIssue, ...] = ()

    @field_validator("source")
    @classmethod
    def _non_empty_lines(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "diagram source cannot be blank"
            raise ValueError(msg)
        return stripped

    @model_validator(mode="after")
    def _only_warnings_remain(self) -> Self:
        blocking = [issue for issue in self.issues if issue.severity is ValidationSeverity.ERROR]
        if blocking:
            msg = "diagram contains blocking syntax errors: " + "; ".join(
                str(issue) for issue in blocking
            )
            raise ValueError(msg)
        return self

    @property
    def is_valid(self) -> bool:
        """Whether the diagram has no blocking syntax issues.

        Warnings — a sanitised label, a suspicious reference — do not make a
        diagram invalid; they make it lossy, and the issues say so.
        """
        return all(issue.severity is ValidationSeverity.WARNING for issue in self.issues)

    @property
    def code_fence_language(self) -> str:
        """Language identifier to use when embedding in Markdown."""
        return self.language.value
