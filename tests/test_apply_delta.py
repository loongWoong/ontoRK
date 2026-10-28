"""Atomic delta ingestion tests (v0.2 part A: ``StateStore.apply_delta``).

The Projection -> Kernel write path is one call:

    apply_delta(objects, relations, mutations, execution_id=..., source=...)

Semantics under test:
    * missing object     -> create_object (validated like any create)
    * existing object    -> merge set_property (no-op values are skipped)
    * relation           -> idempotent link
    * mutations          -> applied after the upserts, in the same
                            transaction, so a function may write properties
                            of an object created by the same delta
    * validate-before-apply: any violation writes nothing
"""

from pathlib import Path

import pytest

from ontology_runtime.adapters.assembly import build_runtime_from_dicts
from ontology_runtime.core.errors import ObjectNotFound, SchemaViolation
from ontology_runtime.core.execution import Mutation
from ontology_runtime.core.refs import ObjectRef
from ontology_runtime.core.state import Object, Relation

SCHEMA = {
    "ontology": {"name": "shop"},
    "objects": {
        "Product": {
            "identity": ["name"],
            "properties": {
                "sku": {"type": "string", "required": True, "unique": True},
                "name": {"type": "string", "required": True},
                "price": {"type": "number", "minimum": 0},
            },
        },
        "Store": {
            "properties": {"name": {"type": "string", "required": True}},
        },
    },
    "relations": {
        "sells": {
            "source": "Store",
            "target": "Product",
            "cardinality": "many-to-many",
        },
    },
    "functions": {},
}

P1 = ObjectRef("Product", "P1")
P2 = ObjectRef("Product", "P2")
P3 = ObjectRef("Product", "P3")
S1 = ObjectRef("Store", "S1")

SEED = {
    "objects": [
        {"type": "Product", "id": "P1", "properties": {"sku": "A1", "name": "alpha", "price": 10}},
        {"type": "Store", "id": "S1", "properties": {"name": "shop"}},
    ],
    "relations": [],
}


@pytest.fixture()
def runtime():
    return build_runtime_from_dicts(SCHEMA, seed_data=SEED)


def _set(ref, prop, value):
    return Mutation(kind="set_property", object_ref=ref, property=prop, value=value)


# ----------------------------------------------------------------------
# upsert semantics
# ----------------------------------------------------------------------


def test_apply_delta_creates_missing_objects(runtime):
    changes = runtime.apply_delta(
        [Object(P2, {"sku": "B1", "name": "beta", "price": 20})], [], []
    )
    assert len(changes) == 1
    assert changes[0].kind == "create_object"
    assert changes[0].object_ref == P2
    assert changes[0].new == {"sku": "B1", "name": "beta", "price": 20}
    assert runtime.count_objects() == 3
    assert runtime.get(P2).properties["sku"] == "B1"


def test_apply_delta_merges_existing_objects(runtime):
    changes = runtime.apply_delta([Object(P1, {"price": 12})], [], [])
    assert len(changes) == 1
    change = changes[0]
    assert change.kind == "set_property"
    assert (change.old, change.new) == (10, 12)
    obj = runtime.get(P1)
    assert obj.properties == {"sku": "A1", "name": "alpha", "price": 12}
    assert obj.version == 2


def test_apply_delta_skips_noop_values(runtime):
    changes = runtime.apply_delta([Object(P1, {"price": 10})], [], [])
    assert changes == []
    assert runtime.get(P1).version == 1


def test_apply_delta_links_relations_idempotently(runtime):
    first = runtime.apply_delta([], [Relation(S1, "sells", P1)], [])
    assert [c.kind for c in first] == ["link"]
    assert runtime.related(S1, "sells") == [P1]

    again = runtime.apply_delta([], [Relation(S1, "sells", P1)], [])
    assert again == []
    assert runtime.related(S1, "sells") == [P1]


def test_apply_delta_mutations_see_objects_created_in_the_same_delta(runtime):
    changes = runtime.apply_delta(
        [Object(P2, {"sku": "B1", "name": "beta"})],
        [],
        [_set(P2, "price", 30)],
    )
    assert [c.kind for c in changes] == ["create_object", "set_property"]
    assert runtime.get(P2).properties["price"] == 30
    assert runtime.get(P2).version == 2


# ----------------------------------------------------------------------
# atomicity: validate everything before writing anything
# ----------------------------------------------------------------------


def test_apply_delta_is_atomic_on_invalid_object(runtime):
    good = Object(P2, {"sku": "B1", "name": "beta", "price": 20})
    bad = Object(P3, {"sku": "C1"})  # name is required
    with pytest.raises(SchemaViolation):
        runtime.apply_delta([good, bad], [], [])
    assert runtime.count_objects() == 2
    for ref in (P2, P3):
        with pytest.raises(ObjectNotFound):
            runtime.get(ref)


