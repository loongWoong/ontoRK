"""Execution model: FunctionCall -> Mutations -> Changes -> FunctionResult.

A function never touches the store directly and can never commit state:
it only records mutations on the execution context.  After the function
returns, the runtime validates the whole buffer and applies it as one
logical unit, turning it into changes (one invocation = one atomic
state change).

v0.2: every applied Change is stamped with a store-assigned ``seq`` (a
continuous change-log sequence), a ``ts`` timestamp and the
``execution_id`` that produced it; the FunctionResult reports a
``status`` (succeeded / failed / unknown) so callers can tell a clean
failure from an unknown outcome (e.g. a timeout).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .errors import InvalidArgument, ObjectNotFound
from .refs import ObjectRef
from .state import Object

MUTATION_SET_PROPERTY = "set_property"
MUTATION_LINK = "link"
MUTATION_UNLINK = "unlink"
#: Change kind emitted by ``StateStore.apply_delta`` when it materializes an object.
MUTATION_CREATE_OBJECT = "create_object"
#: Change kind emitted by ``StateStore.delete_objects`` when the source of
#: truth no longer contains an object (projection deletion propagation).
MUTATION_DELETE_OBJECT = "delete_object"

STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"
STATUSES: tuple[str, ...] = (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_UNKNOWN)


@dataclass(frozen=True, slots=True)
class Mutation:
    """A pending state change produced by a function.  Not yet applied."""

    kind: str
    object_ref: ObjectRef | None = None
    property: str | None = None
    value: object = None
    source: ObjectRef | None = None
    relation: str | None = None
    target: ObjectRef | None = None


@dataclass(frozen=True, slots=True)
class Change:
    """An applied state change.  ``old``/``new`` are filled by the store.

    ``seq``/``ts``/``execution_id`` are appended at the end so existing
    positional construction stays valid; the store fills them when it logs
    the change.
    """

    kind: str
    object_ref: ObjectRef | None = None
    property: str | None = None
    old: object = None
    new: object = None
    source: ObjectRef | None = None
    relation: str | None = None
    target: ObjectRef | None = None
    seq: int = 0
    ts: str | None = None
    execution_id: str | None = None


@dataclass(frozen=True, slots=True)
class FunctionCall:
    """One concrete invocation request."""

    function: str
    target: ObjectRef | None
    args: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class FunctionResult:
    """Standardized invocation result.

    ``output``  - what the caller receives
    ``changes`` - what changed in the world
    ``error``   - why it failed
    ``status``  - ``succeeded`` / ``failed`` / ``unknown`` (appended, v0.2)

    output != changes; they answer different questions.
    """

    execution_id: str
    output: object | None = None
    changes: list[Change] = field(default_factory=list)
    error: Exception | None = None
    status: str = STATUS_SUCCEEDED

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise InvalidArgument(
                f"invalid FunctionResult status {self.status!r}, "
                f"expected one of {list(STATUSES)}"
            )


@dataclass(slots=True)
class ExecutionContext:
    """The only world a function sees.

    Read access (object/get/find/related) is a facade over the state store.
    Write access (set/link/unlink) only buffers mutations.  There is no way
    for a function to apply or commit them: the runtime drains the buffer
    after the function returns and applies it atomically.  A function that
    fails mid-way therefore never leaves partial state behind.
    """

    function: str
    execution_id: str
    target: ObjectRef | None
    args: dict[str, object]
    _get_object: Callable[[ObjectRef], Object | None]
    _find_objects: Callable[[str, dict], list[Object]]
    _related: Callable[[ObjectRef, str, str], list[ObjectRef]]
    _mutations: list[Mutation] = field(default_factory=list)

    # ---- read ----

    def object(self) -> Object:
        """The target object of this invocation."""
        if self.target is None:
            raise InvalidArgument(f"function {self.function!r} has no target object")
        obj = self._get_object(self.target)
        if obj is None:
            raise ObjectNotFound(f"object not found: {self.target}")
        return obj

    def get(self, ref: ObjectRef) -> Object:
        obj = self._get_object(ref)
        if obj is None:
            raise ObjectNotFound(f"object not found: {ref}")
        return obj

    def find(self, object_type: str, **filters: object) -> list[Object]:
        return self._find_objects(object_type, filters)

    def related(
        self, ref: ObjectRef, relation: str, direction: str = "out"
    ) -> list[ObjectRef]:
        return self._related(ref, relation, direction)

    def ref(self, object_type: str, object_id: str) -> ObjectRef:
        """Build an ObjectRef without importing kernel types in functions."""
        return ObjectRef(object_type, object_id)

    # ---- write (buffered) ----

    def set(self, ref: ObjectRef, property: str, value: object) -> None:
        self._mutations.append(
            Mutation(
                kind=MUTATION_SET_PROPERTY,
                object_ref=ref,
                property=property,
                value=value,
            )
        )

    def link(self, source: ObjectRef, relation: str, target: ObjectRef) -> None:
        self._mutations.append(
            Mutation(kind=MUTATION_LINK, source=source, relation=relation, target=target)
        )

    def unlink(self, source: ObjectRef, relation: str, target: ObjectRef) -> None:
        self._mutations.append(
            Mutation(kind=MUTATION_UNLINK, source=source, relation=relation, target=target)
        )

    # ---- buffer handoff (runtime only) ----

    def take_mutations(self) -> list[Mutation]:
        """Drain the mutation buffer; the runtime applies the result.

        Functions normally never call this - the runtime does, exactly once,
        after the function returns.  Draining mid-function is equivalent to
        "these mutations never happened": nothing is applied until the
        runtime sees the buffer after a normal return.
        """
        mutations = list(self._mutations)
        self._mutations.clear()
        return mutations

    def fail(self, message: str) -> None:
        """Raise a business error that becomes ``FunctionResult.error``."""
        from .errors import FunctionExecutionError

        raise FunctionExecutionError(message)
