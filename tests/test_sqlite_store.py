import pytest

from ontology_runtime.core.execution import Mutation
from ontology_runtime.core.refs import ObjectRef
from ontology_runtime.core.state import Object, Relation
from ontology_runtime.infrastructure.sqlite import SQLiteStateStore


@pytest.fixture()
def store(tmp_path):
    s = SQLiteStateStore(str(tmp_path / "state.db"))
    s.seed(
        [
            Object(ObjectRef("Vehicle", "V001"), {"balance": 100}),
            Object(ObjectRef("TollStation", "ST001"), {"name": "济南西"}),
        ],
        [Relation(ObjectRef("Vehicle", "V001"), "passes", ObjectRef("TollStation", "ST001"))],
    )
    return s


def test_persistence_across_reopen(tmp_path):
    db = str(tmp_path / "state.db")
    s1 = SQLiteStateStore(db)
    s1.seed([Object(ObjectRef("Vehicle", "V001"), {"balance": 100})], [])
    s1.apply([Mutation(kind="set_property", object_ref=ObjectRef("Vehicle", "V001"),
                       property="balance", value=250)])

    s2 = SQLiteStateStore(db)
    assert s2.get_object(ObjectRef("Vehicle", "V001")).properties["balance"] == 250


def test_set_property_and_changes(store):
    changes = store.apply([Mutation(kind="set_property", object_ref=ObjectRef("Vehicle", "V001"),
                                    property="balance", value=180)])
    assert changes[0].old == 100 and changes[0].new == 180


def test_link_unlink_and_related(store):
    changes = store.apply([Mutation(kind="link", source=ObjectRef("Vehicle", "V001"),
                                    relation="passes", target=ObjectRef("TollStation", "ST001"))])
    assert changes == []  # idempotent, edge exists

    changes = store.apply([Mutation(kind="unlink", source=ObjectRef("Vehicle", "V001"),
                                    relation="passes", target=ObjectRef("TollStation", "ST001"))])
    assert len(changes) == 1
    assert store.related(ObjectRef("Vehicle", "V001"), "passes", "out") == []
    assert store.related(ObjectRef("TollStation", "ST001"), "passes", "in") == []


def test_find_filters(store):
    assert store.find_objects("Vehicle", {"balance__gt": 50})[0].ref.id == "V001"
