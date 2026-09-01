"""Vehicle/TollStation demo: the full ontology runtime loop.

    Discover -> Observe -> Traverse -> Act -> Observe

Run:  python examples/vehicle/demo.py
"""

import json
import sys
from pathlib import Path

# Windows consoles often default to GBK; force UTF-8 so Chinese output
# survives pipes and redirects.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

EXAMPLE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EXAMPLE_DIR.parents[1] / "src"))

from ontology_runtime.adapters.assembly import build_runtime  # noqa: E402
from ontology_runtime.adapters.serialize import to_jsonable  # noqa: E402
from ontology_runtime.core.refs import ObjectRef  # noqa: E402


def show(title, value):
    print(f"\n=== {title} " + "=" * max(0, 50 - len(title)))
    print(json.dumps(to_jsonable(value), ensure_ascii=False, indent=2, default=str))


def main():
    runtime = build_runtime(
        schema_path=str(EXAMPLE_DIR / "ontology.yaml"),
        bindings_path=str(EXAMPLE_DIR / "bindings.yaml"),
        seed_path=str(EXAMPLE_DIR / "seed.json"),
    )

    # 1. Discover
    show("describe (index)", runtime.describe())
    show("describe_object Vehicle", runtime.describe_object("Vehicle"))
    show("describe_function Vehicle.pass_station", runtime.describe_function("Vehicle.pass_station"))

    # 2. Observe
    show("get Vehicle/V001", runtime.get(ObjectRef("Vehicle", "V001")))

    # 3. Traverse
    show(
        "related Vehicle/V001 passes",
        [runtime.get(r) for r in runtime.related(ObjectRef("Vehicle", "V001"), "passes")],
    )
    show("find Vehicle balance__gt=50", runtime.find("Vehicle", balance__gt=50))

    # 4. Act: recharge 100
    result = runtime.invoke(
        "Vehicle.recharge",
        target=ObjectRef("Vehicle", "V001"),
        args={"amount": 100},
    )
    show("invoke Vehicle.recharge(amount=100)", result)

    # 5. Act: pass a station (two mutations, one atomic unit)
    result = runtime.invoke(
        "Vehicle.pass_station",
        target=ObjectRef("Vehicle", "V001"),
        args={"station": "ST002", "fee": 30},
    )
    show("invoke Vehicle.pass_station(ST002, fee=30)", result)

    # 6. Business failure: insufficient balance -> error, state untouched
    result = runtime.invoke(
        "Vehicle.pass_station",
        target=ObjectRef("Vehicle", "V002"),
        args={"station": "ST001", "fee": 999},
    )
    show("invoke pass_station with insufficient balance (error)", result)
    show("Vehicle/V002 unchanged", runtime.get(ObjectRef("Vehicle", "V002")))

    # 7. Observe the new world
    show(
        "related Vehicle/V001 passes (after)",
        [runtime.get(r) for r in runtime.related(ObjectRef("Vehicle", "V001"), "passes")],
    )


if __name__ == "__main__":
    main()
