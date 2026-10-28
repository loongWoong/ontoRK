"""Schema: the description of the business world.

``OntologySchema`` describes objects and relations (what the world is).
``CapabilityCatalog`` describes functions (what the world can do).
Schema is immutable once loaded and validated; state is what changes.

v0.2 adds declarative constraints to the schema (``PropertyDef``,
``ObjectType.identity`` / ``state_machine``, ``RelationDef.cardinality`` /
``inverse``).  The model only *describes* constraints; enforcing them on
writes is the runtime's job (see ``runtime/runtime.py``).  Keeping the
description declarative preserves the Kernel's "schema is data" property.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import FunctionNotFound, SchemaViolation
from .types import TypeDef

#: Legal cardinality values.  ``""`` and ``None`` both mean "unspecified".
CARDINALITIES: tuple[str, ...] = (
    "",
    "one-to-one",
    "one-to-many",
    "many-to-one",
    "many-to-many",
)


def _normalize_state_machine(raw: object) -> dict | None:
    """Normalize / validate a state-machine definition.

    Canonical wire shape (YAML/JSON)::

        state_machine:
          property: status          # which property carries the state
          initial: open             # optional
          states: [open, closed]    # optional; inferred when omitted
          transitions:
            open: [closed]
            closed: [open]

    ``transitions`` may also be a list of ``[from, to]`` pairs or
    ``"from->to"`` strings; the normalized model is always a mapping.
    Invalid definitions raise :class:`SchemaViolation` at load time -
    schema is immutable, so there is no later moment to fix it.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SchemaViolation(
            f"state_machine must be a mapping, got {type(raw).__name__}"
        )
    prop = raw.get("property")
    if prop is None:
        prop = raw.get("field")  # accepted alias used by platform meta-model
    if not isinstance(prop, str) or not prop:
        raise SchemaViolation("state_machine requires a non-empty 'property'")

    raw_transitions = raw.get("transitions")
    transitions: dict[str, tuple[str, ...]] = {}
    if raw_transitions is None:
        raw_transitions = {}
    if isinstance(raw_transitions, dict):
        items = list(raw_transitions.items())
    elif isinstance(raw_transitions, (list, tuple)):
        items = []
        for item in raw_transitions:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                items.append((item[0], [item[1]]))
            elif isinstance(item, str) and "->" in item:
                src, _, dst = item.partition("->")
                items.append((src.strip(), [dst.strip()]))
            else:
                raise SchemaViolation(
                    f"state_machine transition {item!r} must be 'from->to' "
                    "or a [from, to] pair"
                )
    else:
        raise SchemaViolation(
            "state_machine 'transitions' must be a mapping or a list"
        )
    for src, dsts in items:
        if not isinstance(src, str) or not src:
            raise SchemaViolation(f"state_machine has an invalid state {src!r}")
        if isinstance(dsts, str):
            dsts = [dsts]
        if not isinstance(dsts, (list, tuple)):
            raise SchemaViolation(
                f"state_machine transitions for {src!r} must be a list"
            )
        converted = tuple(str(dst) for dst in dsts)
        transitions[src] = tuple(dict.fromkeys((*transitions.get(src, ()), *converted)))

    raw_states = raw.get("states")
    if raw_states is None:
        states: tuple[str, ...] = ()
    elif isinstance(raw_states, (list, tuple)):
        states = tuple(str(s) for s in raw_states)
    else:
        raise SchemaViolation("state_machine 'states' must be a list")

    initial = raw.get("initial")
    if initial is not None:
        initial = str(initial)

    known = set(states)
    known.update(transitions)
    for dsts in transitions.values():
        known.update(dsts)
    if states:
        if initial is not None and initial not in states:
            raise SchemaViolation(
                f"state_machine initial state {initial!r} is not in states"
            )
        for state in known:
            if state not in states:
                raise SchemaViolation(
                    f"state_machine references undeclared state {state!r}"
                )

    return {
        "property": prop,
        "initial": initial,
        "states": states,
        "transitions": transitions,
    }


