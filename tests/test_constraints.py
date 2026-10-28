"""Declarative constraint tests (v0.2 part B).

The schema *describes* constraints; the runtime *enforces* them on every
write.  This file pins both halves:

    * loader parses constraints into the canonical model
    * illegal declarations are rejected at load time (SchemaViolation)
    * runtime rejects violating writes - through ``apply_delta`` and
      through ``invoke`` - without leaving partial state behind
    * violations surface through the normal error channels (SchemaViolation,
      ObjectNotFound), never as a silent coercion
"""

import copy
from pathlib import Path

import pytest

from ontology_runtime.adapters.assembly import build_runtime_from_dicts
from ontology_runtime.core.errors import InvalidArgument, ObjectNotFound, SchemaViolation
from ontology_runtime.core.execution import FunctionResult, Mutation
from ontology_runtime.core.refs import ObjectRef
from ontology_runtime.core.state import Object, Relation
from ontology_runtime.loader import parse_schema

SCHEMA = {
    "ontology": {
        "name": "constraints",
        "version": 3,
        "profile": "strict",
        "schema_version": 2,
    },
    "objects": {
        "Product": {
            "description": "商品",
            "category": "entity",
            "identity": ["brand", "name"],
            "properties": {
                "sku": {"type": "string", "required": True, "unique": True},
                "name": {"type": "string", "required": True},
                "brand": {"type": "string", "required": True},
                "status": {
                    "type": "string",
                    "enum": ["active", "retired"],
                    "default": "active",
                },
                "channel": {"type": "string", "enum": ["online", "offline"]},
                "price": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 10000,
                    "unit": "CNY",
                    "precision": 2,
                },
                "quantity": {"type": "integer", "minimum": 0},
                "money": {"type": "object", "unit": "CNY"},
                "tags": {"type": "array"},
                "active": {"type": "boolean", "default": True},
                "created_at": {"type": "string", "temporal": True},
            },
            "state_machine": {
                "property": "status",
                "initial": "active",
                "states": ["active", "retired"],
                "transitions": {"active": ["retired"], "retired": []},
            },
        },
        "Shop": {"properties": {"name": {"type": "string", "required": True}}},
        "User": {"properties": {"name": {"type": "string"}}},
    },
    "relations": {
        "sells": {
            "source": "Shop",
            "target": "Product",
            "cardinality": "one-to-many",
            "inverse": "sold_by",
        },
        "sold_by": {
            "source": "Product",
            "target": "Shop",
            "cardinality": "many-to-one",
            "inverse": "sells",
        },
        "manages": {
            "source": "Shop",
            "target": "User",
            "cardinality": "one-to-one",
            "inverse": "managed_by",
        },
        "managed_by": {
            "source": "User",
            "target": "Shop",
            "cardinality": "one-to-one",
            "inverse": "manages",
        },
        "views": {
            "source": "User",
            "target": "Product",
            "cardinality": "many-to-many",
            "inverse": "viewed_by",
        },
        "viewed_by": {
            "source": "Product",
            "target": "User",
            "cardinality": "many-to-many",
            "inverse": "views",
        },
    },
    "functions": {
        "Product.update": {"target": "Product", "description": "写属性", "input": {}},
    },
}

SEED = {
    "objects": [
        {
            "type": "Product",
            "id": "P1",
            "properties": {
                "sku": "A1",
                "name": "alpha",
                "brand": "X",
                "price": 10,
                "quantity": 5,
                "channel": "online",
            },
        },
        {
            "type": "Product",
            "id": "P2",
            "properties": {
                "sku": "B1",
                "name": "beta",
                "brand": "Y",
                "price": 20,
                "quantity": 1,
                "channel": "offline",
            },
        },
        {"type": "Shop", "id": "S1", "properties": {"name": "shop-1"}},
        {"type": "Shop", "id": "S2", "properties": {"name": "shop-2"}},
        {"type": "User", "id": "U1", "properties": {"name": "u1"}},
        {"type": "User", "id": "U2", "properties": {"name": "u2"}},
    ],
    "relations": [],
}

