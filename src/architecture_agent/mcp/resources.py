"""MCP resources.

Resources expose the reference material an agent needs *before* it asks a
question: what this server can do, how it scores, and which diagram kinds exist
in which language. That material is stable, so it belongs in resources rather
than in a tool that recomputes it.

Every resource is read-only and returns JSON.
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from architecture_agent.application.diagram_service import DiagramService
from architecture_agent.application.review_service import finding_text
from architecture_agent.domain.diagram import DiagramKind, DiagramLanguage
from architecture_agent.domain.security import ComplianceFramework, ReviewDimension, Severity
from architecture_agent.mcp.tools import ToolServices

_REGISTERED_TOOLS: list[tuple[str, ...]] = [()]

CAPABILITIES_URI = "architecture://capabilities"
REVIEW_RUBRIC_URI = "architecture://review/rubric"
DIAGRAM_KINDS_URI = "architecture://diagram/kinds"


def register_resources(server: FastMCP, services: ToolServices) -> None:
    """Register every resource on ``server``.

    Args:
        server: The FastMCP server to register on.
        services: Supplies the live renderer capabilities so the catalogue cannot
            drift from what the server can actually do.
    """

    @server.resource(
        f"{CAPABILITIES_URI}/tools",
        name="tool_catalogue",
        title="Tool catalogue",
        description="Every registered tool with its description and the material it needs.",
        mime_type="application/json",
    )
    def tool_catalogue() -> str:
        """Describe the registered tools.

        Built from the live server rather than a hand-maintained list, so it cannot
        claim a tool that does not exist or omit one that does.
        """
        import anyio

        async def _describe() -> str:
            from architecture_agent.application.guard import InjectionKind

            tools = await server.list_tools()
            return _json(
                {
                    "tools": [
                        {
                            "name": tool.name,
                            "title": tool.title,
                            "description": tool.description,
                            "read_only": True,
                            "required_input": sorted(
                                tool.parameters.get("properties", {})
                                .get("request", {})
                                .get("required")
                                or []
                            ),
                        }
                        for tool in sorted(tools, key=lambda item: item.name)
                    ],
                    "injection_kinds_reported": [kind.value for kind in InjectionKind],
                }
            )

        return anyio.run(_describe)

    @server.resource(
        CAPABILITIES_URI,
        name="capabilities",
        title="Server capabilities",
        description="Tools, diagram languages and review dimensions currently available.",
        mime_type="application/json",
    )
    def capabilities() -> str:
        return _json(
            {
                "tools": sorted(_tool_names()),
                "diagram_languages": [language.value for language in DiagramLanguage],
                "diagram_kinds_by_language": _kinds_by_language(services.diagrams),
                "review_dimensions": [dimension.value for dimension in ReviewDimension],
                "read_only": True,
                "notes": [
                    "Activity and state diagrams are not supported: they cannot be "
                    "derived from an architecture without inventing steps or states.",
                    "Every tool is read-only. Nothing here executes a command or "
                    "modifies infrastructure.",
                    "A dimension or claim that the supplied material does not support "
                    "is reported as an evidence gap or an open question rather than "
                    "scored or asserted.",
                ],
            }
        )

    @server.resource(
        REVIEW_RUBRIC_URI,
        name="review_rubric",
        title="Review scoring rubric",
        description=(
            "How each review dimension is scored, what severity weights apply, and "
            "the published wording of every finding the reviewer can raise."
        ),
        mime_type="application/json",
    )
    def review_rubric() -> str:
        return _json(
            {
                "method": (
                    "A dimension score is 10 minus the sum of severity weights for its "
                    "findings, floored at 0. A dimension with no findings scores 10 and "
                    "says so; it is not scored badly."
                ),
                "severity_weights": {
                    severity.value: weight for severity, weight in _SEVERITY_WEIGHTS.items()
                },
                "unscored_dimensions": (
                    "A dimension whose inputs were not supplied is omitted and listed as "
                    "an evidence gap. Absence of evidence is never presented as a low score."
                ),
                "dimensions": [dimension.value for dimension in ReviewDimension],
                "severities": [severity.value for severity in reversed(list(Severity))],
                "compliance_frameworks": [framework.value for framework in ComplianceFramework],
                "findings": _published_findings(),
            }
        )

    @server.resource(
        DIAGRAM_KINDS_URI,
        name="diagram_kinds",
        title="Diagram kinds by language",
        description="Which diagram kinds each renderer can produce.",
        mime_type="application/json",
    )
    def diagram_kinds() -> str:
        return _json(
            {
                "kinds": _kinds_by_language(services.diagrams),
                "kinds_requiring_an_infrastructure_plan": [DiagramKind.NETWORK_TOPOLOGY.value],
            }
        )


_SEVERITY_WEIGHTS: dict[Severity, float] = {
    Severity.CRITICAL: 2.5,
    Severity.HIGH: 1.5,
    Severity.MEDIUM: 0.75,
    Severity.LOW: 0.25,
    Severity.INFO: 0.0,
}

_IDENTIFIERS: tuple[str, ...] = (
    "SEC-001",
    "SEC-002",
    "SEC-003",
    "SEC-004",
    "SEC-005",
    "REL-001",
    "REL-002",
    "REL-003",
    "REL-005",
    "REL-006",
    "SCAL-001",
    "OBS-001",
)


def record_tool_names(names: tuple[str, ...]) -> None:
    """Record the registered tool names for the capabilities resource."""
    _REGISTERED_TOOLS[0] = names


def _tool_names() -> tuple[str, ...]:
    """Return the registered tool names."""
    return _REGISTERED_TOOLS[0] or ("<not registered>",)


def _published_findings() -> list[dict[str, Any]]:
    """Describe every finding the reviewer can raise, from the catalogue."""
    return [
        {
            "id": identifier,
            "title": text.title,
            "dimension": text.dimension.value,
            "category": text.category,
            "description": text.description,
            "recommendation": text.recommendation,
            "remediation_effort": text.effort,
        }
        for identifier, text in ((item, finding_text(item)) for item in _IDENTIFIERS)
    ]


def _kinds_by_language(service: DiagramService) -> dict[str, list[str]]:
    """Map each language to the diagram kinds its renderer supports."""
    return {
        language.value: [kind.value for kind in service.supported_kinds(language)]
        for language in DiagramLanguage
    }


def _json(payload: dict[str, Any]) -> str:
    """Serialize a resource payload."""
    return json.dumps(payload, indent=2, sort_keys=True)
