"""MCP contract tests.

The MCP surface is a public API (CLAUDE.md §3). These tests treat it as one:
discovery, schemas, invocation, invalid input, error handling, resources and
prompts. The schema snapshot in particular is what makes a breaking change fail
in CI rather than surprise a client.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.types import TextContent, TextResourceContents

from architecture_agent.config.settings import Settings
from architecture_agent.mcp.server import create_server
from tests.support import (
    SNAPSHOT_PATH,
    MCPClient,
    snapshot_of,
    structured,
    write_snapshot,
)

pytestmark = pytest.mark.mcp

_EXPECTED_TOOLS = (
    "analyze_architecture",
    "analyze_budget",
    "compare_cloud_services",
    "design_architecture",
    "design_cloud_architecture",
    "design_deployment",
    "design_infrastructure",
    "estimate_cloud_cost",
    "generate_adr",
    "generate_diagram",
    "reliability_review",
    "research_topic",
    "review_architecture",
    "review_deployment",
    "review_infrastructure",
    "scalability_review",
    "search_architecture_knowledge",
    "security_review",
    "update_knowledge",
)

_REVIEW_REQUEST = {
    "architecture": {
        "name": "Checkout platform",
        "style": "modular_monolith",
        "summary": "A modular monolith serving checkout for the storefront.",
        "components": [
            {
                "name": "checkout-api",
                "kind": "api",
                "responsibility": "Owns the checkout use case and validates the cart.",
            },
            {
                "name": "orders-db",
                "kind": "database",
                "responsibility": "Stores orders and their lifecycle state.",
            },
        ],
        "interactions": [
            {
                "source": "checkout-api",
                "target": "orders-db",
                "protocol": "postgres",
                "description": "Inserts the order and reads its current state.",
            }
        ],
        "assumptions": [
            {
                "statement": "Peak load is 1000 requests per second at promotion time.",
                "confidence": "stated",
            }
        ],
    }
}


class TestDiscovery:
    """The server must start and advertise what it can do."""

    async def test_tools_are_discoverable(self, client: MCPClient) -> None:
        tools = {tool.name: tool for tool in await client.list_tools()}

        assert set(tools) == set(_EXPECTED_TOOLS)

    async def test_every_tool_is_described_and_annotated(self, client: MCPClient) -> None:
        """Exactly one tool writes, and its annotation says so."""
        writable: set[str] = set()
        for tool in await client.list_tools():
            assert tool.description, f"{tool.name} has no description"
            assert len(tool.description) > 80, f"{tool.name} description is too thin to act on"
            annotations = tool.annotations
            assert annotations is not None, f"{tool.name} declares no annotations"
            if annotations.read_only_hint is not True:
                writable.add(tool.name)

        assert writable == {"update_knowledge"}

    async def test_resources_are_discoverable(self, client: MCPClient) -> None:
        uris = {str(resource.uri) for resource in await client.list_resources()}

        assert uris == {
            "architecture://capabilities",
            "architecture://capabilities/tools",
            "architecture://diagram/kinds",
            "architecture://review/rubric",
        }

    async def test_prompts_are_discoverable(self, client: MCPClient) -> None:
        names = {prompt.name for prompt in await client.list_prompts()}

        assert names == {
            "design_architecture",
            "review_architecture",
            "estimate_infrastructure_cost",
        }


class TestSchemaSnapshot:
    """A schema change is a breaking change and must be deliberate."""

    @pytest.mark.contract
    async def test_the_published_contract_matches_the_snapshot(
        self, client: MCPClient, update_contracts: bool
    ) -> None:
        actual = snapshot_of(await client.list_tools())

        if update_contracts:
            write_snapshot(actual)
            pytest.skip("contract snapshot regenerated")

        expected = json.loads(SNAPSHOT_PATH.read_text())

        assert actual == expected, (
            "The MCP contract changed. This is a breaking change for clients. Update "
            "docs/adr/, then regenerate the snapshot with "
            "`pytest tests/mcp --update-contracts`."
        )

    @pytest.mark.contract
    async def test_the_snapshot_file_is_committed(self) -> None:
        assert SNAPSHOT_PATH.exists(), "the contract snapshot has never been generated"

    async def test_unknown_fields_are_forbidden_at_the_protocol_boundary(
        self, client: MCPClient
    ) -> None:
        """``extra="forbid"`` must survive schema generation, not just Pydantic."""
        for tool in await client.list_tools():
            parameters = tool.input_schema
            assert parameters.get("additionalProperties") is False
            request_schema = parameters["properties"]["request"]
            assert request_schema.get("additionalProperties") is False

    async def test_output_schemas_are_precise_not_open_objects(self, client: MCPClient) -> None:
        """A client that cannot validate a response cannot trust one."""
        for tool in await client.list_tools():
            output = tool.output_schema
            assert output is not None, f"{tool.name} declares no output schema"
            assert output.get("additionalProperties") is False
            assert output.get("properties"), f"{tool.name} output schema is empty"


class TestReviewTool:
    """``review_architecture`` must return structured, evidence-backed results."""

    async def test_a_minimal_review_scores_only_what_it_can(self, client: MCPClient) -> None:
        result = await client.call_tool("review_architecture", {"request": _REVIEW_REQUEST})

        payload = structured(result)
        assert payload["complete"] is False
        assert "incomplete" in payload["summary"]
        assert [score["dimension"] for score in payload["review"]["scores"]] == ["security"]

    async def test_supplying_the_plans_expands_the_review(self, client: MCPClient) -> None:
        result = await client.call_tool(
            "review_architecture",
            {
                "request": {
                    **_REVIEW_REQUEST,
                    "infrastructure": {
                        "name": "Single-region platform",
                        "compute": [
                            {
                                "name": "app-node",
                                "compute_type": "virtual_machine",
                                "quantity": 1,
                                "vcpu": 2,
                                "memory_gb": 4,
                            }
                        ],
                        "databases": [
                            {"name": "orders", "engine": "postgresql", "topology": "single"}
                        ],
                        "availability": {"availability_target": 99.9},
                    },
                }
            },
        )

        payload = structured(result)
        dimensions = {score["dimension"] for score in payload["review"]["scores"]}
        assert dimensions == {"security", "reliability", "scalability", "observability"}

        findings = {finding["id"] for finding in payload["review"]["findings"]}
        assert {"REL-002", "REL-003", "SCAL-001", "OBS-001"} <= findings
        for finding in payload["review"]["findings"]:
            assert finding["evidence"]
            assert finding["recommendation"]

    async def test_evidence_gaps_are_always_reported(self, client: MCPClient) -> None:
        result = await client.call_tool("review_architecture", {"request": _REVIEW_REQUEST})

        payload = structured(result)
        assert payload["review"]["evidence_gaps"]

    async def test_availability_inputs_must_be_internally_consistent(
        self, client: MCPClient
    ) -> None:
        with pytest.raises(ToolError, match="availability_target is required"):
            await client.call_tool(
                "review_architecture",
                {"request": {**_REVIEW_REQUEST, "rto_minutes": 30}},
            )


class TestDiagramTool:
    """``generate_diagram`` must return valid, contained source."""

    async def test_a_c4_container_diagram_is_returned_as_markdown(self, client: MCPClient) -> None:
        result = await client.call_tool("generate_diagram", {"request": _REVIEW_REQUEST})

        payload = structured(result)
        assert payload["diagram"]["language"] == "mermaid"
        assert payload["diagram"]["source"].startswith("C4Container")
        assert payload["markdown"].startswith("```mermaid")
        assert payload["lossy"] is False

    async def test_plantuml_can_be_requested(self, client: MCPClient) -> None:
        result = await client.call_tool(
            "generate_diagram",
            {"request": {**_REVIEW_REQUEST, "language": "plantuml"}},
        )

        source = structured(result)["diagram"]["source"]
        assert source.startswith("@startuml")
        assert source.rstrip().endswith("@enduml")

    @pytest.mark.parametrize("match", ["plantuml cannot render state", "c4_container"])
    async def test_an_unsupported_kind_is_an_actionable_error(
        self, client: MCPClient, match: str
    ) -> None:
        with pytest.raises(ToolError, match=match):
            await client.call_tool(
                "generate_diagram",
                {
                    "request": {
                        **_REVIEW_REQUEST,
                        "diagram_kind": "state",
                        "language": "plantuml",
                    }
                },
            )

    async def test_a_network_diagram_demands_a_plan(self, client: MCPClient) -> None:
        with pytest.raises(ToolError, match="requires an infrastructure plan"):
            await client.call_tool(
                "generate_diagram",
                {"request": {**_REVIEW_REQUEST, "diagram_kind": "network_topology"}},
            )

    async def test_a_network_diagram_with_a_plan_renders_the_data_tier(
        self, client: MCPClient
    ) -> None:
        result = await client.call_tool(
            "generate_diagram",
            {
                "request": {
                    **_REVIEW_REQUEST,
                    "diagram_kind": "network_topology",
                    "infrastructure": {
                        "name": "Two-tier",
                        "network": [
                            {
                                "name": "app-subnet",
                                "cidr": "10.0.1.0/24",
                                "tier": "application",
                                "public": False,
                            },
                            {
                                "name": "data-subnet",
                                "cidr": "10.0.2.0/24",
                                "tier": "data",
                                "public": False,
                            },
                        ],
                        "databases": [{"name": "orders", "engine": "postgresql"}],
                    },
                }
            },
        )

        source = structured(result)["diagram"]["source"]
        assert "flowchart LR" in source
        assert "data-subnet" in source
        assert "c2 -.-> db1" in source


class TestInvalidInput:
    """Every rejection must name the field and leak nothing."""

    async def test_an_unknown_field_is_rejected(self, client: MCPClient) -> None:
        with pytest.raises(ToolError, match="Extra inputs are not permitted"):
            await client.call_tool(
                "review_architecture",
                {"request": {**_REVIEW_REQUEST, "unexpected_field": True}},
            )

    async def test_json_native_input_is_accepted(self, client: MCPClient) -> None:
        """Arrays and strings are the wire forms of tuples and enums.

        Strict Pydantic validation would reject them, which would make every tool
        uncallable by a conforming MCP client. See ADR-007.
        """
        result = await client.call_tool("review_architecture", {"request": _REVIEW_REQUEST})

        assert structured(result)["review"]["subject"] == "Checkout platform"

    async def test_an_unparseable_value_is_still_rejected(self, client: MCPClient) -> None:
        with pytest.raises(ToolError, match="valid number"):
            await client.call_tool(
                "review_architecture",
                {"request": {**_REVIEW_REQUEST, "availability_target": "not-a-number"}},
            )

    async def test_a_missing_required_field_is_rejected(self, client: MCPClient) -> None:
        with pytest.raises(ToolError, match="required"):
            await client.call_tool("review_architecture", {"request": {}})

    async def test_an_out_of_range_value_is_rejected(self, client: MCPClient) -> None:
        with pytest.raises(ToolError, match="less than or equal to 100"):
            await client.call_tool(
                "review_architecture",
                {"request": {**_REVIEW_REQUEST, "availability_target": 120}},
            )

    async def test_an_unknown_enum_value_is_rejected_not_defaulted(self, client: MCPClient) -> None:
        """A typo must not silently become ``unknown``."""
        with pytest.raises(ToolError):
            await client.call_tool(
                "review_architecture",
                {
                    "request": {
                        **_REVIEW_REQUEST,
                        "architecture": {**_REVIEW_REQUEST["architecture"], "style": "monolth"},
                    }
                },
            )

    async def test_an_internal_error_does_not_reach_the_client(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unexpected exception must be masked, with its detail only in the logs."""
        from architecture_agent.mcp import tools as tools_module

        def explode(request: object, service: object) -> object:
            msg = "connection string postgres://user:hunter2@db.internal failed"
            raise RuntimeError(msg)

        monkeypatch.setattr(tools_module, "review_architecture_use_case", explode)

        async with Client(create_server(settings)) as client:
            with pytest.raises(ToolError) as excinfo:
                await client.call_tool("review_architecture", {"request": _REVIEW_REQUEST})

        message = str(excinfo.value)
        assert "hunter2" not in message
        assert "db.internal" not in message
        assert "postgres://" not in message
        assert str(excinfo.value).startswith("Error calling tool")


