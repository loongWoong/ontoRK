"""Ports: the only two variation points of the kernel (Principle 7).

    StateStore        - where state lives (memory / sqlite / ...)
    FunctionExecutor  - how functions run (python / builtin / http / wasm / ...)
"""

from .function_executor import FunctionExecutor
from .state_store import StateStore

__all__ = ["FunctionExecutor", "StateStore"]
