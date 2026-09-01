"""Runtime error model.

The specification calls the base error ``RuntimeError``; here it is named
``KernelError`` to avoid shadowing Python's builtin ``RuntimeError``.
Adapters translate these into MCP errors / HTTP status codes / CLI output.
"""

from __future__ import annotations


class KernelError(Exception):
    """Base class of every ontology runtime error."""


class ObjectNotFound(KernelError):
    """A referenced object does not exist in state."""


class RelationNotFound(KernelError):
    """A relation name is not defined in the schema."""


class FunctionNotFound(KernelError):
    """A function name is not defined in the capability catalog."""


class InvalidArgument(KernelError):
    """Arguments, direction or query filters are invalid."""


class SchemaViolation(KernelError):
    """A mutation or definition violates the schema."""


class FunctionExecutionError(KernelError):
    """A function failed while executing (business error or crash)."""
