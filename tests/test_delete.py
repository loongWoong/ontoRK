"""State deletion: projection deletion propagation (v0.2, plan 1.6)."""

from __future__ import annotations

from ontology_runtime.adapters.serialize import to_jsonable
from ontology_runtime.core.execution import (
    MUTATION_DELETE_OBJECT,
    MUTATION_UNLINK,
)
from ontology_runtime.core.refs import ObjectRef
from ontology_runtime.core.state import Object, Relation
from ontology_runtime.infrastructure.memory import MemoryStateStore
from ontology_runtime.infrastructure.sqlite import SQLiteStateStore

V1 = ObjectRef("Vehicle", "V001")
V2 = ObjectRef("Vehicle", "V002")
S1 = ObjectRef("TollStation", "S001")


def _seed(store):
    store.seed(
        [Object(V1, {"plate": "A"}), Object(V2, {"plate": "B"}),
         Object(S1, {"name": "station"})],
        [Relation(source=V1, relation="passes", target=S1),
         Relation(source=V2, relation="passes", target=S1)],
    )


def _assert_deleted(store):
    _seed(store)
    changes = store.delete_objects([S1, S1, ObjectRef("Vehicle", "nope")])
    kinds = [c.kind for c in changes]
    assert kinds.count(MUTATION_UNLINK) == 2
    assert kinds.count(MUTATION_DELETE_OBJECT) == 1
    assert store.get_object(S1) is None
    # unrelated objects survive
    assert store.get_object(V1) is not None
    # relations are gone in both directions
    assert store.related(V1, "passes", "out") == []
    # idempotent: deleting again yields no changes
    assert store.delete_objects([S1]) == []
    # changes are logged with a continuous seq and carry the last properties
    logged = store.list_changes()
    delete_change = [c for c in logged if c.kind == MUTATION_DELETE_OBJECT][0]
    assert delete_change.object_ref == S1
    assert delete_change.old == {"name": "station"}
    assert delete_change.seq > 0


def test_memory_store_deletes_objects_and_relations():
    _assert_deleted(MemoryStateStore())


def test_sqlite_store_deletes_objects_and_relations(tmp_path):
    _assert_deleted(SQLiteStateStore(str(tmp_path / "state.db")))


def test_serialized_delete_change_exposes_last_properties():
    store = MemoryStateStore()
    _seed(store)
    changes = store.delete_objects([S1])
    payload = to_jsonable([c for c in changes
                           if c.kind == MUTATION_DELETE_OBJECT][0])
    assert payload["kind"] == "delete_object"
    assert payload["object"] == "TollStation/S001"
    assert payload["properties"] == {"name": "station"}
