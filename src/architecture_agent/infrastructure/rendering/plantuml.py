"""PlantUML renderer.

PlantUML labels are quoted with double quotes and there is no reliable escape for a
double quote inside a quoted string, so labels go through the same conservative
sanitiser as Mermaid (see :mod:`.identifiers`). Identifiers are positional, so
input cannot influence structure.
"""

from __future__ import annotations

from collections.abc import Sequence

from architecture_agent.domain.architecture import Architecture
from architecture_agent.domain.diagram import DiagramKind, ValidationSeverity
from architecture_agent.infrastructure.rendering.identifiers import (
    IdentifierMap,
    sanitize_label,
)
from architecture_agent.infrastructure.rendering.syntax import PLANTUML, find_issues

_C4_DIRECTIVE: dict[DiagramKind, str] = {
    DiagramKind.C4_CONTEXT: "!$c4 = true",
    DiagramKind.C4_CONTAINER: "!$c4 = true",
    DiagramKind.C4_COMPONENT: "!$c4 = true",
    DiagramKind.C4_CODE: "!$c4 = true",
}

_SUPPORTED_KINDS: frozenset[DiagramKind] = frozenset(
    {DiagramKind.CLASS, DiagramKind.SEQUENCE, DiagramKind.COMPONENT, DiagramKind.DEPLOYMENT}
    | set(_C4_DIRECTIVE)
)


class PlantUmlRenderer:
    """Render an architecture as PlantUML source."""

    language = "plantuml"
    supported_kinds: frozenset[DiagramKind] = _SUPPORTED_KINDS

    def supports(self, kind: DiagramKind) -> bool:
        """Whether this renderer can produce ``kind``."""
        return kind in self.supported_kinds

    def render(
        self,
        architecture: Architecture,
        kind: DiagramKind,
        *,
        plan: object | None = None,  # noqa: ARG002 - required by the DiagramRenderer port
    ) -> tuple[str, tuple[str, ...]]:
        """Render ``architecture`` and return the source plus sanitisation warnings."""
        if not self.supports(kind):
            msg = f"PlantUML cannot render {kind.value}"
            raise ValueError(msg)

        warnings: list[str] = []
        if kind in _C4_DIRECTIVE:
            body = self._render_c4(architecture, warnings)
            preamble = (
                "@startuml\n"
                "!$c4Context = true\n"
                f"title {sanitize_label(architecture.name, max_chars=120).value}\n"
            )
        elif kind is DiagramKind.CLASS:
            body = self._render_class(architecture, warnings)
            preamble = "@startuml\n"
        elif kind is DiagramKind.SEQUENCE:
            body = self._render_sequence(architecture, warnings)
            preamble = "@startuml\nautonumber\n"
        else:
            body = self._render_component(architecture, warnings)
            preamble = (
                "@startuml\nskinparam rectangle {\n  BackgroundColor<<external>> #F5F5F5\n}\n"
            )

        source = f"{preamble}{body}@enduml\n"

        issues = find_issues(source, PLANTUML)
        blocking = [issue for issue in issues if issue.severity is ValidationSeverity.ERROR]
        if blocking:
            msg = f"generated PlantUML is not valid: {blocking[0]}"
            raise ValueError(msg)

        return source, tuple(warnings)

    def _render_c4(self, architecture: Architecture, warnings: list[str]) -> str:
        lines: list[str] = []
        for index, dependency in enumerate(architecture.external_dependencies, start=1):
            label, notes = _label(dependency.name)
            warnings.extend(notes)
            lines.append(f'Person_Ext(actor{index}, "{label}", "{label}")')

        aliases = IdentifierMap.assign([component.name for component in architecture.components])
        for component in architecture.components:
            label, notes = _label(component.name)
            warnings.extend(notes)
            lines.append(
                f'Container_Boundary(alias, "{label}", "") {{\n'
                f'  Component({aliases.alias(component.name)}, "{label}", '
                f'"{_technology_summary(component.technology)}")\n'
                "}"
            )

        for interaction in architecture.interactions:
            protocol, notes = _label(interaction.protocol)
            warnings.extend(notes)
            source = aliases.alias(interaction.source)
            target = aliases.alias(interaction.target)
            lines.append(f'{source} --> {target} : "{protocol}"')
        return "\n".join(lines) + "\n" if lines else ""

    def _render_class(self, architecture: Architecture, warnings: list[str]) -> str:
        aliases = IdentifierMap.assign([component.name for component in architecture.components])
        lines: list[str] = []
        for component in architecture.components:
            alias = aliases.alias(component.name)
            label, notes = _label(component.name)
            warnings.extend(notes)
            lines.append(f'class "{label}" as {alias} {{')
            for technology in component.technology:
                safe, notes = _label(technology[:40])
                warnings.extend(notes)
                lines.append(f"  +{safe}()")
            lines.append("}")

        for interaction in architecture.interactions:
            protocol, notes = _label(interaction.protocol)
            warnings.extend(notes)
            arrow = "-->" if interaction.synchronous else "..>"
            source = aliases.alias(interaction.source)
            target = aliases.alias(interaction.target)
            lines.append(f'{source} {arrow} {target} : "{protocol}"')
        return "\n".join(lines) + "\n"

    def _render_sequence(self, architecture: Architecture, warnings: list[str]) -> str:
        aliases = IdentifierMap.assign([component.name for component in architecture.components])
        lines: list[str] = []
        for component in architecture.components:
            label, notes = _label(component.name)
            warnings.extend(notes)
            lines.append(f'participant "{label}" as {aliases.alias(component.name)}')

        for interaction in architecture.interactions:
            arrow = "->" if interaction.synchronous else "->>"
            protocol, notes = _label(interaction.protocol[:40])
            warnings.extend(notes)
            source = aliases.alias(interaction.source)
            target = aliases.alias(interaction.target)
            lines.append(f'{source} {arrow} {target} : "{protocol}"')
        return "\n".join(lines) + "\n"

    def _render_component(self, architecture: Architecture, warnings: list[str]) -> str:
        aliases = IdentifierMap.assign([component.name for component in architecture.components])
        lines: list[str] = []
        for component in architecture.components:
            label, notes = _label(f"{component.name} - {component.kind.value}")
            warnings.extend(notes)
            stereotype = "<<external>>" if component.is_external else "<<internal>>"
            alias = aliases.alias(component.name)
            lines.append(f'rectangle "{label}" as {alias} {stereotype}')

        for interaction in architecture.interactions:
            protocol, notes = _label(interaction.protocol)
            warnings.extend(notes)
            arrow = "-->" if interaction.synchronous else "..>"
            source = aliases.alias(interaction.source)
            target = aliases.alias(interaction.target)
            lines.append(f'{source} {arrow} {target} : "{protocol}"')
        return "\n".join(lines) + "\n"


def _label(text: str) -> tuple[str, list[str]]:
    """Sanitise ``text`` for diagram syntax and report whether it changed.

    Composed labels must be built from characters the sanitiser preserves, or
    every render would raise a warning about a separator the renderer chose
    itself. The helper exists so the comparison is always between a sanitiser's
    input and its output.
    """
    sanitized = sanitize_label(text)
    if not sanitized.modified:
        return sanitized.value, []
    return sanitized.value, [
        f"label sanitised for diagram syntax: {text.strip()[:80]!r} -> {sanitized.value!r}"
    ]


def _technology_summary(technology: Sequence[str]) -> str:
    """Collapse a technology list into one technology description."""
    if not technology:
        return "Technology not specified"
    return sanitize_label(", ".join(technology[:4])[:60]).value
