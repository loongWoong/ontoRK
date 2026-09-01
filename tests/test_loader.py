import pytest

from ontology_runtime.core.errors import SchemaViolation
from ontology_runtime.loader import parse_schema, parse_seed


def test_parse_minimal_ontology():
    schema, catalog = parse_schema(
        {
            "ontology": {"name": "traffic", "version": 1},
            "objects": {
                "Vehicle": {
                    "description": "车辆",
                    "properties": {"balance": {"type": "number"}},
                },
                "TollStation": {"properties": {"name": {"type": "string"}}},
            },
            "relations": {"passes": {"source": "Vehicle", "target": "TollStation"}},
            "functions": {
                "Vehicle.recharge": {
                    "target": "Vehicle",
                    "description": "充值",
                    "input": {"amount": {"type": "number"}},
                }
            },
        }
    )
    assert schema.name == "traffic"
    assert schema.objects["Vehicle"].properties["balance"].type.kind == "number"
    assert schema.relations["passes"].source == "Vehicle"
    assert catalog.functions["Vehicle.recharge"].inputs["amount"].kind == "number"


def test_shorthand_property_types():
    schema, _ = parse_schema(
        {"objects": {"Vehicle": {"properties": {"plate": "string"}}}}
    )
    assert schema.objects["Vehicle"].properties["plate"].type.kind == "string"


def test_relation_source_must_exist():
    with pytest.raises(SchemaViolation, match="source"):
        parse_schema(
            {
                "objects": {"Vehicle": {"properties": {}}},
                "relations": {"passes": {"source": "Ghost", "target": "Vehicle"}},
            }
        )


def test_function_target_must_exist():
    with pytest.raises(SchemaViolation, match="target"):
        parse_schema(
            {
                "objects": {"Vehicle": {"properties": {}}},
                "functions": {"Ghost.do": {"target": "Ghost", "description": ""}},
            }
        )


def test_function_name_must_match_target():
    with pytest.raises(SchemaViolation, match="name should be"):
        parse_schema(
            {
                "objects": {"Vehicle": {"properties": {}}},
                "functions": {"Station.do": {"target": "Vehicle", "description": ""}},
            }
        )


def test_seed_parsing():
    objects, relations = parse_seed(
        {
            "objects": [
                {"type": "Vehicle", "id": "V001", "properties": {"balance": 100}}
            ],
            "relations": [
                {
                    "source": "Vehicle/V001",
                    "relation": "passes",
                    "target": "TollStation/ST001",
                }
            ],
        }
    )
    assert objects[0].ref.type == "Vehicle"
    assert relations[0].source.id == "V001"