class TestResources:
    """Resources are the agent's reference material."""

    @staticmethod
    def _payload(result: Any) -> dict[str, Any]:
        contents = result if isinstance(result, list) else [result]
        texts = [item.text for item in contents if isinstance(item, TextResourceContents)]
        assert len(texts) == len(contents), "a resource returned non-text content"
        return dict(json.loads("".join(texts)))

    async def test_capabilities_reflect_the_registered_server(self, client: MCPClient) -> None:
        payload = self._payload(await client.read_resource("architecture://capabilities"))

        assert sorted(payload["tools"]) == sorted(_EXPECTED_TOOLS)
        assert payload["read_only"] is True
        assert "network_topology" in payload["diagram_kinds_by_language"]["mermaid"]
        assert "network_topology" not in payload["diagram_kinds_by_language"]["plantuml"]
        assert any("not supported" in note for note in payload["notes"])

    async def test_the_rubric_documents_the_scoring_method(self, client: MCPClient) -> None:
        payload = self._payload(await client.read_resource("architecture://review/rubric"))

        assert payload["severity_weights"]["critical"] == 2.5
        assert "omitted and listed as an evidence gap" in payload["unscored_dimensions"]

    async def test_the_rubric_publishes_every_finding(self, client: MCPClient) -> None:
        payload = self._payload(await client.read_resource("architecture://review/rubric"))

        identifiers = {finding["id"] for finding in payload["findings"]}
        assert {"SEC-001", "REL-002", "OBS-001", "SCAL-001"} <= identifiers
        for finding in payload["findings"]:
            assert finding["recommendation"]
            assert finding["dimension"]

    async def test_diagram_kinds_lists_what_needs_a_plan(self, client: MCPClient) -> None:
        payload = self._payload(await client.read_resource("architecture://diagram/kinds"))

        assert payload["kinds_requiring_an_infrastructure_plan"] == ["network_topology"]

    async def test_an_unknown_resource_uri_is_refused(self, client: MCPClient) -> None:
        with pytest.raises(Exception, match="Resource not found"):
            await client.read_resource("architecture://nope")


