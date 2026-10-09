"""Diagram syntax safety: identifier assignment and label sanitisation.

These tests are the security boundary for T7 (hostile content reaching a
renderer). They assert that input cannot influence diagram *structure*, not merely
that output looks plausible.
"""

from __future__ import annotations

import re

import pytest

from architecture_agent.domain.architecture import (
    Architecture,
    ComponentKind,
    Interaction,
    SystemComponent,
)
from architecture_agent.infrastructure.rendering import (
    IdentifierMap,
    MermaidRenderer,
    PlantUmlRenderer,
    is_valid_identifier,
    sanitize_label,
)
from architecture_agent.infrastructure.rendering.syntax import MERMAID, PLANTUML, find_issues

pytestmark = [pytest.mark.unit, pytest.mark.security]


class TestIdentifierAssignment:
    """Identifiers must be positional, never derived from input."""

    def test_aliases_are_positional_and_stable(self) -> None:
        identifiers = IdentifierMap.assign(["storefront", "checkout-api", "orders-db"])

        assert identifiers.alias("storefront") == "c1"
        assert identifiers.alias("orders-db") == "c3"
        assert identifiers.names == ("storefront", "checkout-api", "orders-db")

    def test_a_repeated_name_keeps_its_first_alias(self) -> None:
        identifiers = IdentifierMap.assign(["api", "db", "api"])

        assert identifiers.alias("api") == "c1"
        assert len(identifiers.names) == 2

    def test_every_generated_alias_is_a_valid_identifier(self) -> None:
        identifiers = IdentifierMap.assign([f"host-{index}" for index in range(50)])

        assert all(is_valid_identifier(alias) for alias in identifiers.aliases.values())

    def test_membership_is_supported(self) -> None:
        identifiers = IdentifierMap.assign(["api"])

        assert "api" in identifiers
        assert "unknown" not in identifiers

    def test_an_unregistered_name_is_a_bug_guard(self) -> None:
        identifiers = IdentifierMap.assign(["api"])

        with pytest.raises(KeyError):
            identifiers.alias("never-registered")

    def test_a_hostile_name_cannot_reach_an_identifier(self) -> None:
        hostile = 'A" --> B; malicious "x'
        identifiers = IdentifierMap.assign([hostile])

        assert is_valid_identifier(identifiers.alias(hostile)) is True
        assert identifiers.alias(hostile) == "c1"


class TestLabelSanitisation:
    """Labels may be altered but must never break structure."""

    @pytest.mark.parametrize(
        ("hostile", "must_not_contain"),
        [
            ('label" --> injected', '"'),
            ("label\n  %%{init: {'x':'y'}}%%", "\n"),
            ("label --> B", "-->"),
            ("label[[link]]", "["),
            ("label;;", ";"),
            ("label{a}", "{"),
            ("label`tick`", "`"),
            ("label\\backslash", "\\"),
        ],
    )
    def test_structural_characters_are_removed(self, hostile: str, must_not_contain: str) -> None:
        result = sanitize_label(hostile)

        assert must_not_contain not in result.value
        assert "\n" not in result.value
        assert result.modified is True

    def test_safe_labels_pass_through_unchanged(self) -> None:
        result = sanitize_label("checkout-api v2.1 (primary)")

        assert result.value == "checkout-api v2.1 primary"
        assert result.modified is True

        clean = sanitize_label("checkout-api")

        assert clean.value == "checkout-api"
        assert clean.modified is False

    def test_labels_are_length_bounded(self) -> None:
        result = sanitize_label("x" * 500, max_chars=40)

        assert len(result.value) == 40
        assert result.original_length == 500

    def test_an_empty_label_becomes_a_placeholder(self) -> None:
        for hostile in ('"""', ";;;", "   ", "[[]]"):
            assert sanitize_label(hostile).value == "unnamed"

    def test_an_arrow_is_defanged_to_harmless_dashes(self) -> None:
        assert sanitize_label("-->").value == "--"


class TestRenderersRejectInjection:
    """End-to-end: a hostile architecture still renders valid, contained diagrams."""

    @pytest.fixture
    def hostile_architecture(self, minimal_architecture: Architecture) -> Architecture:
        """An architecture carrying injection attempts in every text position."""
        injection = 'A" --> B; System("owned")'
        components = (
            *minimal_architecture.components,
            SystemComponent(
                name=injection,
                kind=ComponentKind.SERVICE,
                responsibility=injection,
                technology=(injection,),
            ),
        )
        return minimal_architecture.model_copy(
            update={
                "name": 'Platform "prod"',
                "components": components,
                "interactions": (
                    *minimal_architecture.interactions,
                    Interaction(
                        source=injection,
                        target="orders-db",
                        protocol='https" --> System_Ext(evil, "owned")',
                        description=injection,
                    ),
                ),
            }
        )

    @pytest.mark.parametrize("renderer", [MermaidRenderer(), PlantUmlRenderer()])
    def test_hostile_names_produce_valid_source(
        self, hostile_architecture: Architecture, renderer: MermaidRenderer | PlantUmlRenderer
    ) -> None:
        from architecture_agent.domain.diagram import DiagramKind, ValidationSeverity

        source, warnings = renderer.render(hostile_architecture, DiagramKind.C4_CONTAINER)

        blocking = [
            issue
            for issue in find_issues(
                source, MERMAID if renderer.language == "mermaid" else PLANTUML
            )
            if issue.severity is ValidationSeverity.ERROR
        ]
        assert blocking == [], f"injection produced invalid source: {blocking}"
        assert warnings, "a lossy render must be reported as a warning"

    @pytest.mark.parametrize("renderer", [MermaidRenderer(), PlantUmlRenderer()])
    def test_injection_cannot_create_a_node_or_a_directive(
        self, hostile_architecture: Architecture, renderer: MermaidRenderer | PlantUmlRenderer
    ) -> None:
        """The payload's words may survive inside a quoted label; never outside one.

        Sanitisation strips quotes, so ``owned`` legitimately appears in the middle
        of the label ``A -- B System owned``. The property that matters is that no
        fragment of the payload escapes into the *structure*: outside quoted
        strings there must be no trace of the injected names, no extra node and no
        extra edge.
        """
        from architecture_agent.domain.diagram import DiagramKind

        source, _ = renderer.render(hostile_architecture, DiagramKind.C4_CONTAINER)

        outside = _outside_quotes(source)
        for injected in ("owned", "evil", "System", "A -- B"):
            assert injected not in outside, f"{injected!r} escaped a quoted string"

        declared = source.count("Container(") + source.count("Component(")
        assert declared == len(hostile_architecture.components)

        edges = sum(source.count(marker) for marker in ("-->", "..>", "-.->", "Rel("))
        assert edges == len(hostile_architecture.interactions)

    @pytest.mark.parametrize("renderer", [MermaidRenderer(), PlantUmlRenderer()])
    def test_the_sanitised_label_is_reported_as_lossy(
        self, hostile_architecture: Architecture, renderer: MermaidRenderer | PlantUmlRenderer
    ) -> None:
        from architecture_agent.domain.diagram import DiagramKind

        _, warnings = renderer.render(hostile_architecture, DiagramKind.C4_CONTAINER)

        assert any("label sanitised" in warning for warning in warnings)


def _outside_quotes(source: str) -> str:
    """Strip every quoted span, leaving only structural text."""
    return re.sub(r'"[^"]*"', "", source)
