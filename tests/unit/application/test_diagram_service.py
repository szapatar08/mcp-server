"""Renderer behaviour and diagram use case."""

from __future__ import annotations

import pytest

from architecture_agent.application.diagram_service import (
    DiagramRenderer,
    DiagramService,
)
from architecture_agent.application.errors import (
    RenderingError,
    UnsupportedRequestError,
)
from architecture_agent.domain.architecture import Architecture
from architecture_agent.domain.diagram import DiagramKind, DiagramLanguage, ValidationSeverity
from architecture_agent.domain.infrastructure import InfrastructurePlan
from architecture_agent.infrastructure.rendering import (
    MERMAID,
    PLANTUML,
    MermaidRenderer,
    PlantUmlRenderer,
    find_issues,
)

pytestmark = pytest.mark.unit

_MERMAID_KINDS = (
    DiagramKind.C4_CONTEXT,
    DiagramKind.C4_CONTAINER,
    DiagramKind.C4_COMPONENT,
    DiagramKind.C4_CODE,
    DiagramKind.CLASS,
    DiagramKind.SEQUENCE,
    DiagramKind.COMPONENT,
    DiagramKind.DEPLOYMENT,
    DiagramKind.ER,
    DiagramKind.FLOWCHART,
    DiagramKind.NETWORK_TOPOLOGY,
)
_PLANTUML_KINDS = (
    DiagramKind.C4_CONTEXT,
    DiagramKind.C4_CONTAINER,
    DiagramKind.C4_COMPONENT,
    DiagramKind.C4_CODE,
    DiagramKind.CLASS,
    DiagramKind.SEQUENCE,
    DiagramKind.COMPONENT,
    DiagramKind.DEPLOYMENT,
)


class TestRendererContract:
    """A renderer that emits invalid source must fail, not return broken output."""

    @pytest.mark.parametrize("kind", _MERMAID_KINDS)
    def test_mermaid_output_is_always_structurally_valid(
        self,
        kind: DiagramKind,
        minimal_architecture: Architecture,
        weak_infrastructure: InfrastructurePlan,
    ) -> None:
        source, _ = MermaidRenderer().render(minimal_architecture, kind, plan=weak_infrastructure)
        blocking = [
            issue
            for issue in find_issues(source, MERMAID)
            if issue.severity is ValidationSeverity.ERROR
        ]

        assert blocking == []

    @pytest.mark.parametrize("kind", _PLANTUML_KINDS)
    def test_plantuml_output_is_always_structurally_valid(
        self, kind: DiagramKind, minimal_architecture: Architecture
    ) -> None:
        source, _ = PlantUmlRenderer().render(minimal_architecture, kind)
        blocking = [
            issue
            for issue in find_issues(source, PLANTUML)
            if issue.severity is ValidationSeverity.ERROR
        ]

        assert blocking == []

    @pytest.mark.parametrize("kind", list(DiagramKind))
    def test_unsupported_kinds_are_refused_by_both_renderers(
        self, kind: DiagramKind, minimal_architecture: Architecture
    ) -> None:
        for renderer in (MermaidRenderer(), PlantUmlRenderer()):
            if renderer.supports(kind):
                continue
            with pytest.raises(ValueError, match="cannot render"):
                renderer.render(minimal_architecture, kind)

    def test_a_clean_architecture_renders_without_warnings(
        self, minimal_architecture: Architecture
    ) -> None:
        source, warnings = MermaidRenderer().render(minimal_architecture, DiagramKind.C4_CONTAINER)

        assert warnings == ()
        assert "Checkout platform" in source

    def test_identifiers_are_positional_not_derived_from_names(
        self, minimal_architecture: Architecture
    ) -> None:
        source, _ = MermaidRenderer().render(minimal_architecture, DiagramKind.C4_CONTAINER)

        assert "Container(c1, " in source
        assert "Container(c4" not in source

    def test_a_network_diagram_places_databases_in_the_data_segment(
        self, minimal_architecture: Architecture, weak_infrastructure: InfrastructurePlan
    ) -> None:
        source, _ = MermaidRenderer().render(
            minimal_architecture, DiagramKind.NETWORK_TOPOLOGY, plan=weak_infrastructure
        )

        assert "c2 -.-> db1" in source
        assert "postgresql" in source


