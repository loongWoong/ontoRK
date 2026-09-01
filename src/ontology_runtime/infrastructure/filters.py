"""Shared Django-style filter matching for stores.

Supported: eq (default), ne, gt, gte, lt, lte, contains, in.
``balance__gt=100`` -> field "balance", op "gt".
"""

from __future__ import annotations

_FILTER_OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "in")


def parse_filter_key(key: str) -> tuple[str, str]:
    field, _, op = key.partition("__")
    return (field, op or "eq")


def match_object(properties: dict, filters: dict) -> bool:
    for key, expected in filters.items():
        field, op = parse_filter_key(key)
        if field not in properties:
            return False
        actual = properties[field]
        try:
            if op == "eq" and not actual == expected:
                return False
            if op == "ne" and not actual != expected:
                return False
            if op == "gt" and not actual > expected:
                return False
            if op == "gte" and not actual >= expected:
                return False
            if op == "lt" and not actual < expected:
                return False
            if op == "lte" and not actual <= expected:
                return False
            if op == "contains" and expected not in actual:
                return False
            if op == "in" and actual not in expected:
                return False
        except TypeError:
            return False
    return True
