import json
from pathlib import Path

from ontology_runtime.adapters import cli

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "vehicle"
BASE = ["--schema", str(EXAMPLES / "ontology.yaml"),
        "--bindings", str(EXAMPLES / "bindings.yaml"),
        "--seed", str(EXAMPLES / "seed.json")]


def run_json(argv):
    """Run the CLI in-process, capture stdout as parsed JSON."""
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.run([*BASE, *argv])
    assert code == 0
    return json.loads(buffer.getvalue())


def test_cli_describe():
    data = run_json(["describe"])
    assert data["ontology"]["name"] == "traffic"
    assert any(o["name"] == "Vehicle" for o in data["objects"])


def test_cli_get():
    data = run_json(["get", "Vehicle", "V001"])
    assert data["properties"]["plate"] == "鲁A12345"


def test_cli_find():
    data = run_json(["find", "Vehicle", "--filter", "balance__gt=50"])
    assert len(data) == 1 and data[0]["id"] == "V001"


def test_cli_related():
    data = run_json(["related", "Vehicle/V001", "passes"])
    assert data == ["TollStation/ST001"]


def test_cli_invoke():
    data = run_json(["invoke", "Vehicle.recharge", "--target", "Vehicle/V001",
                     "--arg", "amount=100"])
    assert data["output"]["balance"] == 200
    assert data["changes"][0]["old"] == 100
    assert data["error"] is None


def test_cli_invoke_business_error(capsys):
    code = cli.run([*BASE, "invoke", "Vehicle.pass_station", "--target", "Vehicle/V002",
                    "--arg", "station=ST001", "--arg", "fee=999"])
    assert code == 1
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["error"]["type"] == "FunctionExecutionError"


def test_cli_error_unknown_object(capsys):
    # unknown object type in find raises InvalidArgument -> exit 1 path
    code = cli.run([*BASE, "find", "Ghost"])
    assert code == 1
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["error"]["type"] == "InvalidArgument"
