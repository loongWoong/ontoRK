"""Ontology Runtime Kernel v0.1.

A small, embeddable semantic object runtime:

    Schema  defines the world
    State   keeps the world
    Function changes the world
    Change  describes what happened

Public entry points are the five runtime APIs:

    describe / get / find / related / invoke
"""

from .core.errors import (
    FunctionExecutionError,
    InvalidArgument,
    KernelError,
    ObjectNotFound,
    RelationNotFound,
    SchemaViolation,
)
from .core.execution import Change, ExecutionContext, FunctionCall, FunctionResult, Mutation
from .core.refs import ObjectRef
from .core.schema import (
    CapabilityCatalog,
    FunctionDef,
    ObjectType,
    OntologySchema,
    PropertyDef,
    RelationDef,
)
from .core.state import Object, Relation
from .core.types import TypeDef
from .infrastructure.memory import MemoryStateStore
from .infrastructure.python_executor import PythonFunctionExecutor
from .runtime.runtime import OntologyRuntime

__version__ = "0.1.0"

__all__ = [
    "CapabilityCatalog",
    "Change",
    "ExecutionContext",
    "FunctionCall",
    "FunctionDef",
    "FunctionExecutionError",
    "FunctionResult",
    "InvalidArgument",
    "KernelError",
    "MemoryStateStore",
    "Mutation",
    "Object",
    "ObjectNotFound",
    "ObjectRef",
    "ObjectType",
    "OntologyRuntime",
    "OntologySchema",
    "PropertyDef",
    "PythonFunctionExecutor",
    "Relation",
    "RelationDef",
    "RelationNotFound",
    "SchemaViolation",
    "TypeDef",
]
