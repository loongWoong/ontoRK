"""SQLiteStateStore: the same Port over SQLite.

SQLite concepts never leak into Core; this file is the only place that
knows SQL exists.
"""

from __future__ import annotations

import json
import sqlite3
import threading

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
"""


class SQLiteStateStore:

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- seeding (not part of the Port) ----

    def seed(self, objects: list[Object], relations: list[Relation]) -> None:
        with self._lock, self._conn:
            for obj in objects:
                self._conn.execute(
                    "INSERT OR REPLACE INTO objects VALUES (?, ?, ?)",
                    (obj.ref.type, obj.ref.id, json.dumps(obj.properties, ensure_ascii=False)),
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
                "SELECT properties FROM objects WHERE otype = ? AND oid = ?",
                (ref.type, ref.id),
            ).fetchone()
        if row is None:
            return None
        return Object(ref, json.loads(row[0]))

    def find_objects(
        self, object_type: str, filters: dict[str, object]
    ) -> list[Object]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT oid, properties FROM objects WHERE otype = ?",
                (object_type,),
            ).fetchall()
        results = []
        for oid, props_json in rows:
            props = json.loads(props_json)
            if match_object(props, filters):
                results.append(Object(ObjectRef(object_type, oid), props))
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

    def apply(self, mutations: list[Mutation]) -> list[Change]:
        with self._lock:
            changes: list[Change] = []
            # validate state-level preconditions first (atomicity)
            for m in mutations:
                self._check(m)
            with self._conn:  # BEGIN ... COMMIT
                for m in mutations:
                    change = self._apply_one(m)
                    if change is not None:
                        changes.append(change)
            return changes

    # ---- internals ----

    def _check(self, m: Mutation) -> None:
        if m.kind == MUTATION_SET_PROPERTY:
            if self._exists(m.object_ref) is None:
                raise ObjectNotFound(f"object not found: {m.object_ref}")
        elif m.kind in (MUTATION_LINK, MUTATION_UNLINK):
            for ref in (m.source, m.target):
                if self._exists(ref) is None:
                    raise ObjectNotFound(f"object not found: {ref}")

    def _exists(self, ref: ObjectRef) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM objects WHERE otype = ? AND oid = ?",
            (ref.type, ref.id),
        ).fetchone()
        return row is not None

    def _apply_one(self, m: Mutation) -> Change | None:
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
            self._conn.execute(
                "UPDATE objects SET properties = ? WHERE otype = ? AND oid = ?",
                (json.dumps(props, ensure_ascii=False), m.object_ref.type, m.object_ref.id),
            )
            return Change(
                kind=MUTATION_SET_PROPERTY,
                object_ref=m.object_ref,
                property=m.property,
                old=old,
                new=new,
            )
        if m.kind == MUTATION_LINK:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO relations VALUES (?, ?, ?, ?, ?)",
                (m.relation, m.source.type, m.source.id, m.target.type, m.target.id),
            )
            if cur.rowcount == 0:
                return None  # edge already existed; unique by definition
            return Change(kind=MUTATION_LINK, source=m.source, relation=m.relation, target=m.target)
        if m.kind == MUTATION_UNLINK:
            cur = self._conn.execute(
                "DELETE FROM relations WHERE relation = ? AND src_type = ? "
                "AND src_id = ? AND tgt_type = ? AND tgt_id = ?",
                (m.relation, m.source.type, m.source.id, m.target.type, m.target.id),
            )
            if cur.rowcount == 0:
                return None
            return Change(kind=MUTATION_UNLINK, source=m.source, relation=m.relation, target=m.target)
        raise ValueError(f"unknown mutation kind: {m.kind!r}")
