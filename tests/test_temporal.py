"""Temporal state tests (v0.2 part A).

State is not a timeless snapshot any more:

* objects carry ``version`` / ``updated_at`` / ``source``
* every applied change is logged with a continuous ``seq`` plus ``ts`` /
  ``execution_id``
* ``FunctionResult.status`` tells "clean failure" apart from "unknown
  outcome" (e.g. timeout)
* the change log never disagrees with state (same transaction), survives a
  restart, and continues its sequence
* seeding never overwrites live state (v0.9 fix)
"""

import sqlite3
from pathlib import Path

import pytest

from ontology_runtime.adapters.assembly import build_runtime, build_runtime_from_dicts
from ontology_runtime.core.errors import InvalidArgument
from ontology_runtime.core.execution import Change, FunctionResult, Mutation
from ontology_runtime.core.refs import ObjectRef
from ontology_runtime.core.state import Object, Relation
from ontology_runtime.infrastructure.memory import MemoryStateStore
from ontology_runtime.infrastructure.sqlite import SQLiteStateStore

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "vehicle"


def _set(ref, prop, value):
    return Mutation(kind="set_property", object_ref=ref, property=prop, value=value)


@pytest.fixture()
def runtime():
    return build_runtime(
        schema_path=str(EXAMPLES / "ontology.yaml"),
        bindings_path=str(EXAMPLES / "bindings.yaml"),
        seed_path=str(EXAMPLES / "seed.json"),
    )


# ----------------------------------------------------------------------
# canonical model: v0.1 positional construction stays valid
# ----------------------------------------------------------------------


def test_object_and_change_stay_positionally_compatible():
    obj = Object(ObjectRef("Vehicle", "V001"), {"balance": 1})
    assert (obj.version, obj.updated_at, obj.source) == (1, None, None)
    change = Change("set_property", ObjectRef("Vehicle", "V001"), "balance", 1, 2)
    assert (change.seq, change.ts, change.execution_id) == (0, None, None)


def test_function_result_status_is_validated():
    assert FunctionResult("exec-1").status == "succeeded"
    for status in ("succeeded", "failed", "unknown"):
        assert FunctionResult("exec-1", status=status).status == status
    with pytest.raises(InvalidArgument):
        FunctionResult("exec-1", status="bogus")


# ----------------------------------------------------------------------
# serialization: metadata is exposed, wire shape stays backward compatible
# ----------------------------------------------------------------------


def test_serialize_exposes_object_metadata():
    from ontology_runtime.adapters.serialize import to_jsonable

    payload = to_jsonable(
        Object(
            ObjectRef("Vehicle", "V001"),
            {"balance": 1},
            version=3,
            updated_at="2024-01-01T00:00:00+00:00",
            source="invoke",
        )
    )
    assert payload == {
        "type": "Vehicle",
        "id": "V001",
        "properties": {"balance": 1},
        "version": 3,
        "updated_at": "2024-01-01T00:00:00+00:00",
        "source": "invoke",
    }


def test_serialize_keeps_change_wire_shape_stable():
    """Existing consumers assert set_property payloads exactly; seq/ts/
    execution_id stay internal to the kernel change log."""
    from ontology_runtime.adapters.serialize import to_jsonable

    payload = to_jsonable(
        Change(
            "set_property",
            ObjectRef("Vehicle", "V001"),
            "balance",
            10,
            30,
            seq=7,
            ts="2024-01-01T00:00:00+00:00",
            execution_id="exec-abc",
        )
    )
    assert payload == {
        "kind": "set_property",
        "object": "Vehicle/V001",
        "property": "balance",
        "old": 10,
        "new": 30,
    }
    created = to_jsonable(
        Change("create_object", ObjectRef("Vehicle", "V001"), new={"balance": 1})
    )
    assert created == {
        "kind": "create_object",
        "object": "Vehicle/V001",
        "properties": {"balance": 1},
    }


