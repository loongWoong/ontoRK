"""FunctionExecutor Port: how a FunctionDef is actually executed.

The executor loads the implementation (binding), runs it against the
execution context, collects mutations and finalizes them into a
FunctionResult.  Default implementation: PythonFunctionExecutor.
"""

from __future__ import annotations

from typing import Protocol

from ..core.execution import ExecutionContext, FunctionCall, FunctionResult


class FunctionExecutor(Protocol):

    def execute(self, call: FunctionCall, context: ExecutionContext) -> FunctionResult:
        ...
