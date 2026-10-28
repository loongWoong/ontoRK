"""PythonFunctionExecutor tests.

The executor is the only component that loads user python.  Its contract:

    * it runs the function with the ExecutionContext and returns output only
    * it never applies mutations (the runtime owns commit)
    * a re-published script at the same path takes effect (v0.9 fix: the
      load cache key includes mtime/size/content hash, not just the path)
    * ``TimeoutError`` is not swallowed: the runtime needs it to report
      ``status = "unknown"``, which is different from a clean failure
"""

import pytest

from ontology_runtime.core.errors import FunctionExecutionError, ObjectNotFound
from ontology_runtime.core.execution import ExecutionContext, FunctionCall
from ontology_runtime.infrastructure.bindings import FunctionBinding
from ontology_runtime.infrastructure.python_executor import PythonFunctionExecutor


def _context(function="X.run"):
    return ExecutionContext(
        function=function,
        execution_id="exec-test",
        target=None,
        args={},
        _get_object=lambda ref: None,
        _find_objects=lambda object_type, filters: [],
        _related=lambda ref, relation, direction: [],
    )


def _run(executor, function="X.run"):
    return executor.execute(
        FunctionCall(function=function, target=None, args={}), _context(function)
    )


def _binding(path, function="X.run", entrypoint=None):
    return FunctionBinding(
        function=function,
        executor="python",
        entrypoint=entrypoint or str(path),
    )


def test_returns_function_output_and_execution_id(tmp_path):
    script = tmp_path / "fn.py"
    script.write_text(
        "def execute(ctx):\n    return {'echo': ctx.execution_id}\n",
        encoding="utf-8",
    )
    executor = PythonFunctionExecutor({"X.run": _binding(script)})
    result = _run(executor)
    assert result.output == {"echo": "exec-test"}
    assert result.execution_id == "exec-test"
    assert result.error is None
    assert result.status == "succeeded"


def test_republished_script_at_the_same_path_takes_effect(tmp_path):
    script = tmp_path / "fn.py"
    script.write_text(
        "def execute(ctx):\n    return {'version': 1}\n", encoding="utf-8"
    )
    executor = PythonFunctionExecutor({"X.run": _binding(script)})
    assert _run(executor).output == {"version": 1}

    # same path, same length on purpose: only the content differs
    script.write_text(
        "def execute(ctx):\n    return {'version': 2}\n", encoding="utf-8"
    )
    assert _run(executor).output == {"version": 2}


def test_module_level_state_is_reloaded_on_republish(tmp_path):
    script = tmp_path / "fn.py"
    script.write_text(
        "LABEL = 'first'\n\ndef execute(ctx):\n    return {'label': LABEL}\n",
        encoding="utf-8",
    )
    executor = PythonFunctionExecutor({"X.run": _binding(script)})
    assert _run(executor).output == {"label": "first"}
    script.write_text(
        "LABEL = 'second'\n\ndef execute(ctx):\n    return {'label': LABEL}\n",
        encoding="utf-8",
    )
    assert _run(executor).output == {"label": "second"}


def test_entrypoint_can_select_a_named_attribute(tmp_path):
    script = tmp_path / "fn.py"
    script.write_text(
        "def execute(ctx):\n    return 'default'\n\n"
        "def other(ctx):\n    return 'other'\n",
        encoding="utf-8",
    )
    executor = PythonFunctionExecutor(
        {
            "X.run": _binding(script),
            "X.other": _binding(script, "X.other", entrypoint=f"{script}:other"),
        }
    )
    assert _run(executor, "X.run").output == "default"
    assert _run(executor, "X.other").output == "other"


def test_missing_entrypoint_raises(tmp_path):
    binding = _binding(tmp_path / "does-not-exist.py")
    executor = PythonFunctionExecutor({"X.run": binding})
    with pytest.raises(FunctionExecutionError):
        _run(executor)


def test_missing_callable_raises(tmp_path):
    script = tmp_path / "fn.py"
    script.write_text("value = 1\n", encoding="utf-8")
    executor = PythonFunctionExecutor({"X.run": _binding(script)})
    with pytest.raises(FunctionExecutionError):
        _run(executor)


def test_timeout_error_is_not_wrapped(tmp_path):
    script = tmp_path / "fn.py"
    script.write_text(
        "def execute(ctx):\n    raise TimeoutError('too slow')\n",
        encoding="utf-8",
    )
    executor = PythonFunctionExecutor({"X.run": _binding(script)})
    with pytest.raises(TimeoutError):
        _run(executor)


def test_kernel_error_passes_through(tmp_path):
    script = tmp_path / "fn.py"
    script.write_text(
        "from ontology_runtime.core.errors import ObjectNotFound\n\n"
        "def execute(ctx):\n    raise ObjectNotFound('gone')\n",
        encoding="utf-8",
    )
    executor = PythonFunctionExecutor({"X.run": _binding(script)})
    with pytest.raises(ObjectNotFound):
        _run(executor)


def test_other_exceptions_are_wrapped(tmp_path):
    script = tmp_path / "fn.py"
    script.write_text(
        "def execute(ctx):\n    raise ValueError('bad input')\n",
        encoding="utf-8",
    )
    executor = PythonFunctionExecutor({"X.run": _binding(script)})
    with pytest.raises(FunctionExecutionError) as excinfo:
        _run(executor)
    assert "bad input" in str(excinfo.value)


def test_unbound_function_raises(tmp_path):
    executor = PythonFunctionExecutor({})
    with pytest.raises(FunctionExecutionError):
        _run(executor)
