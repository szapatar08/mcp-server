"""Architecture Engineering Agent.

An MCP server that provides software architecture, infrastructure, cloud,
deployment, security, cost and diagramming capabilities.
"""

from __future__ import annotations

from architecture_agent.config.settings import Settings, get_settings

__all__ = ["Settings", "__version__", "get_settings"]

try:
    from importlib.metadata import PackageNotFoundError, version

    __version__ = version("architecture-agent")
except PackageNotFoundError:  # pragma: no cover - only hit outside an install
    __version__ = "0.0.0+unknown"