# ----------------------------------------------------------------------
# memory store: metadata + change log
# ----------------------------------------------------------------------


@pytest.fixture()
def memory_store():
    store = MemoryStateStore()
    store.seed(
        [
            Object(ObjectRef("Vehicle", "V001"), {"balance": 100}),
            Object(ObjectRef("TollStation", "ST001"), {"name": "济南西"}),
        ],
        [Relation(ObjectRef("Vehicle", "V001"), "passes", ObjectRef("TollStation", "ST001"))],
    )
    return store


def test_memory_apply_bumps_version_and_stamps_change(memory_store):
    changes = memory_store.apply(
        [_set(ObjectRef("Vehicle", "V001"), "balance", 250)],
        execution_id="exec-abc",
    )
    assert len(changes) == 1
    change = changes[0]
    assert change.seq == 1
    assert change.ts is not None and change.ts.endswith("+00:00")
    assert change.execution_id == "exec-abc"

    obj = memory_store.get_object(ObjectRef("Vehicle", "V001"))
    assert obj.version == 2
    assert obj.updated_at == change.ts
    assert obj.source == "invoke"


def test_memory_link_does_not_bump_object_version(memory_store):
    before = memory_store.get_object(ObjectRef("Vehicle", "V001"))
    store_changes = memory_store.apply(
        [
            Mutation(
                kind="link",
                source=ObjectRef("Vehicle", "V001"),
                relation="passes",
                target=ObjectRef("TollStation", "ST001"),
            )
        ]
    )
    assert store_changes == []  # edge already exists -> no change, no log entry
    after = memory_store.get_object(ObjectRef("Vehicle", "V001"))
    assert after.version == before.version
    assert memory_store.list_changes() == []


def test_memory_noop_write_is_not_logged(memory_store):
    changes = memory_store.apply([_set(ObjectRef("Vehicle", "V001"), "balance", 100)])
    assert changes == []
    assert memory_store.list_changes() == []
    assert memory_store.get_object(ObjectRef("Vehicle", "V001")).version == 1


def test_memory_change_seq_is_continuous_and_filterable(memory_store):
    memory_store.apply([_set(ObjectRef("Vehicle", "V001"), "balance", 10)])
    memory_store.apply([_set(ObjectRef("Vehicle", "V001"), "balance", 20)])
    changes = memory_store.list_changes()
    assert [c.seq for c in changes] == [1, 2]
    assert [c.new for c in memory_store.list_changes(after_seq=1)] == [20]
    assert memory_store.count_objects() == 2


def test_memory_seed_does_not_overwrite_existing_state(memory_store):
    memory_store.apply([_set(ObjectRef("Vehicle", "V001"), "balance", 999)])
    # a "restart" replays the same seed: the live value must win
    memory_store.seed(
        [Object(ObjectRef("Vehicle", "V001"), {"balance": 100})], []
    )
    assert memory_store.get_object(ObjectRef("Vehicle", "V001")).properties["balance"] == 999


def test_store_seed_keeps_given_metadata():
    store = MemoryStateStore()
    store.seed(
        [
            Object(
                ObjectRef("Vehicle", "V001"),
                {"balance": 1},
                version=7,
                updated_at="2024-01-01T00:00:00+00:00",
                source="fixture",
            )
        ],
        [],
    )
    obj = store.get_object(ObjectRef("Vehicle", "V001"))
    assert (obj.version, obj.updated_at, obj.source) == (
        7,
        "2024-01-01T00:00:00+00:00",
        "fixture",
    )


# ----------------------------------------------------------------------
# sqlite store: persistence, migration, continuing sequence
# ----------------------------------------------------------------------


