import pytest

from ontology_runtime.core.errors import SchemaViolation
from ontology_runtime.core.types import TypeDef


@pytest.mark.parametrize(
    "kind,value,ok",
    [
        ("string", "a", True),
        ("string", 1, False),
        ("number", 1, True),
        ("number", 1.5, True),
        ("number", True, False),  # bool is not a number
        ("integer", 3, True),
        ("integer", 3.5, False),
        ("integer", True, False),
        ("boolean", True, True),
        ("boolean", 1, False),
        ("object", {"a": 1}, True),
        ("object", [1], False),
        ("array", [1], True),
        ("array", {"a": 1}, False),
        ("datetime", "2024-01-01T00:00:00", True),
        ("datetime", "not-a-date", False),
        ("datetime", 123, False),
    ],
)
def test_type_checks(kind, value, ok):
    assert TypeDef(kind).check(value) is ok


def test_unknown_kind_rejected():
    with pytest.raises(SchemaViolation):
        TypeDef("float128")
