"""Runtime state: the world as it is right now.

State contains only objects and relations - never schema, function
definitions, execution records or audit data.

v0.2 temporalizes state: every object carries a monotonically increasing
``version``, the ``updated_at`` timestamp of its last property write and the
``source`` label of that write (e.g. ``"seed"`` / ``"projection"`` /
``"invoke"``).  History itself lives in the store's change log, not here:
state answers "what is", the change log answers "what happened".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .refs import ObjectRef


@dataclass(slots=True)
class Object:
    """One object instance.

    The ``properties`` dict should not be mutated directly by callers;
    changes go through Runtime mutations (ctx.set / ctx.link / ...).

    ``ref`` and ``properties`` stay first so ``Object(ref, props)`` keeps
    working positionally; the temporal metadata is appended (v0.2).
    """

    ref: ObjectRef
    properties: dict[str, object] = field(default_factory=dict)
    version: int = 1
    updated_at: str | None = None
    source: str | None = None

    def get(self, key: str, default: object = None) -> object:
        """Convenience accessor used inside functions (``vehicle.get("balance")``)."""
        return self.properties.get(key, default)


@dataclass(frozen=True, slots=True)
class Relation:
    """One relation edge: ``(source, relation, target)`` is unique."""

    source: ObjectRef
    relation: str
    target: ObjectRef