def test_sqlite_changes_and_sequence_survive_restart(tmp_path):
    db = str(tmp_path / "state.db")
    first = SQLiteStateStore(db)
    first.seed([Object(ObjectRef("Vehicle", "V001"), {"balance": 100})], [])
    changes = first.apply([_set(ObjectRef("Vehicle", "V001"), "balance", 150)])
    assert changes[0].seq == 1

    second = SQLiteStateStore(db)
    logged = second.list_changes()
    assert [c.seq for c in logged] == [1]
    assert logged[0].new == 150
    assert logged[0].kind == "set_property"
    assert logged[0].object_ref == ObjectRef("Vehicle", "V001")

    changes = second.apply([_set(ObjectRef("Vehicle", "V001"), "balance", 200)])
    assert changes[0].seq == 2  # sequence continues, never reuses a number
    reloaded = SQLiteStateStore(db)
    assert [c.seq for c in reloaded.list_changes()] == [1, 2]
    obj = reloaded.get_object(ObjectRef("Vehicle", "V001"))
    assert obj.version == 3  # seed + two applied changes
    assert obj.updated_at is not None


def test_sqlite_link_and_unlink_are_logged(tmp_path):
    store = SQLiteStateStore(str(tmp_path / "state.db"))
    store.seed(
        [
            Object(ObjectRef("Vehicle", "V001"), {"balance": 1}),
            Object(ObjectRef("TollStation", "ST001"), {"name": "站"}),
        ],
        [],
    )
    store.apply(
        [
            Mutation(
                kind="link",
                source=ObjectRef("Vehicle", "V001"),
                relation="passes",
                target=ObjectRef("TollStation", "ST001"),
            )
        ]
    )
    store.apply(
        [
            Mutation(
                kind="unlink",
                source=ObjectRef("Vehicle", "V001"),
                relation="passes",
                target=ObjectRef("TollStation", "ST001"),
            )
        ]
    )
    kinds = [c.kind for c in store.list_changes()]
    assert kinds == ["link", "unlink"]


