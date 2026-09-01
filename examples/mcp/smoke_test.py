"""MCP smoke test: build the MCP server and list its tools offline.

Run:  python examples/mcp/smoke_test.py     (requires pip install mcp)
"""

import asyncio
import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EXAMPLE_DIR.parents[1] / "src"))

from ontology_runtime.adapters.mcp import build_server  # noqa: E402

VEHICLE_DIR = EXAMPLE_DIR.parent / "vehicle"


async def main() -> None:
    server = build_server(
        schema_path=str(VEHICLE_DIR / "ontology.yaml"),
        bindings_path=str(VEHICLE_DIR / "bindings.yaml"),
        seed_path=str(VEHICLE_DIR / "seed.json"),
    )
    tools = await server.list_tools()
    print("MCP tools exposed by the ontology runtime:")
    for tool in tools:
        print(f"  - {tool.name}: {(tool.description or '').splitlines()[0]}")


if __name__ == "__main__":
    asyncio.run(main())
