"""Schema: the description of the business world.

``OntologySchema`` describes objects and relations (what the world is).
``CapabilityCatalog`` describes functions (what the world can do).
Schema is immutable once loaded and validated; state is what changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import FunctionNotFound
from .types import TypeDef


@dataclass(frozen=True, slots=True)
class PropertyDef:
    """A property of an ObjectType.  Not a runtime entity of its own."""

    type: TypeDef
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ObjectType:
    """A kind of business object, e.g. ``Vehicle``."""

    name: str
    description: str | None = None
    properties: dict[str, PropertyDef] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RelationDef:
    """A semantic relation between object types.

    ``Vehicle --passes--> TollStation``
    Relation properties (time/amount/...) are reserved for future versions:
    not implemented, but the architecture leaves room for them.
    """

    name: str
    source: str
    target: str
    cardinality: str | None = None


@dataclass(frozen=True, slots=True)
class OntologySchema:
    """The structural model of the world.  Immutable after load."""

    name: str
    version: int = 1
    namespace: str | None = None
    objects: dict[str, ObjectType] = field(default_factory=dict)
    relations: dict[str, RelationDef] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FunctionDef:
    """A business capability, e.g. ``Vehicle.recharge``.

    Deliberately free of implementation details (script/executor); those
    live in Function Binding at the infrastructure layer.
    """

    name: str
    target_type: str | None
    description: str
    inputs: dict[str, TypeDef] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CapabilityCatalog:
    """All executable capabilities of the runtime.  Immutable after load."""

    functions: dict[str, FunctionDef] = field(default_factory=dict)

    def get(self, name: str) -> FunctionDef:
        try:
            return self.functions[name]
        except KeyError:
            raise FunctionNotFound(f"function not found: {name!r}") from None
