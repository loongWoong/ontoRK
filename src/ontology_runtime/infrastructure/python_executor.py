"""PythonFunctionExecutor: the default executor.

Responsibilities:
    read the function binding
    load the python function (cached by path + mtime/size)
    run it with the ExecutionContext
    return a FunctionResult carrying the output

The executor never applies state: mutations buffered by the function stay
on the context, and the runtime validates + applies them atomically after
execute() returns.  The script only ever sees ``ctx`` - never the store,
SQLite, MCP or HTTP.

v0.9 fix: the load cache key includes the file's ``mtime_ns`` and ``size``,
so re-publishing source that happens to land on the same path takes effect
on the next invocation instead of serving a stale function object.
"""

from __future__ import annotations

import hashlib
import sys
import types
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
        # key: (path, attr, mtime_ns, size, content_hash) -> callable
        self._cache: dict[tuple[str, str, int, int, str], callable] = {}

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
        except TimeoutError:
            # let the runtime mark the result "unknown" instead of wrapping it
            raise
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
        attr = "execute"
        path_text = entry
        if ".py:" in entry:  # "functions/fn.py:custom_attr" (also on Windows)
            path_text, _, attr = entry.partition(".py:")
            path_text += ".py"
        elif entry.endswith(".py"):
            path_text = entry
        elif ":" in entry:  # "package.module:attr"
            module_name, _, attr = entry.partition(":")
            return _import_module_attr(module_name, attr)
        path = Path(path_text)
        if not path.is_file():
            raise FunctionExecutionError(
                f"entrypoint not found for {binding.function!r}: {path}"
            )
        try:
            raw = path.read_bytes()
            stat = path.stat()
            digest = hashlib.sha256(raw).hexdigest()
        except OSError as exc:  # pragma: no cover - defensive
            raise FunctionExecutionError(
                f"cannot read entrypoint for {binding.function!r}: {path}"
            ) from exc
        key = (str(path), attr, stat.st_mtime_ns, stat.st_size, digest)
        if key not in self._cache:
            # drop stale revisions of the same file/attr so a republished
            # script can never keep a stale cache entry alive
            for stale in [
                cached for cached in self._cache
                if cached[0] == str(path) and cached[1] == attr
            ]:
                del self._cache[stale]
            self._cache[key] = _load_file_attr(path, attr, raw)
        return self._cache[key]


def _load_file_attr(path: Path, attr: str, raw: bytes | None = None):
    """Compile the source directly.

    ``importlib`` may serve stale bytecode from ``__pycache__`` when a script
    is re-published with the same size within the same mtime second; a
    direct compile always reflects the bytes we just read.
    """
    module_name = f"_ontology_fn_{abs(hash(str(path)))}"
    if raw is None:
        raw = path.read_bytes()
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    sys.modules[module_name] = module
    try:
        code = compile(raw, str(path), "exec")
        exec(code, module.__dict__)
    except Exception as exc:
        raise FunctionExecutionError(
            f"cannot import entrypoint {path}: {exc}"
        ) from exc
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
