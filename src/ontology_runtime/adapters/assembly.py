"""Shared runtime assembly used by every adapter.

    schema yaml + bindings yaml + store + executor -> OntologyRuntime
"""

from __future__ import annotations

from ..infrastructure.memory import MemoryStateStore
from ..infrastructure.python_executor import PythonFunctionExecutor
from ..infrastructure.sqlite import SQLiteStateStore
from ..loader import load_bindings_file, load_schema, load_seed, parse_seed
from ..runtime.runtime import OntologyRuntime


def build_runtime(
    schema_path: str,
    bindings_path: str | None = None,
    store: str = "memory",
    db_path: str | None = None,
    seed_path: str | None = None,
) -> OntologyRuntime:
    schema, catalog = load_schema(schema_path)

    if store == "sqlite":
        if not db_path:
            raise ValueError("--db is required when --store sqlite")
        state_store = SQLiteStateStore(db_path)
    elif store == "memory":
        state_store = MemoryStateStore()
    else:
        raise ValueError(f"unknown store: {store!r} (expected memory|sqlite)")

    if seed_path:
        objects, relations = load_seed(seed_path)
        state_store.seed(objects, relations)

    bindings = load_bindings_file(bindings_path) if bindings_path else {}
    executor = PythonFunctionExecutor(bindings)
    return OntologyRuntime(
        schema=schema, catalog=catalog, store=state_store, executor=executor
    )


def build_runtime_from_dicts(
    schema_data: dict,
    bindings_data: dict | None = None,
    seed_data: dict | None = None,
    store: str = "memory",
    db_path: str | None = None,
) -> OntologyRuntime:
    """Assemble a runtime directly from parsed dicts (no file I/O)."""
    from ..core.schema import CapabilityCatalog  # noqa: F401  (typing aid)

    schema, catalog = _parse(schema_data)
    if store == "sqlite":
        if not db_path:
            raise ValueError("db_path is required when store == 'sqlite'")
        state_store = SQLiteStateStore(db_path)
    else:
        state_store = MemoryStateStore()
    if seed_data:
        objects, relations = parse_seed(seed_data)
        state_store.seed(objects, relations)
    bindings = load_bindings_file_data(bindings_data or {})
    executor = PythonFunctionExecutor(bindings)
    return OntologyRuntime(
        schema=schema, catalog=catalog, store=state_store, executor=executor
    )


def _parse(schema_data: dict):
    from ..loader import parse_schema

    return parse_schema(schema_data)


def load_bindings_file_data(data: dict):
    from ..infrastructure.bindings import load_bindings

    return load_bindings(data)
