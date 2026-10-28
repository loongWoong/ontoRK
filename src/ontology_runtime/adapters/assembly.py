"""Shared runtime assembly used by every adapter.

    schema yaml + bindings yaml + store + executor -> OntologyRuntime

Seeding policy (v0.2): seed data is applied only to an empty store.  A
persistent store that already holds state is never overwritten by
``--seed``, so restarting a process is reproducible and non-destructive;
``seed`` itself is also insert-only for individual objects.
"""

from __future__ import annotations

from ..infrastructure.bindings import load_bindings
from ..infrastructure.memory import MemoryStateStore
from ..infrastructure.python_executor import PythonFunctionExecutor
from ..infrastructure.sqlite import SQLiteStateStore
from ..loader import load_bindings_file, load_schema, load_seed, parse_schema, parse_seed
from ..runtime.runtime import OntologyRuntime


def _build_store(store: str, db_path: str | None):
    if store == "sqlite":
        if not db_path:
            raise ValueError("--db is required when --store sqlite")
        return SQLiteStateStore(db_path)
    if store == "memory":
        return MemoryStateStore()
    raise ValueError(f"unknown store: {store!r} (expected memory|sqlite)")


def build_runtime(
    schema_path: str,
    bindings_path: str | None = None,
    store: str = "memory",
    db_path: str | None = None,
    seed_path: str | None = None,
) -> OntologyRuntime:
    schema, catalog = load_schema(schema_path)
    state_store = _build_store(store, db_path)

    bindings = load_bindings_file(bindings_path) if bindings_path else {}
    executor = PythonFunctionExecutor(bindings)
    runtime = OntologyRuntime(
        schema=schema, catalog=catalog, store=state_store, executor=executor
    )
    if seed_path and runtime.count_objects() == 0:
        objects, relations = load_seed(seed_path)
        runtime.seed(objects, relations)
    return runtime


def build_runtime_from_dicts(
    schema_data: dict,
    bindings_data: dict | None = None,
    seed_data: dict | None = None,
    store: str = "memory",
    db_path: str | None = None,
) -> OntologyRuntime:
    """Assemble a runtime directly from parsed dicts (no file I/O)."""
    schema, catalog = parse_schema(schema_data)
    state_store = _build_store(store, db_path)
    bindings = load_bindings(bindings_data or {})
    executor = PythonFunctionExecutor(bindings)
    runtime = OntologyRuntime(
        schema=schema, catalog=catalog, store=state_store, executor=executor
    )
    if seed_data and runtime.count_objects() == 0:
        objects, relations = parse_seed(seed_data)
        runtime.seed(objects, relations)
    return runtime