P1 = ObjectRef("Product", "P1")
P2 = ObjectRef("Product", "P2")
S1 = ObjectRef("Shop", "S1")
S2 = ObjectRef("Shop", "S2")
U1 = ObjectRef("User", "U1")
U2 = ObjectRef("User", "U2")


@pytest.fixture()
def runtime():
    return build_runtime_from_dicts(SCHEMA, seed_data=SEED)


class BufferExecutor:
    """Test executor: runs a python callback against the execution context
    and returns a clean result, exactly like PythonFunctionExecutor."""

    def __init__(self, fn):
        self._fn = fn

    def execute(self, call, context):
        self._fn(context)
        return FunctionResult(context.execution_id, None)


def _invoke_set(runtime, prop, value, ref=P1):
    runtime.executor = BufferExecutor(lambda ctx: ctx.set(ref, prop, value))
    return runtime.invoke("Product.update", target=ref, args={})


# ----------------------------------------------------------------------
# loader: constraints land in the canonical model
# ----------------------------------------------------------------------


def test_loader_parses_constraints(runtime):
    product = runtime.schema.objects["Product"]
    assert product.identity == ("brand", "name")
    assert product.category == "entity"
    assert product.state_machine == {
        "property": "status",
        "initial": "active",
        "states": ("active", "retired"),
        "transitions": {"active": ("retired",), "retired": ()},
    }

    sku = product.properties["sku"]
    assert (sku.type.kind, sku.required, sku.unique) == ("string", True, True)

    price = product.properties["price"]
    assert (price.minimum, price.maximum) == (0, 10000)
    assert (price.unit, price.precision) == ("CNY", 2)

    status = product.properties["status"]
    assert status.enum == ("active", "retired")
    assert status.default == "active"

    assert product.properties["created_at"].temporal is True

    sells = runtime.schema.relations["sells"]
    assert (sells.cardinality, sells.inverse) == ("one-to-many", "sold_by")
    assert runtime.schema.profile == "strict"
    assert runtime.schema.schema_version == 2


def test_describe_object_exposes_constraints(runtime):
    described = runtime.describe_object("Product")
    assert described["identity"] == ["brand", "name"]
    assert described["category"] == "entity"
    price = described["properties"]["price"]
    assert price["type"] == "number"
    assert price["minimum"] == 0 and price["maximum"] == 10000
    assert price["unit"] == "CNY" and price["precision"] == 2
    assert described["properties"]["sku"]["required"] is True
    assert described["properties"]["sku"]["unique"] is True
    assert described["properties"]["status"]["enum"] == ["active", "retired"]
    assert described["properties"]["status"]["default"] == "active"
    assert described["properties"]["created_at"]["temporal"] is True
    state_machine = described["state_machine"]
    assert state_machine["property"] == "status"
    assert state_machine["initial"] == "active"
    assert state_machine["transitions"]["active"] == ["retired"]


def test_describe_keeps_index_shape_and_describe_relation_adds_inverse(runtime):
    index = runtime.describe()
    assert index["ontology"]["profile"] == "strict"
    assert index["ontology"]["schema_version"] == 2
    assert {"name": "Product", "description": "商品"} in index["objects"]
    sells = [r for r in index["relations"] if r["name"] == "sells"][0]
    assert sells == {
        "name": "sells",
        "source": "Shop",
        "target": "Product",
        "cardinality": "one-to-many",
    }
    assert runtime.describe_relation("sells")["inverse"] == "sold_by"


def test_loader_accepts_platform_meta_model_spellings():
    """Platform compiles ``state_machine.field`` and temporal *mode* strings;
    the kernel treats them as the canonical model."""
    schema, _ = parse_schema(
        {
            "objects": {
                "Task": {
                    "properties": {
                        "state": {"type": "string"},
                        "occurred_at": {"type": "string", "temporal": "valid-time"},
                    },
                    "state_machine": {
                        "field": "state",
                        "transitions": {"todo": ["done"], "done": []},
                    },
                }
            },
            "relations": {},
            "functions": {},
        }
    )
    task = schema.objects["Task"]
    assert task.state_machine["property"] == "state"
    assert task.state_machine["transitions"]["todo"] == ("done",)
    assert task.properties["occurred_at"].temporal == "valid-time"


