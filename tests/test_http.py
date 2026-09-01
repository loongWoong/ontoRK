import json
import threading
import urllib.request
from pathlib import Path

import pytest

from ontology_runtime.adapters import http as http_adapter

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "vehicle"


@pytest.fixture(scope="module")
def base_url():
    runtime = http_adapter.build_runtime(
        schema_path=str(EXAMPLES / "ontology.yaml"),
        bindings_path=str(EXAMPLES / "bindings.yaml"),
        seed_path=str(EXAMPLES / "seed.json"),
    )
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), http_adapter.build_handler(runtime))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def get(base_url, path):
    with urllib.request.urlopen(base_url + path) as res:
        return res.status, json.loads(res.read())


def post(base_url, path, payload):
    req = urllib.request.Request(
        base_url + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_describe(base_url):
    status, data = get(base_url, "/describe")
    assert status == 200
    assert data["ontology"]["name"] == "traffic"


def test_get_and_find(base_url):
    status, obj = get(base_url, "/objects/Vehicle/V001")
    assert status == 200 and obj["properties"]["balance"] == 100
    status, found = get(base_url, "/objects/Vehicle?balance__gt=50")
    assert status == 200 and len(found) == 1


def test_related_hydrates_objects(base_url):
    status, related = get(base_url, "/objects/Vehicle/V001/related/passes")
    assert status == 200
    assert related[0]["properties"]["name"] == "济南西"


def test_invoke_roundtrip(base_url):
    status, data = post(base_url, "/invoke", {
        "function": "Vehicle.recharge",
        "target": {"type": "Vehicle", "id": "V001"},
        "args": {"amount": 100},
    })
    assert status == 200
    assert data["output"]["balance"] == 200
    assert data["changes"][0]["new"] == 200


def test_invoke_business_error_maps_to_4xx(base_url):
    status, data = post(base_url, "/invoke", {
        "function": "Vehicle.pass_station",
        "target": {"type": "Vehicle", "id": "V002"},
        "args": {"station": "ST001", "fee": 999},
    })
    assert status == 422
    assert data["error"]["type"] == "FunctionExecutionError"


def test_unknown_object_maps_to_404(base_url):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(base_url, "/objects/Vehicle/GHOST")
    assert exc.value.code == 404
