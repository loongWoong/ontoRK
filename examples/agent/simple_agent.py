"""A minimal deterministic agent on top of the runtime.

No LLM required - this proves the agent loop shape:

    Discover -> Observe -> Traverse -> Act -> Observe

The agent only uses the five public APIs; it never touches the store.

Run:  python examples/agent/simple_agent.py
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

VEHICLE_DIR = EXAMPLE_DIR.parent / "vehicle"


def say(step: str, value) -> None:
    print(f"\n[{step}]")
    print(json.dumps(to_jsonable(value), ensure_ascii=False, indent=2, default=str))


class SimpleAgent:
    """A tiny policy-driven agent: recharge a vehicle, then verify."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def run(self, vehicle_id: str, amount: float) -> None:
        # 1. Discover: understand the world through describe()
        index = self.runtime.describe()
        say("Discover", index)
        functions = {f["name"] for f in index["functions"]}
        assert "Vehicle.recharge" in functions, "capability missing"

        # 2. Observe: look at the target object
        before = self.runtime.get(ObjectRef("Vehicle", vehicle_id))
        say("Observe (before)", before)

        # 3. Traverse: check what we already know about the vehicle
        related = self.runtime.related(ObjectRef("Vehicle", vehicle_id), "passes")
        say("Traverse (passes)", related)

        # 4. Act: invoke the business capability
        result = self.runtime.invoke(
            "Vehicle.recharge",
            target=ObjectRef("Vehicle", vehicle_id),
            args={"amount": amount},
        )
        say("Act (invoke)", result)
        if result.error is not None:
            print("agent stops: invocation failed")
            return

        # 5. Observe: verify the world actually changed
        after = self.runtime.get(ObjectRef("Vehicle", vehicle_id))
        say("Observe (after)", after)
        expected = before.get("balance") + amount
        assert after.get("balance") == expected, "state change not visible"
        print("\nAgent loop closed: Discover -> Observe -> Traverse -> Act -> Observe")


def main():
    runtime = build_runtime(
        schema_path=str(VEHICLE_DIR / "ontology.yaml"),
        bindings_path=str(VEHICLE_DIR / "bindings.yaml"),
        seed_path=str(VEHICLE_DIR / "seed.json"),
    )
    SimpleAgent(runtime).run(vehicle_id="V002", amount=80)


if __name__ == "__main__":
    main()
