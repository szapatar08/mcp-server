"""Shared helpers for tests that talk to the MCP server.

Lives outside ``conftest.py`` so both ``tests/mcp`` and ``tests/security`` can
import it: a conftest fixture only applies to its own directory and below.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from fastmcp import Client

type MCPClient = Client[Any]
"""The in-memory client type, aliased so test signatures stay readable."""

SNAPSHOT_DIR = Path(__file__).parent / "mcp" / "snapshots"
SNAPSHOT_PATH = SNAPSHOT_DIR / "tool_schemas.json"


def structured(result: Any) -> dict[str, Any]:
    """Return a tool result's structured content, asserting it is present.

    A tool returning a non-object, or a client not asking for structured output,
    would otherwise surface as a confusing subscript error inside an assertion
    far from the call that caused it.
    """
    payload = result.structured_content
    assert isinstance(payload, dict), f"expected structured content, got {type(payload).__name__}"
    return payload


def snapshot_of(tools: Any) -> dict[str, Any]:
    """Build the comparable snapshot payload for a list of tools."""
    return {
        tool.name: {
            "description": tool.description,
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
        }
        for tool in sorted(tools, key=lambda item: item.name)
    }


def write_snapshot(payload: dict[str, Any]) -> None:
    """Persist a snapshot with stable formatting so diffs stay readable."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
