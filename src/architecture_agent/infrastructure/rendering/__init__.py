"""Diagram rendering.

Rendering is presentation, not domain logic, so it lives in infrastructure. The
use case in :mod:`architecture_agent.application.diagram_service` depends only on
the ``DiagramRenderer`` protocol it declares itself.

Two adapters exist today (Mermaid, PlantUML) and a remote renderer is planned, so
the port is justified by current plurality rather than by speculation.
"""

from architecture_agent.infrastructure.rendering.identifiers import (
    IdentifierMap,
    SanitizedLabel,
    is_valid_identifier,
    sanitize_label,
)
from architecture_agent.infrastructure.rendering.mermaid import MermaidRenderer
from architecture_agent.infrastructure.rendering.plantuml import PlantUmlRenderer
from architecture_agent.infrastructure.rendering.syntax import (
    MERMAID,
    PLANTUML,
    SyntaxProfile,
    find_issues,
)

__all__ = [
    "MERMAID",
    "PLANTUML",
    "IdentifierMap",
    "MermaidRenderer",
    "PlantUmlRenderer",
    "SanitizedLabel",
    "SyntaxProfile",
    "find_issues",
    "is_valid_identifier",
    "sanitize_label",
]
