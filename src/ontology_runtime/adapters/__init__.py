"""Adapters: external protocols over the same Runtime API.

    CLI / MCP / HTTP / Agent SDK - all are thin protocol translations.
"""

from .serialize import error_to_dict, to_jsonable

__all__ = ["error_to_dict", "to_jsonable"]
