"""CLI adapter.

    ontology describe
    ontology describe-object Vehicle
    ontology describe-function Vehicle.recharge
    ontology get Vehicle V001
    ontology find Vehicle --filter balance__gt=50
    ontology related Vehicle/V001 passes [--direction in]
    ontology invoke Vehicle.recharge --target Vehicle/V001 --arg amount=100
"""

from __future__ import annotations

import argparse
import json
import sys

from ..core.errors import KernelError
from ..core.refs import ObjectRef
from .assembly import build_runtime
from .serialize import error_to_dict, to_jsonable


def _parse_scalar(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _pairs(values: list[str] | None) -> dict:
    result = {}
    for item in values or []:
        if "=" not in item:
            raise SystemExit(f"expected key=value, got {item!r}")
        key, _, raw = item.partition("=")
        result[key] = _parse_scalar(raw)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ontology", description="Ontology Runtime Kernel")
    parser.add_argument("--schema", required=True, help="path to ontology YAML/JSON")
    parser.add_argument("--bindings", help="path to bindings YAML/JSON")
    parser.add_argument("--store", default="memory", choices=["memory", "sqlite"])
    parser.add_argument("--db", help="sqlite database path")
    parser.add_argument("--seed", help="path to seed YAML/JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("describe", help="index-level ontology description")

    p = sub.add_parser("describe-object")
    p.add_argument("name")
    p = sub.add_parser("describe-relation")
    p.add_argument("name")
    p = sub.add_parser("describe-function")
    p.add_argument("name")

    p = sub.add_parser("get")
    p.add_argument("type")
    p.add_argument("id")

    p = sub.add_parser("find")
    p.add_argument("type")
    p.add_argument("--filter", action="append", default=[])

    p = sub.add_parser("related")
    p.add_argument("ref", help="Type/Id")
    p.add_argument("relation")
    p.add_argument("--direction", default="out", choices=["out", "in"])

    p = sub.add_parser("invoke")
    p.add_argument("function")
    p.add_argument("--target", help="Type/Id")
    p.add_argument("--arg", action="append", default=[], help="key=value")
    p.add_argument("--arg-json", help="args as a JSON object")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime = build_runtime(
        schema_path=args.schema,
        bindings_path=args.bindings,
        store=args.store,
        db_path=args.db,
        seed_path=args.seed,
    )

    try:
        if args.command == "describe":
            result = runtime.describe()
        elif args.command == "describe-object":
            result = runtime.describe_object(args.name)
        elif args.command == "describe-relation":
            result = runtime.describe_relation(args.name)
        elif args.command == "describe-function":
            result = runtime.describe_function(args.name)
        elif args.command == "get":
            result = runtime.get(ObjectRef(args.type, args.id))
        elif args.command == "find":
            result = runtime.find(args.type, **_pairs(args.filter))
        elif args.command == "related":
            result = runtime.related(
                ObjectRef.parse(args.ref), args.relation, args.direction
            )
        elif args.command == "invoke":
            arg_values = _pairs(args.arg)
            if args.arg_json:
                arg_values.update(json.loads(args.arg_json))
            target = ObjectRef.parse(args.target) if args.target else None
            call_result = runtime.invoke(args.function, target=target, args=arg_values)
            if call_result.error is not None:
                print(json.dumps({"error": error_to_dict(call_result.error)},
                                 ensure_ascii=False), file=sys.stderr)
                return 1
            result = {
                "execution_id": call_result.execution_id,
                "output": to_jsonable(call_result.output),
                "changes": [to_jsonable(c) for c in call_result.changes],
                "error": None,
            }
        else:  # pragma: no cover
            raise SystemExit(f"unknown command: {args.command}")
    except KernelError as exc:
        print(json.dumps({"error": error_to_dict(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 1

    print(json.dumps(to_jsonable(result), ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> None:
    # Windows consoles often default to GBK; force UTF-8 for JSON output.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(run(argv))


if __name__ == "__main__":
    main()
