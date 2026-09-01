import pytest

from ontology_runtime.core.errors import InvalidArgument
from ontology_runtime.core.refs import ObjectRef


def test_parse_and_str():
    ref = ObjectRef.parse("Vehicle/V001")
    assert ref == ObjectRef("Vehicle", "V001")
    assert str(ref) == "Vehicle/V001"


def test_ids_are_not_global():
    # same id, different types -> different objects
    assert ObjectRef("Vehicle", "V001") != ObjectRef("Station", "V001")
    assert hash(ObjectRef("Vehicle", "V001")) != hash(ObjectRef("Station", "V001"))


def test_id_must_not_contain_slash():
    # 'Type/Id' must stay unambiguous, so ids never contain '/'
    with pytest.raises(InvalidArgument):
        ObjectRef("Vehicle", "a/b")
    with pytest.raises(InvalidArgument):
        ObjectRef.parse("Vehicle/a/b")


@pytest.mark.parametrize("bad", ["V001", "", "Vehicle/", "/V001"])
def test_invalid_references(bad):
    with pytest.raises(InvalidArgument):
        ObjectRef.parse(bad)
