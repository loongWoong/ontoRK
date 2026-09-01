"""FunctionExecutor Port: how a FunctionDef is actually executed.

Contract: the executor runs the function against the context and returns a
FunctionResult carrying the output (or raises; the runtime turns that into
``FunctionResult.error``).  Mutations buffered on the context are validated
and applied by the runtime after execute() returns - never by the executor.
Default implementation: PythonFunctionExecutor.
"""

from __future__ import annotations

from typing import Protocol

from ..core.execution import ExecutionContext, FunctionCall, FunctionResult


class FunctionExecutor(Protocol):

    def execute(self, call: FunctionCall, context: ExecutionContext) -> FunctionResult:
        ...