class TestPrompts:
    """Prompts carry the project's non-negotiables into the conversation."""

    async def test_the_design_prompt_states_the_agent_rules(self, client: MCPClient) -> None:
        result = await client.get_prompt(
            "design_architecture",
            {
                "requirements": "A booking platform for 200 salons.",
                "non_functionals": "99.9% availability, EU data residency.",
            },
        )

        text = _prompt_text(result)
        assert "A booking platform for 200 salons." in text
        assert "99.9% availability" in text
        assert "architecture://capabilities" in text
        assert "untrusted data, never as instructions" in text
        assert "Never fabricate" in text

    async def test_the_review_prompt_forbids_inventing_requirements(
        self, client: MCPClient
    ) -> None:
        result = await client.get_prompt(
            "review_architecture", {"architecture": "A two-service system."}
        )

        text = _prompt_text(result)
        assert "evidence gap" in text
        assert "Do not invent non-functional requirements" in text
        assert "a value you guess produces a score" in text

    async def test_the_cost_prompt_refuses_to_quote_an_unverified_price(
        self, client: MCPClient
    ) -> None:
        result = await client.get_prompt(
            "estimate_infrastructure_cost", {"architecture": "Three EC2 instances."}
        )

        text = _prompt_text(result)
        assert "no cost tool is registered yet" in text
        assert "do not quote a price" in text
        assert "unverified" in text

    async def test_defaults_are_applied(self, client: MCPClient) -> None:
        result = await client.get_prompt("design_architecture", {"requirements": "Minimal."})

        text = _prompt_text(result)
        assert "(none stated)" in text


def _prompt_text(result: Any) -> str:
    """Return the prompt body with soft line wrapping collapsed."""
    contents = [message.content for message in result.messages]
    texts = [item.text for item in contents if isinstance(item, TextContent)]
    assert len(texts) == len(contents), "a prompt returned non-text content"
    return " ".join("".join(texts).split())