# ----------------------------------------------------------------------
# loader: illegal declarations are rejected at load time
# ----------------------------------------------------------------------


def _base_schema():
    return copy.deepcopy(SCHEMA)


def _set_property(schema, object_name, prop, field, value):
    schema["objects"][object_name]["properties"][prop][field] = value
    return schema


BAD_SCHEMAS = {
    "bad_cardinality": lambda s: s["relations"]["sells"].update(cardinality="1-n"),
    "inverse_missing": lambda s: s["relations"]["sells"].update(inverse="ghost"),
    "inverse_not_swapped": lambda s: s["relations"]["sold_by"].update(
        source="Shop", target="Shop"
    ),
    "identity_property_undefined": lambda s: s["objects"]["Product"].update(
        identity=["ghost"]
    ),
    "identity_duplicate": lambda s: s["objects"]["Product"].update(
        identity=["name", "name"]
    ),
    "enum_type_mismatch": lambda s: _set_property(
        s, "Product", "quantity", "enum", ["many"]
    ),
    "minimum_above_maximum": lambda s: _set_property(
        s, "Product", "price", "minimum", 20000
    ),
    "precision_negative": lambda s: _set_property(
        s, "Product", "price", "precision", -1
    ),
    "required_not_bool": lambda s: _set_property(s, "Product", "sku", "required", "yes"),
    "temporal_not_bool": lambda s: _set_property(
        s, "Product", "created_at", "temporal", 5
    ),
    "unit_not_string": lambda s: _set_property(s, "Product", "price", "unit", 5),
    "state_machine_without_property": lambda s: s["objects"]["Product"].update(
        state_machine={"states": ["active"]}
    ),
    "state_machine_property_undefined": lambda s: s["objects"]["Product"].update(
        state_machine={
            "property": "ghost",
            "states": ["active"],
            "transitions": {"active": ["active"]},
        }
    ),
    "state_machine_undeclared_state": lambda s: s["objects"]["Product"].update(
        state_machine={
            "property": "status",
            "states": ["active"],
            "transitions": {"active": ["ghost"]},
        }
    ),
    "state_machine_initial_unknown": lambda s: s["objects"]["Product"].update(
        state_machine={
            "property": "status",
            "initial": "ghost",
            "states": ["active"],
        }
    ),
}


@pytest.mark.parametrize("label", sorted(BAD_SCHEMAS))
def test_illegal_schema_declarations_raise(label):
    schema = _base_schema()
    mutated = BAD_SCHEMAS[label](schema)
    with pytest.raises(SchemaViolation):
        parse_schema(mutated if mutated is not None else schema)


# ----------------------------------------------------------------------
# runtime: every write is validated  (apply_delta path)
# ----------------------------------------------------------------------


def test_required_enforced_on_create(runtime):
    with pytest.raises(SchemaViolation):
        runtime.apply_delta([Object(ObjectRef("Product", "P3"), {"sku": "C1"})], [], [])


def test_create_uses_defaults_and_initial_state(runtime):
    runtime.apply_delta(
        [Object(ObjectRef("Product", "P3"), {"sku": "C1", "name": "gamma", "brand": "Z"})],
        [],
        [],
    )
    props = runtime.get(ObjectRef("Product", "P3")).properties
    assert props["status"] == "active"
    assert props["active"] is True


def test_create_must_start_in_initial_state(runtime):
    with pytest.raises(SchemaViolation):
        runtime.apply_delta(
            [
                Object(
                    ObjectRef("Product", "P3"),
                    {"sku": "C1", "name": "gamma", "brand": "Z", "status": "retired"},
                )
            ],
            [],
            [],
        )


