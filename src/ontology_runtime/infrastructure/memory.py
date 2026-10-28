"""MemoryStateStore: the default store.  Zero dependencies, fast, ideal for
tests, agent skills and demos.

Internal layout:
    objects[ObjectRef]                -> Object
    out[(source_ref, relation)]       -> set of target refs
    in_[(target_ref, relation)]       -> set of source refs
    changes[]                         -> append-only change log (Change)

v0.2: objects carry version/updated_at/source metadata; every applied change
is logged with a continuous ``seq`` (the memory counterpart of the SQLite
change table) and stamped with ts/execution_id.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from ..core.errors import ObjectNotFound
from ..core.execution import (
    MUTATION_CREATE_OBJECT,
    MUTATION_DELETE_OBJECT,
    MUTATION_LINK,
    MUTATION_SET_PROPERTY,
    MUTATION_UNLINK,
    Change,
    Mutation,
)
from ..core.refs import ObjectRef
from ..core.state import Object, Relation
from .filters import match_object


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryStateStore:

    def __init__(self) -> None:
        self._objects: dict[ObjectRef, Object] = {}
        self._out: dict[tuple[ObjectRef, str], set[ObjectRef]] = {}
        self._in: dict[tuple[ObjectRef, str], set[ObjectRef]] = {}
        self._changes: list[Change] = []
        self._seq = 0

    # ---- seeding (not part of the Port; convenience for bootstrap) ----

    def seed(self, objects: list[Object], relations: list[Relation]) -> None:
        """Insert objects only when absent: seeding never overwrites live
        state (v0.9 fix - a restart must not reset the world)."""
        for obj in objects:
            if obj.ref in self._objects:
                continue
            self._objects[obj.ref] = Object(
                obj.ref,
                dict(obj.properties),
                obj.version,
                obj.updated_at,
                obj.source,
            )
        for rel in relations:
            self._link(rel.source, rel.relation, rel.target)

    # ---- StateStore Port ----

    def get_object(self, ref: ObjectRef) -> Object | None:
        obj = self._objects.get(ref)
        if obj is None:
            return None
        return Object(
            obj.ref, dict(obj.properties), obj.version, obj.updated_at, obj.source
        )  # defensive copy

    def find_objects(
        self, object_type: str, filters: dict[str, object]
    ) -> list[Object]:
        results = []
        for ref, obj in self._objects.items():
            if ref.type != object_type:
                continue
            if match_object(obj.properties, filters):
                results.append(
                    Object(
                        ref,
                        dict(obj.properties),
                        obj.version,
                        obj.updated_at,
                        obj.source,
                    )
                )
        return results

    def related(
        self, source: ObjectRef, relation: str, direction: str
    ) -> list[ObjectRef]:
        if direction == "out":
            return sorted(self._out.get((source, relation), set()), key=str)
        if direction == "in":
            return sorted(self._in.get((source, relation), set()), key=str)
        raise ValueError(f"unknown direction: {direction!r}")

    def count_objects(self) -> int:
        return len(self._objects)

    def list_changes(self, after_seq: int = 0) -> list[Change]:
        """Change-log entries with ``seq > after_seq``, oldest first."""
        return [change for change in self._changes if change.seq > after_seq]

    def apply(
        self, mutations: list[Mutation], *, execution_id: str | None = None
    ) -> list[Change]:
        ts = _now()
        # validate state-level preconditions first (atomicity)
        for m in mutations:
            self._check(m)
        # then apply; only real changes are logged
        changes: list[Change] = []
        for m in mutations:
            change = self._apply_one(m, ts, execution_id)
            if change is not None:
                changes.append(self._log_change(change))
        return changes

    def apply_delta(
        self,
        objects: list[Object],
        relations: list[Relation],
        mutations: list[Mutation],
        *,
        execution_id: str | None = None,
        source: str | None = None,
    ) -> list[Change]:
        """One atomic delta: upsert objects, assert relations, apply mutations."""
        objects = list(objects or ())
        relations = list(relations or ())
        mutations = list(mutations or ())
        ts = _now()
        creating = {obj.ref for obj in objects if obj.ref not in self._objects}
        # validate everything first - a bad delta applies nothing
        for rel in relations:
            for ref in (rel.source, rel.target):
                if ref not in self._objects and ref not in creating:
                    raise ObjectNotFound(f"object not found: {ref}")
        for m in mutations:
            self._check(m, allowed_refs=creating)

        changes: list[Change] = []
        for obj in objects:
            existing = self._objects.get(obj.ref)
            if existing is None:
                self._objects[obj.ref] = Object(
                    obj.ref,
                    dict(obj.properties),
                    obj.version,
                    obj.updated_at or ts,
                    obj.source if obj.source is not None else source,
                )
                changes.append(
                    Change(
                        kind=MUTATION_CREATE_OBJECT,
                        object_ref=obj.ref,
                        new=dict(obj.properties),
                        ts=ts,
                        execution_id=execution_id,
                    )
                )
            else:
                changes.extend(
                    self._merge_properties(existing, obj, ts, execution_id, source)
                )
        for rel in relations:
            change = self._link(rel.source, rel.relation, rel.target, ts, execution_id)
            if change is not None:
                changes.append(change)
        for m in mutations:
            change = self._apply_one(m, ts, execution_id)
            if change is not None:
                changes.append(change)
        return [self._log_change(change) for change in changes]

    # ---- internals ----

    def delete_objects(
        self, refs: list[ObjectRef], *, execution_id: str | None = None
    ) -> list[Change]:
        """Delete objects + incident relations; log unlink then delete_object."""
        ts = _now()
        changes: list[Change] = []
        seen: set[ObjectRef] = set()
        for ref in refs:
            if ref in seen:
                continue
            seen.add(ref)
            obj = self._objects.pop(ref, None)
            if obj is None:
                continue  # idempotent
            for (src, rel), targets in list(self._out.items()):
                if src != ref:
                    continue
                for target in sorted(targets, key=str):
                    self._in.get((target, rel), set()).discard(src)
                    changes.append(
                        Change(kind=MUTATION_UNLINK, source=src, relation=rel,
                               target=target, ts=ts,
                               execution_id=execution_id))
                self._out.pop((src, rel), None)
            for (target, rel), sources in list(self._in.items()):
                if target != ref:
                    continue
                for src in sorted(sources, key=str):
                    self._out.get((src, rel), set()).discard(target)
                    changes.append(
                        Change(kind=MUTATION_UNLINK, source=src, relation=rel,
                               target=target, ts=ts,
                               execution_id=execution_id))
                self._in.pop((target, rel), None)
            changes.append(
                Change(kind=MUTATION_DELETE_OBJECT, object_ref=ref,
                       old=dict(obj.properties), ts=ts,
                       execution_id=execution_id))
        return [self._log_change(change) for change in changes]

    def _log_change(self, change: Change) -> Change:
        self._seq += 1
        logged = replace(change, seq=self._seq)
        self._changes.append(logged)
        return logged

    def _check(self, m: Mutation, allowed_refs: frozenset[ObjectRef] = frozenset()) -> None:
        if m.kind == MUTATION_SET_PROPERTY:
            ref = m.object_ref
            if ref is None or (ref not in self._objects and ref not in allowed_refs):
                raise ObjectNotFound(f"object not found: {ref}")
        elif m.kind in (MUTATION_LINK, MUTATION_UNLINK):
            for ref in (m.source, m.target):
                if ref is None or (ref not in self._objects and ref not in allowed_refs):
                    raise ObjectNotFound(f"object not found: {ref}")
        else:
            raise ValueError(f"unknown mutation kind: {m.kind!r}")

    def _merge_properties(
        self,
        existing: Object,
        obj: Object,
        ts: str,
        execution_id: str | None,
        source: str | None,
    ) -> list[Change]:
        changes: list[Change] = []
        for prop, value in obj.properties.items():
            old = existing.properties.get(prop)
            if old == value and type(old) is type(value):
                continue  # no-op
            existing.properties[prop] = value
            existing.version += 1
            existing.updated_at = ts
            if source is not None:
                existing.source = source
            changes.append(
                Change(
                    kind=MUTATION_SET_PROPERTY,
                    object_ref=obj.ref,
                    property=prop,
                    old=old,
                    new=value,
                    ts=ts,
                    execution_id=execution_id,
                )
            )
        return changes

    def _apply_one(
        self, m: Mutation, ts: str, execution_id: str | None
    ) -> Change | None:
        if m.kind == MUTATION_SET_PROPERTY:
            obj = self._objects[m.object_ref]
            old = obj.properties.get(m.property)
            new = m.value
            if old == new and type(old) is type(new):
                return None  # no-op change
            obj.properties[m.property] = new
            obj.version += 1
            obj.updated_at = ts
            if execution_id is not None:
                obj.source = "invoke"
            return Change(
                kind=MUTATION_SET_PROPERTY,
                object_ref=m.object_ref,
                property=m.property,
                old=old,
                new=new,
                ts=ts,
                execution_id=execution_id,
            )
        if m.kind == MUTATION_LINK:
            return self._link(m.source, m.relation, m.target, ts, execution_id)
        if m.kind == MUTATION_UNLINK:
            return self._unlink(m.source, m.relation, m.target, ts, execution_id)
        raise ValueError(f"unknown mutation kind: {m.kind!r}")

    def _link(
        self,
        source: ObjectRef,
        relation: str,
        target: ObjectRef,
        ts: str | None = None,
        execution_id: str | None = None,
    ) -> Change | None:
        key = (source, relation)
        targets = self._out.setdefault(key, set())
        if target in targets:
            return None  # (source, relation, target) is unique; idempotent
        targets.add(target)
        self._in.setdefault((target, relation), set()).add(source)
        return Change(
            kind=MUTATION_LINK,
            source=source,
            relation=relation,
            target=target,
            ts=ts,
            execution_id=execution_id,
        )

    def _unlink(
        self,
        source: ObjectRef,
        relation: str,
        target: ObjectRef,
        ts: str | None = None,
        execution_id: str | None = None,
    ) -> Change | None:
        key = (source, relation)
        targets = self._out.get(key)
        if not targets or target not in targets:
            return None  # idempotent
        targets.discard(target)
        self._in.get((target, relation), set()).discard(source)
        return Change(
            kind=MUTATION_UNLINK,
            source=source,
            relation=relation,
            target=target,
            ts=ts,
            execution_id=execution_id,
        )
