"""MemoryStateStore: the default store.  Zero dependencies, fast, ideal for
tests, agent skills and demos.

Internal layout:
    objects[ObjectRef]                -> Object
    out[(source_ref, relation)]       -> set of target refs
    in_[(target_ref, relation)]       -> set of source refs
"""

from __future__ import annotations

from ..core.errors import ObjectNotFound
from ..core.execution import (
    MUTATION_LINK,
    MUTATION_SET_PROPERTY,
    MUTATION_UNLINK,
    Change,
    Mutation,
)
from ..core.refs import ObjectRef
from ..core.state import Object, Relation
from .filters import match_object


class MemoryStateStore:

    def __init__(self) -> None:
        self._objects: dict[ObjectRef, Object] = {}
        self._out: dict[tuple[ObjectRef, str], set[ObjectRef]] = {}
        self._in: dict[tuple[ObjectRef, str], set[ObjectRef]] = {}

    # ---- seeding (not part of the Port; convenience for bootstrap) ----

    def seed(self, objects: list[Object], relations: list[Relation]) -> None:
        for obj in objects:
            self._objects[obj.ref] = Object(obj.ref, dict(obj.properties))
        for rel in relations:
            self._link(rel.source, rel.relation, rel.target)

    # ---- StateStore Port ----

    def get_object(self, ref: ObjectRef) -> Object | None:
        obj = self._objects.get(ref)
        if obj is None:
            return None
        return Object(obj.ref, dict(obj.properties))  # defensive copy

    def find_objects(
        self, object_type: str, filters: dict[str, object]
    ) -> list[Object]:
        results = []
        for ref, obj in self._objects.items():
            if ref.type != object_type:
                continue
            if match_object(obj.properties, filters):
                results.append(Object(ref, dict(obj.properties)))
        return results

    def related(
        self, source: ObjectRef, relation: str, direction: str
    ) -> list[ObjectRef]:
        if direction == "out":
            return sorted(self._out.get((source, relation), set()), key=str)
        if direction == "in":
            return sorted(self._in.get((source, relation), set()), key=str)
        raise ValueError(f"unknown direction: {direction!r}")

    def apply(self, mutations: list[Mutation]) -> list[Change]:
        changes: list[Change] = []
        # validate state-level preconditions first (atomicity)
        for m in mutations:
            self._check(m)
        # then apply
        for m in mutations:
            change = self._apply_one(m)
            if change is not None:
                changes.append(change)
        return changes

    # ---- internals ----

    def _check(self, m: Mutation) -> None:
        if m.kind == MUTATION_SET_PROPERTY:
            if m.object_ref not in self._objects:
                raise ObjectNotFound(f"object not found: {m.object_ref}")
        elif m.kind in (MUTATION_LINK, MUTATION_UNLINK):
            for ref in (m.source, m.target):
                if ref not in self._objects:
                    raise ObjectNotFound(f"object not found: {ref}")

    def _apply_one(self, m: Mutation) -> Change | None:
        if m.kind == MUTATION_SET_PROPERTY:
            obj = self._objects[m.object_ref]
            old = obj.properties.get(m.property)
            new = m.value
            if old == new and type(old) is type(new):
                return None  # no-op change
            obj.properties[m.property] = new
            return Change(
                kind=MUTATION_SET_PROPERTY,
                object_ref=m.object_ref,
                property=m.property,
                old=old,
                new=new,
            )
        if m.kind == MUTATION_LINK:
            return self._link(m.source, m.relation, m.target)
        if m.kind == MUTATION_UNLINK:
            return self._unlink(m.source, m.relation, m.target)
        raise ValueError(f"unknown mutation kind: {m.kind!r}")

    def _link(self, source: ObjectRef, relation: str, target: ObjectRef) -> Change | None:
        key = (source, relation)
        targets = self._out.setdefault(key, set())
        if target in targets:
            return None  # (source, relation, target) is unique; idempotent
        targets.add(target)
        self._in.setdefault((target, relation), set()).add(source)
        return Change(kind=MUTATION_LINK, source=source, relation=relation, target=target)

    def _unlink(self, source: ObjectRef, relation: str, target: ObjectRef) -> Change | None:
        key = (source, relation)
        targets = self._out.get(key)
        if not targets or target not in targets:
            return None  # idempotent
        targets.discard(target)
        self._in.get((target, relation), set()).discard(source)
        return Change(kind=MUTATION_UNLINK, source=source, relation=relation, target=target)