def test_type_enum_range_and_unit_are_enforced(runtime):
    violations = [
        {"price": "free"},  # type
        {"quantity": 1.5},  # integer type
        {"channel": "mall"},  # enum
        {"price": -1},  # minimum
        {"price": 10001},  # maximum
        {"price": 9.999},  # precision
        {"quantity": -2},  # integer minimum
        {"money": {"amount": 10, "unit": "USD"}},  # unit
    ]
    for properties in violations:
        with pytest.raises(SchemaViolation):
            runtime.apply_delta([Object(P1, properties)], [], [])
    props = runtime.get(P1).properties
    assert props["price"] == 10 and props["channel"] == "online"
    assert "money" not in props


def test_valid_money_and_precision_pass(runtime):
    runtime.apply_delta(
        [
            Object(
                P1,
                {
                    "money": {"amount": 10, "unit": "CNY"},
                    "price": 9.99,
                    "created_at": "2024-01-01T00:00:00+00:00",
                },
            )
        ],
        [],
        [],
    )
    props = runtime.get(P1).properties
    assert props["money"] == {"amount": 10, "unit": "CNY"}
    assert props["price"] == 9.99


def test_unique_is_enforced_on_create_and_update(runtime):
    with pytest.raises(SchemaViolation):
        runtime.apply_delta(
            [Object(ObjectRef("Product", "P3"), {"sku": "A1", "name": "g", "brand": "Z"})],
            [],
            [],
        )
    with pytest.raises(SchemaViolation):
        runtime.apply_delta([], [], [Mutation(
            kind="set_property", object_ref=P2, property="sku", value="A1"
        )])


def test_identity_is_enforced(runtime):
    with pytest.raises(SchemaViolation):
        runtime.apply_delta(
            [Object(ObjectRef("Product", "P3"), {"sku": "C1", "name": "alpha", "brand": "X"})],
            [],
            [],
        )
    # an update that only conflicts after two mutations are combined is
    # caught by validating the whole buffer against the resulting state
    with pytest.raises(SchemaViolation):
        runtime.apply_delta(
            [],
            [],
            [
                Mutation(kind="set_property", object_ref=P2, property="brand", value="X"),
                Mutation(kind="set_property", object_ref=P2, property="name", value="alpha"),
            ],
        )
    assert runtime.get(P2).properties["brand"] == "Y"
    assert runtime.count_objects() == 6


def test_state_machine_rejects_illegal_transition(runtime):
    runtime.apply_delta(
        [], [], [Mutation(kind="set_property", object_ref=P1, property="status", value="retired")]
    )
    assert runtime.get(P1).properties["status"] == "retired"
    with pytest.raises(SchemaViolation):
        runtime.apply_delta(
            [], [], [Mutation(kind="set_property", object_ref=P1, property="status", value="active")]
        )
    assert runtime.get(P1).properties["status"] == "retired"


# ----------------------------------------------------------------------
# runtime: constraints on the invoke path (same validation, atomic)
# ----------------------------------------------------------------------


def test_invoke_rejects_type_violation(runtime):
    result = _invoke_set(runtime, "price", "free")
    assert isinstance(result.error, SchemaViolation)
    assert result.status == "failed"
    assert result.changes == []
    assert runtime.get(P1).properties["price"] == 10


def test_invoke_rejects_range_enum_and_precision(runtime):
    for prop, value in (
        ("price", -5),
        ("price", 50000),
        ("price", 1.234),
        ("channel", "mall"),
    ):
        result = _invoke_set(runtime, prop, value)
        assert isinstance(result.error, SchemaViolation), (prop, value)
        assert result.status == "failed"
        assert result.changes == []
    assert runtime.get(P1).properties["price"] == 10


def test_invoke_rejects_unique_conflict(runtime):
    result = _invoke_set(runtime, "sku", "B1", ref=P1)  # P2 already uses B1
    assert isinstance(result.error, SchemaViolation)
    assert result.status == "failed"


