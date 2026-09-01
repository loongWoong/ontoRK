"""HTTP adapter (stdlib only).

    GET  /describe
    GET  /describe/object/{type}
    GET  /describe/relation/{name}
    GET  /describe/function/{name}
    GET  /objects/{type}/{id}
    GET  /objects/{type}?balance__gt=100          (find)
    GET  /objects/{type}/{id}/related/{relation}?direction=out
    POST /invoke   {"function": "...", "target": {"type","id"}|null, "args": {}}
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..core.errors import (
    FunctionNotFound,
    InvalidArgument,
    KernelError,
    ObjectNotFound,
    RelationNotFound,
)
from ..core.refs import ObjectRef
from .assembly import OntologyRuntime, build_runtime
from .serialize import error_to_dict, to_jsonable

_STATUS = {
    ObjectNotFound: 404,
    RelationNotFound: 404,
    FunctionNotFound: 404,
    InvalidArgument: 400,
}


def _parse_scalar(text: str):
    """Query-string values arrive as text; coerce to JSON scalars when possible."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def build_handler(runtime: OntologyRuntime) -> type[BaseHTTPRequestHandler]:
    class OntologyHTTPHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter tests
            pass

        # browsers are a first-class access path; allow cross-origin demos
        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _send(self, status: int, payload) -> None:
            body = json.dumps(to_jsonable(payload), ensure_ascii=False, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()

        def _error(self, exc: Exception) -> None:
            status = _STATUS.get(type(exc), 400) if isinstance(exc, KernelError) else 500
            self._send(status, {"error": error_to_dict(exc)})

        # ---- GET ----

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            query = {
                k: _parse_scalar(v[-1]) for k, v in parse_qs(parsed.query).items()
            }
            try:
                if parts == ["describe"]:
                    return self._send(200, runtime.describe())
                if len(parts) == 3 and parts[0] == "describe":
                    kind, name = parts[1], parts[2]
                    if kind == "object":
                        return self._send(200, runtime.describe_object(name))
                    if kind == "relation":
                        return self._send(200, runtime.describe_relation(name))
                    if kind == "function":
                        return self._send(200, runtime.describe_function(name))
                    raise InvalidArgument(f"unknown describe kind: {kind}")
                if len(parts) == 3 and parts[0] == "objects":
                    return self._send(200, runtime.get(ObjectRef(parts[1], parts[2])))
                if len(parts) == 2 and parts[0] == "objects":
                    return self._send(200, runtime.find(parts[1], **query))
                if len(parts) == 5 and parts[0] == "objects" and parts[3] == "related":
                    direction = query.get("direction", "out")
                    refs = runtime.related(ObjectRef(parts[1], parts[2]), parts[4], direction)
                    return self._send(
                        200,
                        [
                            to_jsonable(runtime.get(ref))
                            for ref in refs
                        ],
                    )
                raise InvalidArgument(f"no route for {self.path}")
            except KernelError as exc:
                return self._error(exc)

        # ---- POST /invoke ----

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path != "/invoke":
                    raise InvalidArgument(f"no route for {self.path}")
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                target = None
                if body.get("target"):
                    target = ObjectRef(body["target"]["type"], body["target"]["id"])
                result = runtime.invoke(
                    function=body["function"], target=target, args=body.get("args", {})
                )
                payload = {
                    "execution_id": result.execution_id,
                    "output": to_jsonable(result.output),
                    "changes": [to_jsonable(c) for c in result.changes],
                    "error": error_to_dict(result.error) if result.error else None,
                }
                status = 200
                if result.error is not None:
                    status = _STATUS.get(type(result.error), 422)
                return self._send(status, payload)
            except KernelError as exc:
                return self._error(exc)
            except (KeyError, json.JSONDecodeError) as exc:
                return self._error(InvalidArgument(f"invalid request body: {exc}"))

    return OntologyHTTPHandler


def serve(
    schema_path: str,
    bindings_path: str | None = None,
    store: str = "memory",
    db_path: str | None = None,
    seed_path: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    runtime = build_runtime(
        schema_path=schema_path,
        bindings_path=bindings_path,
        store=store,
        db_path=db_path,
        seed_path=seed_path,
    )
    server = ThreadingHTTPServer((host, port), build_handler(runtime))
    print(f"ontology http adapter on http://{host}:{port}")
    server.serve_forever()


def main() -> None:  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(prog="ontology-http")
    parser.add_argument("--schema", required=True)
    parser.add_argument("--bindings")
    parser.add_argument("--store", default="memory", choices=["memory", "sqlite"])
    parser.add_argument("--db")
    parser.add_argument("--seed")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    serve(
        schema_path=args.schema,
        bindings_path=args.bindings,
        store=args.store,
        db_path=args.db,
        seed_path=args.seed,
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
