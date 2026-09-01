"""Minimal type system.

First version kinds: string / number / integer / boolean / object / array /
datetime.  Other type systems (JSON Schema, Python, TypeScript, MCP) are the
job of adapters, not of the kernel.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .errors import SchemaViolation

TYPE_KINDS: tuple[str, ...] = (
    "string",
    "number",
    "integer",
    "boolean",
    "object",
    "array",
    "datetime",
)


@dataclass(frozen=True, slots=True)
class TypeDef:
    """A scalar/structural type reference, e.g. ``TypeDef("number")``."""

    kind: str

    def __post_init__(self) -> None:
        if self.kind not in TYPE_KINDS:
            raise SchemaViolation(
                f"unknown type kind {self.kind!r}, expected one of {list(TYPE_KINDS)}"
            )

    def check(self, value: object) -> bool:
        """Return True when ``value`` conforms to this type."""
        kind = self.kind
        if kind == "string":
            return isinstance(value, str)
        if kind == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if kind == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if kind == "boolean":
            return isinstance(value, bool)
        if kind == "object":
            return isinstance(value, dict)
        if kind == "array":
            return isinstance(value, list)
        if kind == "datetime":
            if not isinstance(value, str):
                return False
            try:
                datetime.fromisoformat(value)
                return True
            except ValueError:
                return False
        return False
