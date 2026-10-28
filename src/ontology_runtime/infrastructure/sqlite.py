"""SQLiteStateStore: the same Port over SQLite.

SQLite concepts never leak into Core; this file is the only place that
knows SQL exists.

v0.2:
    - objects carry version / updated_at / source columns (old databases are
      upgraded in place via ALTER TABLE - no data loss, no migration tool)
    - an append-only ``changes`` table (seq INTEGER PRIMARY KEY AUTOINCREMENT)
      is written in the same transaction as the state change it describes, so
      the change log can never disagree with the state
    - ``apply_delta`` upserts objects + relations + mutations atomically
"""

from __future__ import annotations

import json
import sqlite3
import threading
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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    otype TEXT NOT NULL,
    oid   TEXT NOT NULL,
    properties TEXT NOT NULL,
    PRIMARY KEY (otype, oid)
);
CREATE TABLE IF NOT EXISTS relations (
    relation TEXT NOT NULL,
    src_type TEXT NOT NULL,
    src_id   TEXT NOT NULL,
    tgt_type TEXT NOT NULL,
    tgt_id   TEXT NOT NULL,
    PRIMARY KEY (relation, src_type, src_id, tgt_type, tgt_id)
);
CREATE TABLE IF NOT EXISTS changes (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    execution_id TEXT,
    kind TEXT NOT NULL,
    otype TEXT,
    oid TEXT,
    property TEXT,
    old TEXT,
    new TEXT,
    source TEXT,
    relation TEXT,
    target TEXT
);
"""

# Columns added in v0.2.  Existing databases are upgraded with ALTER TABLE.
_OBJECT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("version", "INTEGER NOT NULL DEFAULT 1"),
    ("updated_at", "TEXT"),
    ("source", "TEXT"),
)

_OBJECT_SELECT = "SELECT properties, version, updated_at, source FROM objects"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


class SQLiteStateStore:

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        columns = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(objects)").fetchall()
        }
        for name, ddl in _OBJECT_COLUMNS:
            if name not in columns:
                self._conn.execute(f"ALTER TABLE objects ADD COLUMN {name} {ddl}")

    # ---- seeding (not part of the Port) ----

    def seed(self, objects: list[Object], relations: list[Relation]) -> None:
        """Insert only objects that do not exist yet; existing state wins."""
        with self._lock, self._conn:
            for obj in objects:
                self._conn.execute(
                    "INSERT OR IGNORE INTO objects "
                    "(otype, oid, properties, version, updated_at, source) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        obj.ref.type,
                        obj.ref.id,
                        _json(obj.properties),
                        obj.version,
                        obj.updated_at,
                        obj.source,
                    ),
                )
            for rel in relations:
                self._conn.execute(
                    "INSERT OR IGNORE INTO relations VALUES (?, ?, ?, ?, ?)",
                    (
                        rel.relation,
                        rel.source.type,
                        rel.source.id,
                        rel.target.type,
                        rel.target.id,
                    ),
                )

    # ---- StateStore Port ----

    def get_object(self, ref: ObjectRef) -> Object | None:
        with self._lock:
            row = self._conn.execute(
                _OBJECT_SELECT + " WHERE otype = ? AND oid = ?",
                (ref.type, ref.id),
            ).fetchone()
        if row is None:
            return None
        return Object(
            ref, json.loads(row[0]), int(row[1]), row[2], row[3]
        )

    def find_objects(
        self, object_type: str, filters: dict[str, object]
    ) -> list[Object]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT oid, properties, version, updated_at, source "
                "FROM objects WHERE otype = ?",
                (object_type,),
            ).fetchall()
        results = []
        for oid, props_json, version, updated_at, source in rows:
            props = json.loads(props_json)
            if match_object(props, filters):
                results.append(
                    Object(
                        ObjectRef(object_type, oid),
                        props,
                        int(version),
                        updated_at,
                        source,
                    )
                )
        return results

    def related(
        self, source: ObjectRef, relation: str, direction: str
    ) -> list[ObjectRef]:
        with self._lock:
            if direction == "out":
                rows = self._conn.execute(
                    "SELECT tgt_type, tgt_id FROM relations "
                    "WHERE relation = ? AND src_type = ? AND src_id = ?",
                    (relation, source.type, source.id),
                ).fetchall()
            elif direction == "in":
                rows = self._conn.execute(
                    "SELECT src_type, src_id FROM relations "
                    "WHERE relation = ? AND tgt_type = ? AND tgt_id = ?",
                    (relation, source.type, source.id),
                ).fetchall()
            else:
                raise ValueError(f"unknown direction: {direction!r}")
        return sorted((ObjectRef(t, i) for t, i in rows), key=str)

    def count_objects(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM objects").fetchone()
        return int(row[0])

    def list_changes(self, after_seq: int = 0) -> list[Change]:
        """Change-log entries with ``seq > after_seq``, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, ts, execution_id, kind, otype, oid, property, "
                "old, new, source, relation, target FROM changes "
                "WHERE seq > ? ORDER BY seq",
                (after_seq,),
            ).fetchall()
        changes: list[Change] = []
        for (
            seq,
            ts,
            execution_id,
            kind,
            otype,
            oid,
            prop,
            old_json,
            new_json,
            source,
            relation,
            target,
        ) in rows:
            changes.append(
                Change(
                    kind=kind,
                    object_ref=ObjectRef(otype, oid) if otype else None,
                    property=prop,
                    old=json.loads(old_json) if old_json is not None else None,
                    new=json.loads(new_json) if new_json is not None else None,
                    source=ObjectRef.parse(source) if source else None,
                    relation=relation,
                    target=ObjectRef.parse(target) if target else None,
                    seq=int(seq),
                    ts=ts,
                    execution_id=execution_id,
                )
            )
        return changes

    def apply(
        self, mutations: list[Mutation], *, execution_id: str | None = None
    ) -> list[Change]:
        ts = _now()
        with self._lock:
            # validate state-level preconditions first (atomicity)
            for m in mutations:
                self._check(m)
            with self._conn:  # BEGIN ... COMMIT
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
        with self._lock:
            creating = {obj.ref for obj in objects if not self._exists(obj.ref)}
            with self._conn:  # BEGIN ... COMMIT
                # validate everything first - a bad delta applies nothing
                for rel in relations:
                    for ref in (rel.source, rel.target):
                        if not self._exists(ref) and ref not in creating:
                            raise ObjectNotFound(f"object not found: {ref}")
                for m in mutations:
                    self._check(m, allowed_refs=creating)

                changes: list[Change] = []
                for obj in objects:
                    row = self._conn.execute(
                        _OBJECT_SELECT + " WHERE otype = ? AND oid = ?",
                        (obj.ref.type, obj.ref.id),
                    ).fetchone()
                    if row is None:
                        self._conn.execute(
                            "INSERT INTO objects "
                            "(otype, oid, properties, version, updated_at, source) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                obj.ref.type,
                                obj.ref.id,
                                _json(obj.properties),
                                obj.version,
                                obj.updated_at or ts,
                                obj.source if obj.source is not None else source,
                            ),
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
                            self._merge_properties(
                                obj, json.loads(row[0]), ts, execution_id, source
                            )
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
        """Delete objects + incident relations in one transaction."""
        ts = _now()
        changes: list[Change] = []
        seen: set[ObjectRef] = set()
        with self._lock, self._conn:
            for ref in refs:
                if ref in seen:
                    continue
                seen.add(ref)
                row = self._conn.execute(
                    "SELECT properties FROM objects WHERE otype = ? AND oid = ?",
                    (ref.type, ref.id),
                ).fetchone()
                if row is None:
                    continue  # idempotent
                old = json.loads(row[0])
                incident = self._conn.execute(
                    "SELECT relation, src_type, src_id, tgt_type, tgt_id "
                    "FROM relations WHERE (src_type = ? AND src_id = ?) "
                    "OR (tgt_type = ? AND tgt_id = ?)",
                    (ref.type, ref.id, ref.type, ref.id),
                ).fetchall()
                for relation, src_type, src_id, tgt_type, tgt_id in incident:
                    self._conn.execute(
                        "DELETE FROM relations WHERE relation = ? AND "
                        "src_type = ? AND src_id = ? AND tgt_type = ? AND tgt_id = ?",
                        (relation, src_type, src_id, tgt_type, tgt_id),
                    )
                    changes.append(
                        Change(kind=MUTATION_UNLINK,
                               source=ObjectRef(src_type, src_id),
                               relation=relation,
                               target=ObjectRef(tgt_type, tgt_id),
                               ts=ts, execution_id=execution_id))
                self._conn.execute(
                    "DELETE FROM objects WHERE otype = ? AND oid = ?",
                    (ref.type, ref.id),
                )
                changes.append(
                    Change(kind=MUTATION_DELETE_OBJECT, object_ref=ref,
                           old=old, ts=ts, execution_id=execution_id))
            return [self._log_change(change) for change in changes]

    def _log_change(self, change: Change) -> Change:
        cur = self._conn.execute(
            "INSERT INTO changes "
            "(ts, execution_id, kind, otype, oid, property, old, new, "
            "source, relation, target) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                change.ts,
                change.execution_id,
                change.kind,
                change.object_ref.type if change.object_ref is not None else None,
                change.object_ref.id if change.object_ref is not None else None,
                change.property,
                _change_value_json(change, "old"),
                _change_value_json(change, "new"),
                str(change.source) if change.source is not None else None,
                change.relation,
                str(change.target) if change.target is not None else None,
            ),
        )
        return replace(change, seq=int(cur.lastrowid))

    def _check(self, m: Mutation, allowed_refs: frozenset[ObjectRef] = frozenset()) -> None:
        if m.kind == MUTATION_SET_PROPERTY:
            ref = m.object_ref
            if ref is None or (not self._exists(ref) and ref not in allowed_refs):
                raise ObjectNotFound(f"object not found: {ref}")
        elif m.kind in (MUTATION_LINK, MUTATION_UNLINK):
            for ref in (m.source, m.target):
                if ref is None or (not self._exists(ref) and ref not in allowed_refs):
                    raise ObjectNotFound(f"object not found: {ref}")
        else:
            raise ValueError(f"unknown mutation kind: {m.kind!r}")

    def _exists(self, ref: ObjectRef) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM objects WHERE otype = ? AND oid = ?",
            (ref.type, ref.id),
        ).fetchone()
        return row is not None

    def _merge_properties(
        self,
        obj: Object,
        props: dict,
        ts: str,
        execution_id: str | None,
        source: str | None,
    ) -> list[Change]:
        changes: list[Change] = []
        for prop, value in obj.properties.items():
            old = props.get(prop)
            if old == value and type(old) is type(value):
                continue  # no-op
            props[prop] = value
            self._conn.execute(
                "UPDATE objects SET properties = ?, version = version + 1, "
                "updated_at = ?, source = COALESCE(?, source) "
                "WHERE otype = ? AND oid = ?",
                (
                    _json(props),
                    ts,
                    source,
                    obj.ref.type,
                    obj.ref.id,
                ),
            )
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
            row = self._conn.execute(
                "SELECT properties FROM objects WHERE otype = ? AND oid = ?",
                (m.object_ref.type, m.object_ref.id),
            ).fetchone()
            props = json.loads(row[0])
            old = props.get(m.property)
            new = m.value
            if old == new and type(old) is type(new):
                return None
            props[m.property] = new
            source_label = "invoke" if execution_id is not None else None
            self._conn.execute(
                "UPDATE objects SET properties = ?, version = version + 1, "
                "updated_at = ?, source = COALESCE(?, source) "
                "WHERE otype = ? AND oid = ?",
                (
                    _json(props),
                    ts,
                    source_label,
                    m.object_ref.type,
                    m.object_ref.id,
                ),
            )
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
            change = self._link(m.source, m.relation, m.target, ts, execution_id)
            return change
        if m.kind == MUTATION_UNLINK:
            cur = self._conn.execute(
                "DELETE FROM relations WHERE relation = ? AND src_type = ? "
                "AND src_id = ? AND tgt_type = ? AND tgt_id = ?",
                (m.relation, m.source.type, m.source.id, m.target.type, m.target.id),
            )
            if cur.rowcount == 0:
                return None
            return Change(
                kind=MUTATION_UNLINK,
                source=m.source,
                relation=m.relation,
                target=m.target,
                ts=ts,
                execution_id=execution_id,
            )
        raise ValueError(f"unknown mutation kind: {m.kind!r}")

    def _link(
        self,
        source: ObjectRef,
        relation: str,
        target: ObjectRef,
        ts: str | None = None,
        execution_id: str | None = None,
    ) -> Change | None:
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO relations VALUES (?, ?, ?, ?, ?)",
            (relation, source.type, source.id, target.type, target.id),
        )
        if cur.rowcount == 0:
            return None  # edge already existed; unique by definition
        return Change(
            kind=MUTATION_LINK,
            source=source,
            relation=relation,
            target=target,
            ts=ts,
            execution_id=execution_id,
        )


def _change_value_json(change: Change, field: str) -> str | None:
    """Serialize old/new for the change log; link changes have neither."""
    if change.kind == MUTATION_LINK or change.kind == MUTATION_UNLINK:
        return None
    if change.kind == MUTATION_CREATE_OBJECT:
        return _json(change.new) if field == "new" else None
    return _json(getattr(change, field))
