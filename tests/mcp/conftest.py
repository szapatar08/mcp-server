"""Fixtures for the MCP contract suite.

The schema snapshot is a committed file. It is regenerated explicitly, never
implicitly, so an accidental contract change cannot rewrite the expectation that
was supposed to catch it.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the contract snapshot update flag."""
    parser.addoption(
        "--update-contracts",
        action="store_true",
        default=False,
        help="Regenerate the committed MCP contract snapshots instead of comparing.",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register the contract marker."""
    config.addinivalue_line("markers", "contract: MCP public contract snapshot")


@pytest.fixture
def update_contracts(request: pytest.FixtureRequest) -> bool:
    """Whether the run was asked to regenerate snapshots."""
    return bool(request.config.getoption("--update-contracts"))