def test_invoke_state_machine_allows_declared_transition(runtime):
    result = _invoke_set(runtime, "status", "retired")
    assert result.error is None
    assert result.status == "succeeded"
    assert [c.new for c in result.changes] == ["retired"]


def test_invoke_state_machine_rejects_undeclared_transition(runtime):
    _invoke_set(runtime, "status", "retired")
    result = _invoke_set(runtime, "status", "active")
    assert isinstance(result.error, SchemaViolation)
    assert result.status == "failed"
    assert runtime.get(P1).properties["status"] == "retired"


# ----------------------------------------------------------------------
# cardinality (interpretation: the declared end allows at most one edge)
# ----------------------------------------------------------------------


def test_one_to_one_rejects_second_edge_on_either_end(runtime):
    runtime.apply_delta([], [Relation(S1, "manages", U1)], [])
    with pytest.raises(SchemaViolation):
        runtime.apply_delta([], [Relation(S2, "manages", U1)], [])  # target end
    with pytest.raises(SchemaViolation):
        runtime.apply_delta([], [Relation(S1, "manages", U2)], [])  # source end
    assert runtime.related(S1, "manages") == [U1]


def test_one_to_many_rejects_second_edge_at_target_end(runtime):
    # ERD reading: one Shop sells many Products.  The source end is free, and
    # that holds inside one atomic delta too (toll: Station ->has_lane-> Lane)
    runtime.apply_delta(
        [], [Relation(S1, "sells", P1), Relation(S1, "sells", P2)], []
    )
    assert runtime.related(S1, "sells") == [P1, P2]
    # ... while a Product can be sold by at most one Shop (one incoming edge);
    # a delta that would give P1 a second seller is rejected atomically
    with pytest.raises(SchemaViolation):
        runtime.apply_delta(
            [], [Relation(S1, "sells", P1), Relation(S2, "sells", P1)], []
        )
    with pytest.raises(SchemaViolation):
        runtime.apply_delta([], [Relation(S2, "sells", P1)], [])
    assert runtime.related(P1, "sells", "in") == [S1]
    assert runtime.related(S2, "sells") == []


def test_many_to_one_rejects_second_edge_at_source_end(runtime):
    # ERD reading: many Products are sold by one Shop.  The target end is free,
    # including within one atomic delta ...
    runtime.apply_delta(
        [], [Relation(P1, "sold_by", S1), Relation(P2, "sold_by", S1)], []
    )
    assert runtime.related(S1, "sold_by", "in") == [P1, P2]
    # ... while a Product can point at most one Shop (one outgoing edge)
    with pytest.raises(SchemaViolation):
        runtime.apply_delta(
            [], [Relation(P1, "sold_by", S1), Relation(P1, "sold_by", S2)], []
        )
    with pytest.raises(SchemaViolation):
        runtime.apply_delta([], [Relation(P1, "sold_by", S2)], [])
    assert runtime.related(P1, "sold_by") == [S1]
    assert runtime.related(P2, "sold_by") == [S1]


def test_many_to_many_is_unconstrained(runtime):
    runtime.apply_delta(
        [],
        [
            Relation(U1, "views", P1),
            Relation(U2, "views", P1),
            Relation(U1, "views", P2),
        ],
        [],
    )
    assert runtime.related(U1, "views") == [P1, P2]
    assert runtime.related(U2, "views") == [P1]


def test_cardinality_in_one_invoke_buffer_is_rejected_atomically(runtime):
    def link_twice(ctx):
        # Product -sold_by-> Shop is many-to-one: one outgoing edge per source
        ctx.link(P1, "sold_by", S1)
        ctx.link(P1, "sold_by", S2)

    runtime.executor = BufferExecutor(link_twice)
    result = runtime.invoke("Product.update", target=P1, args={})
    assert isinstance(result.error, SchemaViolation)
    assert result.status == "failed"
    assert runtime.related(P1, "sold_by") == []  # nothing was applied


# ----------------------------------------------------------------------
# precision / seed edge cases
# ----------------------------------------------------------------------


