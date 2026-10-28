"""Serialization: canonical model -> JSON-friendly dicts.

Every adapter (CLI / HTTP / MCP) renders the same canonical shapes, so
semantics cannot drift between protocols (Principle 5 + 38).

v0.2: Object metadata (version/updated_at/source) is part of the canonical
shape; ``create_object`` changes expose the created properties.  The
``set_property``/``link``/``unlink`` change payloads keep their v0.1 shape
so existing adapters and consumers stay wire-compatible.
"""

from __future__ import annotations

from dataclasses import is_dataclass

from ..core.errors import KernelError
from ..core.execution import (
    MUTATION_CREATE_OBJECT,
    MUTATION_DELETE_OBJECT,
    Change,
)
from ..core.refs import ObjectRef
from ..core.state import Object


def to_jsonable(value):
    """Recursively convert kernel objects into JSON-serializable data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, ObjectRef):
        return str(value)
    if isinstance(value, Object):
        return {
            "type": value.ref.type,
            "id": value.ref.id,
            "properties": value.properties,
            "version": value.version,
            "updated_at": value.updated_at,
            "source": value.source,
        }
    if isinstance(value, Change):
        out = {"kind": value.kind}
        if value.object_ref is not None:
            out["object"] = str(value.object_ref)
        if value.property is not None:
            out["property"] = value.property
            out["old"] = to_jsonable(value.old)
            out["new"] = to_jsonable(value.new)
        if value.kind == MUTATION_CREATE_OBJECT and value.object_ref is not None:
            out["properties"] = to_jsonable(value.new)
        if value.kind == MUTATION_DELETE_OBJECT and value.object_ref is not None:
            # last known properties, so consumers can react to the removal
            out["properties"] = to_jsonable(value.old)
        if value.source is not None:
            out["source"] = str(value.source)
        if value.relation is not None:
            out["relation"] = value.relation
        if value.target is not None:
            out["target"] = str(value.target)
        return out
    if isinstance(value, KernelError):
        return {"type": type(value).__name__, "message": str(value)}
    if isinstance(value, Exception):
        return {"type": type(value).__name__, "message": str(value)}
    if is_dataclass(value):
        # walk fields manually so nested kernel objects (Change, ObjectRef,
        # Object) keep their types and hit their own branches
        return {
            name: to_jsonable(getattr(value, name))
            for name in value.__dataclass_fields__
        }
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    return str(value)


def error_to_dict(error: Exception) -> dict:
    return {"type": type(error).__name__, "message": str(error)}
