"""Diagram generation use case.

The use case decides *what* to render and guarantees the result is valid. It does
not know Mermaid or PlantUML syntax: that is the renderer's job, reached through
the :class:`DiagramRenderer` port. The port is declared here, next to the use case
that needs it, per ADR-001.
"""

from __future__ import annotations

from typing import Protocol

from architecture_agent.application.errors import RenderingError, UnsupportedRequestError
from architecture_agent.domain.architecture import Architecture
from architecture_agent.domain.diagram import (
    Diagram,
    DiagramIssue,
    DiagramKind,
    DiagramLanguage,
    ValidationSeverity,
)
from architecture_agent.domain.infrastructure import InfrastructurePlan


class DiagramRenderer(Protocol):
    """Renders an architecture into one diagram language.

    Implemented by :class:`~architecture_agent.infrastructure.rendering.mermaid.MermaidRenderer`
    and :class:`~architecture_agent.infrastructure.rendering.plantuml.PlantUmlRenderer`.
    """

    language: str
    """The target language.

    Must equal a :class:`DiagramLanguage` value: the domain has a closed set of
    output languages, so a renderer for a new syntax needs a new enum member
    before it can be registered.
    """

    supported_kinds: frozenset[DiagramKind]

    def supports(self, kind: DiagramKind) -> bool:
        """Whether this renderer can produce ``kind``."""
        ...

    def render(
        self,
        architecture: Architecture,
        kind: DiagramKind,
        *,
        plan: InfrastructurePlan | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        """Render and return the source plus any sanitisation warnings.

        Raises:
            ValueError: if the renderer cannot produce syntactically valid source.
        """
        ...


class DiagramService:
    """Generate a syntactically valid diagram from an architecture."""

    def __init__(self, renderers: tuple[DiagramRenderer, ...]) -> None:
        """Register the renderers available to this service.

        Args:
            renderers: Renderers to offer, in preference order.

        Raises:
            RenderingError: if no renderer is supplied, which would make the
                service silently incapable of producing anything.
        """
        if not renderers:
            msg = "DiagramService requires at least one renderer"
            raise RenderingError(msg)
        self._renderers = renderers

    @property
    def supported_languages(self) -> tuple[str, ...]:
        """Languages this service can produce, in preference order."""
        return tuple(renderer.language for renderer in self._renderers)

    def supported_kinds(self, language: DiagramLanguage) -> tuple[DiagramKind, ...]:
        """Diagram kinds the renderer for ``language`` can produce."""
        renderer = self._renderer_for(language)
        return tuple(sorted(renderer.supported_kinds, key=lambda kind: kind.value))

    def generate(
        self,
        architecture: Architecture,
        kind: DiagramKind,
        language: DiagramLanguage = DiagramLanguage.MERMAID,
        *,
        plan: InfrastructurePlan | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> Diagram:
        """Render ``architecture`` as a validated diagram.

        Args:
            architecture: The architecture to render.
            kind: Diagram kind to produce.
            language: Target diagram language.
            plan: Required for a network topology diagram, which cannot be derived
                from components alone.
            title: Overrides the diagram title; defaults to the architecture name.
            description: Optional prose describing what the diagram shows.

        Returns:
            A validated :class:`Diagram`. Sanitisation warnings are attached; a
            diagram that could not be rendered raises instead of returning broken
            source.

        Raises:
            UnsupportedRequestError: if no registered renderer can produce ``kind`` in
                ``language``.
            RenderingError: if the renderer produced structurally invalid source.
        """
        renderer = self._renderer_for(language)
        if not renderer.supports(kind):
            supported = ", ".join(sorted(item.value for item in renderer.supported_kinds))
            msg = f"{language.value} cannot render {kind.value}; it supports: {supported}"
            raise UnsupportedRequestError(msg)

        if kind is DiagramKind.NETWORK_TOPOLOGY and plan is None:
            msg = "a network topology diagram requires an infrastructure plan"
            raise UnsupportedRequestError(msg)

        try:
            source, warnings = renderer.render(architecture, kind, plan=plan)
        except ValueError as error:
            msg = f"the renderer could not produce valid {language.value} for {kind.value}: {error}"
            raise RenderingError(msg) from error

        issues = tuple(
            DiagramIssue(line=1, message=warning, severity=ValidationSeverity.WARNING)
            for warning in warnings
        )
        diagram = Diagram(
            title=title or architecture.name,
            kind=kind,
            language=language,
            source=source,
            description=description,
            issues=issues,
        )
        return diagram

    def _renderer_for(self, language: DiagramLanguage) -> DiagramRenderer:
        """Return the renderer registered for ``language``."""
        for renderer in self._renderers:
            if renderer.language == language.value:
                return renderer
        available = ", ".join(self.supported_languages)
        msg = f"{language.value} rendering is not available; configured renderers: {available}"
        raise UnsupportedRequestError(msg)
