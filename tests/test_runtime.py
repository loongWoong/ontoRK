import pytest

from ontology_runtime.adapters.assembly import build_runtime
from ontology_runtime.core.errors import (
    FunctionNotFound,
    InvalidArgument,
    ObjectNotFound,
    RelationNotFound,
    SchemaViolation,
)
from ontology_runtime.core.refs import ObjectRef
from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "vehicle"


@pytest.fixture()
def runtime():
    return build_runtime(
        schema_path=str(EXAMPLES / "ontology.yaml"),
        bindings_path=str(EXAMPLES / "bindings.yaml"),
        seed_path=str(EXAMPLES / "seed.json"),
    )


# ----------------------------------------------------------------------
# read APIs
# ----------------------------------------------------------------------


def test_describe_is_index_level(runtime):
    d = runtime.describe()
    assert {"name": "Vehicle", "description": "车辆"} in d["objects"]
    assert {"name": "passes", "source": "Vehicle", "target": "TollStation",
            "cardinality": None} in d["relations"]
    fn = [f for f in d["functions"] if f["name"] == "Vehicle.recharge"][0]
    assert "input" not in fn  # index level only


def test_describe_object_details(runtime):
    d = runtime.describe_object("Vehicle")
    assert d["properties"]["balance"]["type"] == "number"
    assert "Vehicle.recharge" in d["functions"]
    assert "passes" in d["relations_out"]


def test_get_unknown_object(runtime):
    with pytest.raises(ObjectNotFound):
        runtime.get(ObjectRef("Vehicle", "GHOST"))


def test_find_validates_type_and_filters(runtime):
    with pytest.raises(InvalidArgument):
        runtime.find("Ghost")
    with pytest.raises(InvalidArgument):
        runtime.find("Vehicle", nope__gt=1)
    assert len(runtime.find("Vehicle", balance__gt=50)) == 1


def test_related_validates(runtime):
    with pytest.raises(RelationNotFound):
        runtime.related(ObjectRef("Vehicle", "V001"), "owns")
    with pytest.raises(InvalidArgument):
        runtime.related(ObjectRef("Vehicle", "V001"), "passes", "sideways")


# ----------------------------------------------------------------------
# invoke pipeline
# ----------------------------------------------------------------------


def test_invoke_recharge_produces_output_and_changes(runtime):
    result = runtime.invoke(
        "Vehicle.recharge", target=ObjectRef("Vehicle", "V001"), args={"amount": 100}
    )
    assert result.error is None
    assert result.output == {"success": True, "balance": 200}
    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.kind == "set_property"
    assert change.object_ref == ObjectRef("Vehicle", "V001")
    assert (change.old, change.new) == (100, 200)
    assert result.execution_id.startswith("exec-")


def test_invoke_pass_station_links_and_sets(runtime):
    result = runtime.invoke(
        "Vehicle.pass_station",
        target=ObjectRef("Vehicle", "V001"),
        args={"station": "ST002", "fee": 30},
    )
    assert result.error is None
    kinds = [c.kind for c in result.changes]
    assert kinds == ["set_property", "link"]
    refs = runtime.related(ObjectRef("Vehicle", "V001"), "passes")
    assert ObjectRef("TollStation", "ST002") in refs


def test_business_error_is_atomic(runtime):
    before = runtime.get(ObjectRef("Vehicle", "V002")).properties["balance"]
    result = runtime.invoke(
        "Vehicle.pass_station",
        target=ObjectRef("Vehicle", "V002"),
        args={"station": "ST001", "fee": 999},
    )
    assert result.error is not None
    assert "insufficient balance" in str(result.error)
    assert result.changes == []
    # state untouched: no partial apply
    assert runtime.get(ObjectRef("Vehicle", "V002")).properties["balance"] == before
    assert runtime.related(ObjectRef("Vehicle", "V002"), "passes") == []


def test_invoke_validates_function_and_args(runtime):
    with pytest.raises(FunctionNotFound):
        runtime.invoke("Vehicle.fly")
    with pytest.raises(InvalidArgument):
        runtime.invoke("Vehicle.recharge", target=ObjectRef("Vehicle", "V001"), args={})
    with pytest.raises(InvalidArgument):
        runtime.invoke("Vehicle.recharge", target=ObjectRef("Vehicle", "V001"),
                       args={"amount": "many"})
    with pytest.raises(InvalidArgument):
        runtime.invoke("Vehicle.recharge", target=ObjectRef("Vehicle", "V001"),
                       args={"amount": 1, "extra": 2})
    with pytest.raises(InvalidArgument):
        runtime.invoke("Vehicle.recharge", target=ObjectRef("TollStation", "ST001"),
                       args={"amount": 1})
    with pytest.raises(ObjectNotFound):
        runtime.invoke("Vehicle.recharge", target=ObjectRef("Vehicle", "GHOST"),
                       args={"amount": 1})


def test_mutation_schema_validation(runtime):
    # a function writing an unknown property yields a SchemaViolation in
    # result.error, and nothing is applied
    from ontology_runtime.core.execution import FunctionResult

    def bad_fn(ctx):
        ctx.set(ObjectRef("Vehicle", "V001"), "ghost_prop", 1)
        return None

    class EchoExecutor:
        def execute(self, call, context):
            bad_fn(context)
            return FunctionResult(context.execution_id, None, context.apply_mutations(), None)

    runtime.executor = EchoExecutor()
    result = runtime.invoke("Vehicle.recharge", target=ObjectRef("Vehicle", "V001"),
                            args={"amount": 1})
    assert isinstance(result.error, SchemaViolation)
    assert "ghost_prop" in str(result.error)
    assert result.changes == []
    assert runtime.get(ObjectRef("Vehicle", "V001")).properties["balance"] == 100


def test_kernel_error_from_function_becomes_result_error(runtime):
    from ontology_runtime.core.execution import FunctionResult

    class ThrowingExecutor:
        def execute(self, call, context):
            raise ObjectNotFound("boom")

    runtime.executor = ThrowingExecutor()
    result = runtime.invoke("Vehicle.recharge", target=ObjectRef("Vehicle", "V001"),
                            args={"amount": 1})
    assert isinstance(result.error, ObjectNotFound)
