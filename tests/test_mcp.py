"""MCP adapter end-to-end tests (G6): call the tools exactly as an MCP
client would, through FastMCP's call_tool interface.

Requires the optional dependency: pip install ontology-runtime[mcp]
"""

import json
from pathlib import Path

import pytest

mcp = pytest.importorskip("mcp")

from ontology_runtime.adapters.mcp import build_server  # noqa: E402

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "vehicle"


@pytest.fixture(scope="module")
def server():
    return build_server(
        schema_path=str(EXAMPLES / "ontology.yaml"),
        bindings_path=str(EXAMPLES / "bindings.yaml"),
        seed_path=str(EXAMPLES / "seed.json"),
    )


def call(server, name, arguments):
    """Invoke one MCP tool and return its JSON payload."""
    import asyncio

    result = asyncio.run(server.call_tool(name, arguments))
    if isinstance(result, tuple):  # (content_blocks, metadata)
        blocks = result[0]
    elif isinstance(result, dict):  # newer SDKs may return a dict payload
        blocks = result.get("content", [])
    else:
        blocks = result
    text = "".join(getattr(b, "text", "") for b in blocks)
    return json.loads(text)


def test_lists_all_seven_tools(server):
    import asyncio

    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "ontology_describe",
        "ontology_describe_object",
        "ontology_describe_function",
        "ontology_get",
        "ontology_find",
        "ontology_related",
        "ontology_invoke",
    }


def test_describe_and_describe_object(server):
    data = call(server, "ontology_describe", {})
    assert data["ontology"]["name"] == "traffic"
    assert any(o["name"] == "Vehicle" for o in data["objects"])

    detail = call(server, "ontology_describe_object", {"name": "Vehicle"})
    assert detail["properties"]["balance"]["type"] == "number"
    assert "Vehicle.recharge" in detail["functions"]


def test_describe_function(server):
    data = call(server, "ontology_describe_function",
                {"name": "Vehicle.recharge"})
    assert data["target"] == "Vehicle"
    assert data["inputs"] == {"amount": "number"}


def test_get(server):
    data = call(server, "ontology_get", {"type": "Vehicle", "id": "V001"})
    assert data["properties"]["balance"] == 100


def test_find_with_filters(server):
    data = call(server, "ontology_find",
                {"type": "Vehicle", "filters": {"balance__gt": 50}})
    assert [o["id"] for o in data] == ["V001"]


def test_related(server):
    data = call(server, "ontology_related",
                {"type": "Vehicle", "id": "V001", "relation": "passes"})
    assert data == ["TollStation/ST001"]


def test_invoke_roundtrip_with_changes(server):
    data = call(server, "ontology_invoke", {
        "function": "Vehicle.recharge",
        "target_type": "Vehicle",
        "target_id": "V001",
        "args": {"amount": 100},
    })
    assert data["error"] is None
    assert data["output"]["balance"] == 200
    assert data["changes"][0]["kind"] == "set_property"
    assert (data["changes"][0]["old"], data["changes"][0]["new"]) == (100, 200)


def test_invoke_business_error_surfaces_in_payload(server):
    data = call(server, "ontology_invoke", {
        "function": "Vehicle.pass_station",
        "target_type": "Vehicle",
        "target_id": "V002",
        "args": {"station": "ST001", "fee": 999},
    })
    assert data["error"] is not None
    assert "insufficient balance" in data["error"]["message"]


def test_invoke_precheck_error_never_raises(server):
    """G2 at the MCP boundary: unknown function -> error payload, no raise."""
    data = call(server, "ontology_invoke", {
        "function": "Vehicle.fly",
        "target_type": "Vehicle",
        "target_id": "V001",
        "args": {},
    })
    assert data["error"]["type"] == "FunctionNotFound"
