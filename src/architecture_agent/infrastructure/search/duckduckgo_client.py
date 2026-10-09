"""DuckDuckGo search adapter.

Uses ``duckduckgo_search`` and nothing else: no scraping of DuckDuckGo HTML, no
hidden endpoints. The provider is an external dependency whose behaviour can
change, which is why the search port exists and why a failure is a ``degraded``
result rather than an exception.
"""

from __future__ import annotations

from typing import Any

from architecture_agent.application.research_service import SearchHit
from architecture_agent.config.settings import SearchProvider, SearchSettings


class DuckDuckGoSearchClient:
    """Search DuckDuckGo for candidate URLs."""

    provider = "duckduckgo"

    def __init__(self, settings: SearchSettings) -> None:
        """Create the client.

        Args:
            settings: Result count, timeout and region.
        """
        self._settings = settings

    def search(self, query: str, *, max_results: int) -> tuple[SearchHit, ...]:
        """Return up to ``max_results`` hits.

        Raises:
            RuntimeError: propagated from the provider. The research use case
                converts this into a degraded result, so the failure is reported
                rather than swallowed here.
        """
        from duckduckgo_search import DDGS

        limit = min(max_results, self._settings.max_results)
        with DDGS(timeout=int(self._settings.timeout_seconds)) as client:
            raw: list[dict[str, Any]] = list(
                client.text(query, region=self._settings.region, max_results=limit)
            )

        return tuple(
            SearchHit(
                url=str(item.get("href") or item.get("url") or ""),
                title=str(item.get("title") or ""),
                snippet=str(item.get("body") or ""),
                rank=position,
            )
            for position, item in enumerate(raw, start=1)
            if item.get("href") or item.get("url")
        )


def build_search_client(settings: SearchSettings) -> Any:
    """Return a search client for ``settings``."""
    if settings.provider is SearchProvider.FAKE:
        from architecture_agent.infrastructure.search.fake_client import FakeSearchClient

        return FakeSearchClient()
    return DuckDuckGoSearchClient(settings)


__all__ = ["DuckDuckGoSearchClient", "build_search_client"]