def test_apply_delta_is_atomic_on_missing_relation_endpoint(runtime):
    with pytest.raises(ObjectNotFound):
        runtime.apply_delta(
            [Object(P2, {"sku": "B1", "name": "beta"})],
            [Relation(S1, "sells", ObjectRef("Product", "GHOST"))],
            [],
        )
    assert runtime.count_objects() == 2
    with pytest.raises(ObjectNotFound):
        runtime.get(P2)
    assert runtime.related(S1, "sells") == []


def test_apply_delta_rejects_relation_endpoint_type_mismatch(runtime):
    with pytest.raises(SchemaViolation):
        runtime.apply_delta([], [Relation(S1, "sells", S1)], [])
    with pytest.raises(SchemaViolation):
        runtime.apply_delta([], [Relation(P1, "sells", P2)], [])


def test_apply_delta_rejects_unknown_object_type(runtime):
    with pytest.raises(SchemaViolation):
        runtime.apply_delta(
            [Object(ObjectRef("Ghost", "G1"), {"name": "x"})], [], []
        )


# ----------------------------------------------------------------------
# constraints apply to delta writes exactly like to invoke writes
# ----------------------------------------------------------------------


def test_apply_delta_enforces_unique(runtime):
    with pytest.raises(SchemaViolation):
        runtime.apply_delta(
            [Object(P2, {"sku": "A1", "name": "beta"})], [], []
        )
    assert runtime.count_objects() == 2


def test_apply_delta_enforces_unique_within_one_batch(runtime):
    with pytest.raises(SchemaViolation):
        runtime.apply_delta(
            [
                Object(P2, {"sku": "B1", "name": "beta"}),
                Object(P3, {"sku": "B1", "name": "gamma"}),
            ],
            [],
            [],
        )
    assert runtime.count_objects() == 2


def test_apply_delta_enforces_identity(runtime):
    with pytest.raises(SchemaViolation):
        runtime.apply_delta(
            [Object(P2, {"sku": "B1", "name": "alpha"})],  # identity duplicate
            [],
            [],
        )
    assert runtime.count_objects() == 2


def test_apply_delta_validates_property_values(runtime):
    with pytest.raises(SchemaViolation):
        runtime.apply_delta([Object(P1, {"price": -1})], [], [])
    with pytest.raises(SchemaViolation):
        runtime.apply_delta([], [], [_set(P1, "price", "expensive")])
    assert runtime.get(P1).properties["price"] == 10


# ----------------------------------------------------------------------
# metadata + change log
# ----------------------------------------------------------------------


def test_apply_delta_stamps_source_execution_and_changes(runtime):
    changes = runtime.apply_delta(
        [Object(P2, {"sku": "B1", "name": "beta", "price": 20})],
        [Relation(S1, "sells", P2)],
        [],
        execution_id="exec-projection-1",
        source="projection",
    )
    assert [c.kind for c in changes] == ["create_object", "link"]
    assert all(c.execution_id == "exec-projection-1" for c in changes)
    assert all(c.ts is not None and c.seq > 0 for c in changes)
    assert [c.seq for c in changes] == [1, 2]
    obj = runtime.get(P2)
    assert obj.source == "projection"
    assert obj.updated_at is not None
    assert [c.seq for c in runtime.store.list_changes()] == [1, 2]


def test_apply_delta_empty_is_a_noop(runtime):
    assert runtime.apply_delta([], [], []) == []
    assert runtime.store.list_changes() == []


# ----------------------------------------------------------------------
# seed validation door
# ----------------------------------------------------------------------


def test_validate_seed_checks_without_writing(runtime):
    runtime.validate_seed([Object(P2, {"sku": "B1", "name": "beta"})], [])
    assert runtime.count_objects() == 2
    with pytest.raises(SchemaViolation):
        runtime.validate_seed([Object(P2, {"sku": "B1"})], [])
    with pytest.raises(ObjectNotFound):
        runtime.validate_seed([], [Relation(S1, "sells", P3)])
    assert runtime.count_objects() == 2


def test_seed_rejects_invalid_data_without_writing(runtime):
    with pytest.raises(SchemaViolation):
        runtime.seed([Object(P2, {"sku": "B1"})], [])  # required name missing
    assert runtime.count_objects() == 2
    with pytest.raises(ObjectNotFound):
        runtime.seed([], [Relation(S1, "sells", P3)])
    assert runtime.related(S1, "sells") == []