def test_precision_tolerates_float_representation_noise(runtime):
    result = _invoke_set(runtime, "price", 0.1 + 0.2)
    assert result.error is None, result.error
    assert abs(runtime.get(P1).properties["price"] - 0.3) < 1e-12


def test_seed_inserts_only_missing_objects(runtime):
    # P1 exists: seed must neither write nor validate (it is skipped)
    runtime.seed([Object(P1, {"price": -5})], [])
    assert runtime.get(P1).properties["price"] == 10
    # a genuinely new object with invalid data is still rejected
    with pytest.raises(SchemaViolation):
        runtime.seed([Object(ObjectRef("Product", "P9"), {"sku": "Z9"})], [])
    assert runtime.store.get_object(ObjectRef("Product", "P9")) is None


def test_state_machine_without_initial_allows_first_assignment():
    """Platform state machines declare ``field`` + transitions only; the
    first write may pick any declared state, later writes are constrained."""
    schema = {
        "objects": {
            "Task": {
                "properties": {"state": {"type": "string"}},
                "state_machine": {
                    "field": "state",
                    "transitions": {"todo": ["done"], "done": []},
                },
            }
        },
        "relations": {},
        "functions": {"Task.update": {"target": "Task", "input": {}}},
    }
    runtime = build_runtime_from_dicts(
        schema, seed_data={"objects": [{"type": "Task", "id": "T1", "properties": {}}]}
    )
    task = ObjectRef("Task", "T1")
    runtime.apply_delta(
        [], [], [Mutation(kind="set_property", object_ref=task, property="state", value="todo")]
    )
    runtime.apply_delta(
        [], [], [Mutation(kind="set_property", object_ref=task, property="state", value="done")]
    )
    assert runtime.get(task).properties["state"] == "done"
    with pytest.raises(SchemaViolation):
        runtime.apply_delta(
            [], [], [Mutation(kind="set_property", object_ref=task, property="state", value="todo")]
        )


def test_validate_seed_writes_nothing(runtime):
    shop = ObjectRef("Shop", "S9")
    runtime.validate_seed(
        [Object(shop, {"name": "shop-9"})],
        [Relation(shop, "sells", P1)],
    )
    assert runtime.store.get_object(shop) is None


def test_apply_delta_validates_updates_and_mutations_together(runtime):
    # the batch changes P2.sku and the mutation gives P1 the same sku: the
    # combined delta is atomic, so the conflict must be detected up front
    with pytest.raises(SchemaViolation):
        runtime.apply_delta(
            [Object(P2, {"sku": "Z9"})],
            [],
            [Mutation(kind="set_property", object_ref=P1, property="sku", value="Z9")],
        )
    assert runtime.get(P1).properties["sku"] == "A1"
    assert runtime.get(P2).properties["sku"] == "B1"


# ----------------------------------------------------------------------
# find: open-world observability (missing != not matching)
# ----------------------------------------------------------------------


def test_find_reports_excluded_missing():
    schema = {
        "objects": {"T": {"properties": {"status": {"type": "string"}}}},
        "relations": {},
        "functions": {},
    }
    runtime = build_runtime_from_dicts(
        schema,
        seed_data={
            "objects": [
                {"type": "T", "id": "A", "properties": {"status": "free"}},
                {"type": "T", "id": "B", "properties": {}},  # no status
                {"type": "T", "id": "C", "properties": {"status": "busy"}},
            ]
        },
    )
    reported = runtime.find("T", status="free", report=True)
    assert [obj.ref.id for obj in reported["objects"]] == ["A"]
    # B lacks the filter property; C merely has a different value: only B is
    # "missing", never C
    assert reported["excluded_missing"] == 1

    plain = runtime.find("T", status="free")
    assert isinstance(plain, list)
    assert [obj.ref.id for obj in plain] == ["A"]

    # no filters: nothing can be excluded for a missing property
    everything = runtime.find("T", report=True)
    assert [obj.ref.id for obj in everything["objects"]] == ["A", "B", "C"]
    assert everything["excluded_missing"] == 0
