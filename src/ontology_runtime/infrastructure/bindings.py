"""Function Binding: implementation binding, kept out of the domain model.

    Vehicle.recharge:
      executor: python
      entrypoint: functions/recharge.py

Future executors (builtin / http / wasm / remote) plug in here without
touching Core.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FunctionBinding:
    function: str
    executor: str
    entrypoint: str | None = None


def load_bindings(data: dict, base_dir: Path | None = None) -> dict[str, FunctionBinding]:
    """Build bindings from a parsed ``bindings`` mapping.

    Relative entrypoints are resolved against ``base_dir`` (usually the
    directory containing the bindings file).
    """
    raw = data.get("bindings", data) if isinstance(data, dict) else {}
    bindings: dict[str, FunctionBinding] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ValueError(f"binding for {name!r} must be a mapping")
        executor = spec.get("executor", "python")
        entrypoint = spec.get("entrypoint")
        if entrypoint and base_dir is not None:
            entry = Path(entrypoint)
            if not entry.is_absolute():
                entrypoint = str((base_dir / entry).resolve())
        bindings[name] = FunctionBinding(
            function=name, executor=executor, entrypoint=entrypoint
        )
    return bindings
