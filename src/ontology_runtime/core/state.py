"""Runtime state: the world as it is right now.

State contains only objects and relations - never schema, function
definitions, execution records or audit data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .refs import ObjectRef


@dataclass(slots=True)
class Object:
    """One object instance.

    The ``properties`` dict should not be mutated directly by callers;
    changes go through Runtime mutations (ctx.set / ctx.link / ...).
    """

    ref: ObjectRef
    properties: dict[str, object] = field(default_factory=dict)

    def get(self, key: str, default: object = None) -> object:
        """Convenience accessor used inside functions (``vehicle.get("balance")``)."""
        return self.properties.get(key, default)


@dataclass(frozen=True, slots=True)
class Relation:
    """One relation edge: ``(source, relation, target)`` is unique."""

    source: ObjectRef
    relation: str
    target: ObjectRef
