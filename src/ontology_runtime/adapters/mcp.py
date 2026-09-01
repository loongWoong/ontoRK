"""MCP adapter: the runtime exposed as MCP tools.

    ontology_describe / ontology_get / ontology_find
    ontology_related / ontology_invoke

MCP only translates protocol; it never touches the store or scripts.
Requires the optional dependency: pip install ontology-runtime[mcp]
"""

from __future__ import annotations

import json

from ..core.refs import ObjectRef
from .assembly import build_runtime
from .serialize import to_jsonable


def _dumps(value) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False, default=str)


def build_server(schema_path: str, bindings_path: str | None = None,
                 store: str = "memory", db_path: str | None = None,
                 seed_path: str | None = None):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "The MCP adapter requires the 'mcp' package. "
            "Install with: pip install ontology-runtime[mcp]"
        ) from exc

    runtime = build_runtime(
        schema_path=schema_path,
        bindings_path=bindings_path,
        store=store,
        db_path=db_path,
        seed_path=seed_path,
    )
    mcp = FastMCP("ontology-runtime")

    @mcp.tool()
    def ontology_describe() -> str:
        """Index-level ontology: object types, relations, functions."""
        return _dumps(runtime.describe())

    @mcp.tool()
    def ontology_describe_object(name: str) -> str:
        """Full schema of one object type: properties, relations, functions."""
        return _dumps(runtime.describe_object(name))

    @mcp.tool()
    def ontology_describe_function(name: str) -> str:
        """Full signature of one function: target and typed inputs."""
        return _dumps(runtime.describe_function(name))

    @mcp.tool()
    def ontology_get(type: str, id: str) -> str:
        """Get one object by (type, id)."""
        return _dumps(runtime.get(ObjectRef(type, id)))

    @mcp.tool()
    def ontology_find(type: str, filters: dict | None = None) -> str:
        """Find objects of a type, e.g. filters={"balance__gt": 100}."""
        return _dumps(runtime.find(type, **(filters or {})))

    @mcp.tool()
    def ontology_related(type: str, id: str, relation: str, direction: str = "out") -> str:
        """Navigate relations from an object ('out' or 'in')."""
        return _dumps(runtime.related(ObjectRef(type, id), relation, direction))

    @mcp.tool()
    def ontology_invoke(function: str, target_type: str | None = None,
                        target_id: str | None = None, args: dict | None = None) -> str:
        """Execute a business capability; returns output + state changes."""
        target = None
        if target_type and target_id:
            target = ObjectRef(target_type, target_id)
        result = runtime.invoke(function, target=target, args=args or {})
        return _dumps(
            {
                "execution_id": result.execution_id,
                "output": result.output,
                "changes": result.changes,
                "error": result.error,
            }
        )

    return mcp


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="ontology-mcp")
    parser.add_argument("--schema", required=True)
    parser.add_argument("--bindings")
    parser.add_argument("--store", default="memory", choices=["memory", "sqlite"])
    parser.add_argument("--db")
    parser.add_argument("--seed")
    args = parser.parse_args()
    server = build_server(
        schema_path=args.schema,
        bindings_path=args.bindings,
        store=args.store,
        db_path=args.db,
        seed_path=args.seed,
    )
    server.run()


if __name__ == "__main__":  # pragma: no cover
    main()