def test_sqlite_migrates_a_v01_database(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE objects (
            otype TEXT NOT NULL,
            oid   TEXT NOT NULL,
            properties TEXT NOT NULL,
            PRIMARY KEY (otype, oid)
        );
        CREATE TABLE relations (
            relation TEXT NOT NULL,
            src_type TEXT NOT NULL,
            src_id   TEXT NOT NULL,
            tgt_type TEXT NOT NULL,
            tgt_id   TEXT NOT NULL,
            PRIMARY KEY (relation, src_type, src_id, tgt_type, tgt_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO objects VALUES ('Vehicle', 'V001', '{\"balance\": 100}')"
    )
    conn.commit()
    conn.close()

    store = SQLiteStateStore(str(db))  # upgrades in place, no data loss
    obj = store.get_object(ObjectRef("Vehicle", "V001"))
    assert obj.properties == {"balance": 100}
    assert (obj.version, obj.updated_at, obj.source) == (1, None, None)
    # the new change log works on the upgraded database
    changes = store.apply([_set(ObjectRef("Vehicle", "V001"), "balance", 120)])
    assert changes[0].seq == 1
    assert store.get_object(ObjectRef("Vehicle", "V001")).version == 2


def test_sqlite_seed_does_not_overwrite(tmp_path):
    db = str(tmp_path / "state.db")
    store = SQLiteStateStore(db)
    store.seed([Object(ObjectRef("Vehicle", "V001"), {"balance": 100})], [])
    store.apply([_set(ObjectRef("Vehicle", "V001"), "balance", 500)])
    store.seed([Object(ObjectRef("Vehicle", "V001"), {"balance": 100})], [])
    assert store.get_object(ObjectRef("Vehicle", "V001")).properties["balance"] == 500


# ----------------------------------------------------------------------
# runtime: changes produced by invoke carry execution identity + status
# ----------------------------------------------------------------------


def test_invoke_stamps_changes_and_object_metadata(runtime):
    result = runtime.invoke(
        "Vehicle.recharge",
        target=ObjectRef("Vehicle", "V001"),
        args={"amount": 50},
    )
    assert result.error is None
    assert result.status == "succeeded"
    change = result.changes[0]
    assert change.seq >= 1
    assert change.ts is not None
    assert change.execution_id == result.execution_id
    assert change.execution_id.startswith("exec-")

    obj = runtime.get(ObjectRef("Vehicle", "V001"))
    assert obj.version == 2  # seeded at version 1, one applied change
    assert obj.updated_at == change.ts
    assert obj.source == "invoke"

    logged = runtime.store.list_changes()
    assert logged[-1].seq == change.seq
    assert logged[-1].execution_id == result.execution_id


def test_invoke_noop_produces_no_change_and_no_log_entry(runtime):
    result = runtime.invoke(
        "TollStation.set_status",
        target=ObjectRef("TollStation", "ST001"),
        args={"status": "open"},  # already open
    )
    assert result.error is None
    assert result.changes == []
    assert runtime.store.list_changes() == []
    assert runtime.get(ObjectRef("TollStation", "ST001")).version == 1


def test_invoke_business_error_reports_failed_status(runtime):
    result = runtime.invoke(
        "Vehicle.pass_station",
        target=ObjectRef("Vehicle", "V002"),
        args={"station": "ST001", "fee": 999},
    )
    assert result.error is not None
    assert result.status == "failed"
    assert result.changes == []
    assert runtime.store.list_changes() == []


def test_invoke_timeout_reports_unknown_status(runtime):
    class TimeoutExecutor:
        def execute(self, call, context):
            raise TimeoutError("script exceeded its deadline")

    runtime.executor = TimeoutExecutor()
    result = runtime.invoke(
        "Vehicle.recharge", target=ObjectRef("Vehicle", "V001"), args={"amount": 1}
    )
    assert isinstance(result.error, TimeoutError)
    assert result.status == "unknown"
    assert result.changes == []
    # state untouched: a timed-out function never commits
    assert runtime.get(ObjectRef("Vehicle", "V001")).properties["balance"] == 100


def test_invoke_executor_error_result_is_failed(runtime):
    class FailedExecutor:
        def execute(self, call, context):
            context.set(ObjectRef("Vehicle", "V001"), "balance", 999)
            return FunctionResult(context.execution_id, None, [], RuntimeError("boom"))

    runtime.executor = FailedExecutor()
    result = runtime.invoke(
        "Vehicle.recharge", target=ObjectRef("Vehicle", "V001"), args={"amount": 1}
    )
    assert result.status == "failed"
    assert result.error is not None
    assert runtime.get(ObjectRef("Vehicle", "V001")).properties["balance"] == 100


# ----------------------------------------------------------------------
# v0.9: assembly seeds only an empty store
# ----------------------------------------------------------------------


def test_assembly_does_not_reseed_a_non_empty_sqlite_store(tmp_path):
    db = str(tmp_path / "state.db")
    kwargs = dict(
        schema_path=str(EXAMPLES / "ontology.yaml"),
        bindings_path=str(EXAMPLES / "bindings.yaml"),
        seed_path=str(EXAMPLES / "seed.json"),
        store="sqlite",
        db_path=db,
    )
    first = build_runtime(**kwargs)
    first.invoke(
        "Vehicle.recharge", target=ObjectRef("Vehicle", "V001"), args={"amount": 400}
    )
    assert first.get(ObjectRef("Vehicle", "V001")).properties["balance"] == 500

    second = build_runtime(**kwargs)  # restart with the same seed file
    assert second.get(ObjectRef("Vehicle", "V001")).properties["balance"] == 500
    assert second.count_objects() == 4


def test_runtime_seed_does_not_overwrite_live_state():
    schema_data = {
        "objects": {"Item": {"properties": {"name": {"type": "string"}}}},
        "relations": {},
        "functions": {},
    }
    runtime = build_runtime_from_dicts(schema_data)
    runtime.seed([Object(ObjectRef("Item", "I1"), {"name": "first"})], [])
    runtime.seed([Object(ObjectRef("Item", "I1"), {"name": "second"})], [])
    assert runtime.get(ObjectRef("Item", "I1")).properties["name"] == "first"
