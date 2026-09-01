"""PythonFunctionExecutor: the default executor.

Responsibilities:
    read the function binding
    load the python function (cached)
    run it with the ExecutionContext
    return a FunctionResult carrying the output

The executor never applies state: mutations buffered by the function stay
on the context, and the runtime validates + applies them atomically after
execute() returns.  The script only ever sees ``ctx`` - never the store,
SQLite, MCP or HTTP.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ..core.errors import FunctionExecutionError, KernelError
from ..core.execution import ExecutionContext, FunctionCall, FunctionResult
from .bindings import FunctionBinding


class PythonFunctionExecutor:

    def __init__(
        self,
        bindings: dict[str, FunctionBinding] | None = None,
    ) -> None:
        self._bindings = bindings or {}
        self._cache: dict[tuple[str, str], callable] = {}

    def register(self, binding: FunctionBinding) -> None:
        self._bindings[binding.function] = binding

    def execute(self, call: FunctionCall, context: ExecutionContext) -> FunctionResult:
        binding = self._bindings.get(call.function)
        if binding is None:
            raise FunctionExecutionError(
                f"function {call.function!r} has no implementation binding"
            )
        if binding.executor != "python":
            raise FunctionExecutionError(
                f"function {call.function!r} is bound to executor "
                f"{binding.executor!r}, but this runtime only provides 'python'"
            )
        fn = self._load(binding)
        try:
            output = fn(context)
        except KernelError:
            raise  # domain errors flow through untouched
        except Exception as exc:
            raise FunctionExecutionError(
                f"function {call.function!r} failed: {exc}"
            ) from exc
        # Mutations stay buffered on the context; the runtime finalizes them
        # (validate -> apply -> changes) as one atomic unit.
        return FunctionResult(execution_id=context.execution_id, output=output)

    # ---- internals ----

    def _load(self, binding: FunctionBinding):
        entry = binding.entrypoint or ""
        if ":" in entry and not entry.endswith(".py"):
            module_name, _, attr = entry.partition(":")
            fn = _import_module_attr(module_name, attr)
            return fn
        attr = "execute"
        path_text = entry
        if ".py:" in entry:
            path_text, _, attr = entry.partition(":")
        path = Path(path_text)
        if not path.is_file():
            raise FunctionExecutionError(
                f"entrypoint not found for {binding.function!r}: {path}"
            )
        key = (str(path), attr)
        if key not in self._cache:
            self._cache[key] = _load_file_attr(path, attr)
        return self._cache[key]


def _load_file_attr(path: Path, attr: str):
    module_name = f"_ontology_fn_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise FunctionExecutionError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    fn = getattr(module, attr, None)
    if fn is None or not callable(fn):
        raise FunctionExecutionError(
            f"{path} does not define callable {attr!r}"
        )
    return fn


def _import_module_attr(module_name: str, attr: str):
    import importlib

    module = importlib.import_module(module_name)
    fn = getattr(module, attr, None)
    if fn is None or not callable(fn):
        raise FunctionExecutionError(
            f"module {module_name!r} does not define callable {attr!r}"
        )
    return fn
