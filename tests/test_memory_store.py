import pytest

from ontology_runtime.core.errors import ObjectNotFound
from ontology_runtime.core.execution import Mutation
from ontology_runtime.core.refs import ObjectRef
from ontology_runtime.core.state import Object, Relation
from ontology_runtime.infrastructure.memory import MemoryStateStore


@pytest.fixture()
def store():
    s = MemoryStateStore()
    s.seed(
        [
            Object(ObjectRef("Vehicle", "V001"), {"balance": 100}),
            Object(ObjectRef("Vehicle", "V002"), {"balance": 20}),
            Object(ObjectRef("TollStation", "ST001"), {"name": "济南西"}),
        ],
        [Relation(ObjectRef("Vehicle", "V001"), "passes", ObjectRef("TollStation", "ST001"))],
    )
    return s


def test_get_object_returns_copy(store):
    obj = store.get_object(ObjectRef("Vehicle", "V001"))
    obj.properties["balance"] = 99999  # mutating the copy must not corrupt state
    assert store.get_object(ObjectRef("Vehicle", "V001")).properties["balance"] == 100


def test_find_with_filters(store):
    assert len(store.find_objects("Vehicle", {})) == 2
    assert [o.ref.id for o in store.find_objects("Vehicle", {"balance__gt": 50})] == ["V001"]
    assert [o.ref.id for o in store.find_objects("Vehicle", {"balance__lte": 20})] == ["V002"]


def test_related_directions(store):
    out = store.related(ObjectRef("Vehicle", "V001"), "passes", "out")
    assert out == [ObjectRef("TollStation", "ST001")]
    back = store.related(ObjectRef("TollStation", "ST001"), "passes", "in")
    assert back == [ObjectRef("Vehicle", "V001")]


def test_apply_set_property_emits_change(store):
    changes = store.apply(
        [Mutation(kind="set_property", object_ref=ObjectRef("Vehicle", "V001"),
                  property="balance", value=200)]
    )
    assert len(changes) == 1
    assert changes[0].old == 100 and changes[0].new == 200
    assert store.get_object(ObjectRef("Vehicle", "V001")).properties["balance"] == 200


def test_apply_missing_object_raises_and_rolls_back(store):
    before = store.get_object(ObjectRef("Vehicle", "V001")).properties["balance"]
    mutations = [
        Mutation(kind="set_property", object_ref=ObjectRef("Vehicle", "V001"),
                 property="balance", value=1),
        Mutation(kind="set_property", object_ref=ObjectRef("Vehicle", "GHOST"),
                 property="balance", value=2),
    ]
    with pytest.raises(ObjectNotFound):
        store.apply(mutations)
    # nothing was applied
    assert store.get_object(ObjectRef("Vehicle", "V001")).properties["balance"] == before


def test_link_is_idempotent(store):
    edge = Relation(ObjectRef("Vehicle", "V001"), "passes", ObjectRef("TollStation", "ST001"))
    first = store.apply([Mutation(kind="link", source=edge.source,
                                  relation="passes", target=edge.target)])
    second = store.apply([Mutation(kind="link", source=edge.source,
                                   relation="passes", target=edge.target)])
    assert len(first) == 0  # edge already exists -> no change
    assert len(second) == 0
    assert len(store.related(ObjectRef("Vehicle", "V001"), "passes", "out")) == 1


def test_unlink_is_idempotent(store):
    changes = store.apply([Mutation(kind="unlink", source=ObjectRef("Vehicle", "V001"),
                                    relation="passes", target=ObjectRef("TollStation", "ST001"))])
    assert len(changes) == 1
    again = store.apply([Mutation(kind="unlink", source=ObjectRef("Vehicle", "V001"),
                                  relation="passes", target=ObjectRef("TollStation", "ST001"))])
    assert again == []
