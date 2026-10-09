"""Diagram generation tool.

Translates an MCP call into the diagram use case and back. The tool owns no
syntax: it delegates to the use case, which delegates to a renderer.
"""

from __future__ import annotations

import re
from typing import Final

from architecture_agent.application.diagram_service import DiagramService
from architecture_agent.domain.diagram import Diagram, ValidationSeverity
from architecture_agent.tools.schemas import (
    GenerateDiagramInput,
    GenerateDiagramOutput,
)

_BACKTICK_RUN: Final = re.compile(r"`+")


def generate_diagram(
    request: GenerateDiagramInput,
    service: DiagramService,
) -> GenerateDiagramOutput:
    """Render an architecture as a syntactically validated diagram.

    Args:
        request: What to render, and in which language.
        service: The diagram use case.

    Returns:
        The validated diagram, a Markdown rendering and whether the render was
        lossy.

    Raises:
        UnsupportedRequestError: if the language cannot produce the requested kind,
            or a required input was not supplied.
        RenderingError: if the renderer could not produce valid source.
    """
    diagram = service.generate(
        request.architecture,
        request.diagram_kind,
        request.language,
        plan=request.infrastructure,
        title=request.title,
        description=request.description,
    )
    return _to_output(diagram)


def _to_output(diagram: Diagram) -> GenerateDiagramOutput:
    """Shape a diagram for the MCP boundary."""
    lossy = any(issue.severity is ValidationSeverity.WARNING for issue in diagram.issues)
    return GenerateDiagramOutput(
        diagram=diagram,
        markdown=to_markdown(diagram),
        lossy=lossy,
    )


def to_markdown(diagram: Diagram) -> str:
    """Wrap diagram source in a fenced Markdown block.

    The fence is one backtick longer than the longest backtick *run* inside the
    source, so a diagram containing a fence cannot break out of its own block. Runs
    are found with a pattern rather than ``split``, which would return whole lines
    and produce a fence thousands of characters long.
    """
    longest = max((len(run) for run in _BACKTICK_RUN.findall(diagram.source)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{diagram.code_fence_language}\n{diagram.source}\n{fence}"