@dataclass(frozen=True, slots=True)
class PropertyDef:
    """A property of an ObjectType.  Not a runtime entity of its own.

    ``type`` is the only mandatory declaration; everything else is an
    optional constraint the runtime enforces on every write (v0.2).
    """

    type: TypeDef
    description: str | None = None
    required: bool = False
    enum: tuple[object, ...] | None = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    unique: bool = False
    unit: str | None = None
    precision: int | None = None
    default: object = None
    temporal: bool | str = False

    def __post_init__(self) -> None:
        if self.enum is not None:
            if not isinstance(self.enum, (list, tuple)) or not self.enum:
                raise SchemaViolation("property enum must be a non-empty list")
            object.__setattr__(self, "enum", tuple(self.enum))
            for value in self.enum:
                if not self.type.check(value):
                    raise SchemaViolation(
                        f"enum value {value!r} does not conform to {self.type.kind}"
                    )
        for name in ("minimum", "maximum"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SchemaViolation(f"property {name} must be a number")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise SchemaViolation("property minimum must be <= maximum")
        if self.precision is not None:
            if (
                isinstance(self.precision, bool)
                or not isinstance(self.precision, int)
                or self.precision < 0
            ):
                raise SchemaViolation("property precision must be a non-negative int")
        if self.unit is not None and not isinstance(self.unit, str):
            raise SchemaViolation("property unit must be a string")
        for name in ("required", "unique"):
            if not isinstance(getattr(self, name), bool):
                raise SchemaViolation(f"property {name} must be a boolean")
        if not isinstance(self.temporal, bool) and not (
            isinstance(self.temporal, str) and self.temporal
        ):
            raise SchemaViolation(
                "property temporal must be a boolean or a non-empty mode string"
            )
        if self.default is not None and not self.type.check(self.default):
            raise SchemaViolation(
                f"property default {self.default!r} does not conform to "
                f"{self.type.kind}"
            )


@dataclass(frozen=True, slots=True)
class ObjectType:
    """A kind of business object, e.g. ``Vehicle``.

    ``identity`` is the business key: a tuple of property names whose
    combination is unique among instances of this type.  ``category``
    classifies the type (default ``"object"``; e.g. ``"event"``).
    ``state_machine`` declares the legal lifecycle of one property.
    """

    name: str
    description: str | None = None
    properties: dict[str, PropertyDef] = field(default_factory=dict)
    identity: tuple[str, ...] = ()
    category: str = "object"
    state_machine: dict | None = None

    def __post_init__(self) -> None:
        identity = tuple(self.identity or ())
        if len(set(identity)) != len(identity):
            raise SchemaViolation(f"{self.name}: identity has duplicate properties")
        for prop in identity:
            if not isinstance(prop, str) or prop not in self.properties:
                raise SchemaViolation(
                    f"{self.name}: identity property {prop!r} is not defined"
                )
        object.__setattr__(self, "identity", identity)
        if not isinstance(self.category, str) or not self.category:
            raise SchemaViolation(f"{self.name}: category must be a non-empty string")
        state_machine = _normalize_state_machine(self.state_machine)
        if state_machine is not None and state_machine["property"] not in self.properties:
            raise SchemaViolation(
                f"{self.name}: state_machine property "
                f"{state_machine['property']!r} is not defined"
            )
        object.__setattr__(self, "state_machine", state_machine)


@dataclass(frozen=True, slots=True)
class RelationDef:
    """A semantic relation between object types.

    ``Vehicle --passes--> TollStation``
    ``inverse`` optionally names the reverse navigation role.  Relation
    properties (time/amount/...) remain reserved for future versions.
    """

    name: str
    source: str
    target: str
    cardinality: str | None = None
    inverse: str | None = None

    def __post_init__(self) -> None:
        if self.cardinality not in (None, *CARDINALITIES):
            raise SchemaViolation(
                f"relation {self.name!r}: invalid cardinality {self.cardinality!r}, "
                f"expected one of {list(CARDINALITIES)}"
            )
        if self.inverse is not None and (
            not isinstance(self.inverse, str) or not self.inverse
        ):
            raise SchemaViolation(
                f"relation {self.name!r}: inverse must be a non-empty string"
            )


@dataclass(frozen=True, slots=True)
class OntologySchema:
    """The structural model of the world.  Immutable after load."""

    name: str
    version: int = 1
    namespace: str | None = None
    objects: dict[str, ObjectType] = field(default_factory=dict)
    relations: dict[str, RelationDef] = field(default_factory=dict)
    profile: str = "standard"
    schema_version: int = 1


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