class TestDiagramService:
    """The use case owns validity and language selection, not syntax."""

    def test_it_requires_at_least_one_renderer(self) -> None:
        with pytest.raises(RenderingError, match="at least one renderer"):
            DiagramService(())

    def test_it_reports_its_languages(self, diagram_service: DiagramService) -> None:
        assert diagram_service.supported_languages == ("mermaid", "plantuml")

    def test_it_reports_supported_kinds_per_language(self, diagram_service: DiagramService) -> None:
        mermaid = diagram_service.supported_kinds(DiagramLanguage.MERMAID)
        plantuml = diagram_service.supported_kinds(DiagramLanguage.PLANTUML)

        assert DiagramKind.C4_CONTAINER in mermaid
        assert DiagramKind.NETWORK_TOPOLOGY in mermaid
        assert DiagramKind.NETWORK_TOPOLOGY not in plantuml

    def test_an_unavailable_language_is_reported_with_the_alternatives(
        self, minimal_architecture: Architecture
    ) -> None:
        class NullOnly:
            language = "mermaid"
            supported_kinds = frozenset({DiagramKind.CLASS})

            def supports(self, kind: DiagramKind) -> bool:
                return kind is DiagramKind.CLASS

            def render(
                self, architecture: Architecture, kind: DiagramKind, *, plan: object | None = None
            ) -> tuple[str, tuple[str, ...]]:
                return ("classDiagram", ())

        service = DiagramService((NullOnly(),))

        with pytest.raises(
            UnsupportedRequestError, match="not available; configured renderers: mermaid"
        ):
            service.generate(minimal_architecture, DiagramKind.CLASS, DiagramLanguage.PLANTUML)

    def test_an_unsupported_kind_lists_what_is_available(
        self, diagram_service: DiagramService, minimal_architecture: Architecture
    ) -> None:
        with pytest.raises(UnsupportedRequestError, match="it supports: c4_code"):
            diagram_service.generate(
                minimal_architecture, DiagramKind.STATE, DiagramLanguage.PLANTUML
            )

    def test_a_network_diagram_demands_an_infrastructure_plan(
        self, diagram_service: DiagramService, minimal_architecture: Architecture
    ) -> None:
        with pytest.raises(UnsupportedRequestError, match="requires an infrastructure plan"):
            diagram_service.generate(minimal_architecture, DiagramKind.NETWORK_TOPOLOGY)

    def test_a_renderer_failure_is_wrapped_not_leaked(
        self, minimal_architecture: Architecture
    ) -> None:
        class Broken:
            language = "mermaid"
            supported_kinds = frozenset({DiagramKind.C4_CONTAINER})

            def supports(self, kind: DiagramKind) -> bool:
                return kind is DiagramKind.C4_CONTAINER

            def render(
                self, architecture: Architecture, kind: DiagramKind, *, plan: object | None = None
            ) -> tuple[str, tuple[str, ...]]:
                msg = "unbalanced double quote at line 3"
                raise ValueError(msg)

        with pytest.raises(RenderingError, match="could not produce valid mermaid"):
            DiagramService((Broken(),)).generate(minimal_architecture, DiagramKind.C4_CONTAINER)

    def test_the_title_can_be_overridden(
        self, diagram_service: DiagramService, minimal_architecture: Architecture
    ) -> None:
        diagram = diagram_service.generate(
            minimal_architecture, DiagramKind.C4_CONTAINER, title="Proposed target state"
        )

        assert diagram.title == "Proposed target state"

    def test_warnings_become_diagram_issues(
        self, diagram_service: DiagramService, minimal_architecture: Architecture
    ) -> None:
        lossy = minimal_architecture.model_copy(update={"name": 'Platform "prod" %%{init: {}}%%'})
        diagram = diagram_service.generate(lossy, DiagramKind.C4_CONTAINER)

        assert diagram.issues
        assert all(issue.severity is ValidationSeverity.WARNING for issue in diagram.issues)
        assert diagram.is_valid is True


class TestRendererSubstitution:
    """The port exists so a third renderer can be added without touching the use case."""

    def test_the_service_accepts_any_object_satisfying_the_port(
        self, minimal_architecture: Architecture
    ) -> None:
        """A different renderer for the same language needs no use-case change."""

        class Recording:
            language = "mermaid"
            supported_kinds = frozenset({DiagramKind.C4_CONTAINER})

            def __init__(self) -> None:
                self.calls: list[DiagramKind] = []

            def supports(self, kind: DiagramKind) -> bool:
                return kind is DiagramKind.C4_CONTAINER

            def render(
                self,
                architecture: Architecture,
                kind: DiagramKind,
                *,
                plan: object | None = None,
            ) -> tuple[str, tuple[str, ...]]:
                self.calls.append(kind)
                return ('flowchart LR\n    c1["recorded"]', ("recorded format",))

        recording = Recording()
        service = DiagramService((recording,))

        diagram = service.generate(minimal_architecture, DiagramKind.C4_CONTAINER)

        assert recording.calls == [DiagramKind.C4_CONTAINER]
        assert diagram.language is DiagramLanguage.MERMAID
        assert "recorded" in diagram.source
        assert str(diagram.issues[0]) == "line 1: recorded format"

    def test_the_port_is_satisfied_by_the_production_renderers(self) -> None:
        def accepts(renderer: DiagramRenderer) -> str:
            return renderer.language

        assert accepts(MermaidRenderer()) == "mermaid"
        assert accepts(PlantUmlRenderer()) == "plantuml"
