"""StateStore Port: persistence of State only.

It never owns schema, function definitions or execution logic.
``apply`` receives already schema-validated mutations and returns changes.
"""

from __future__ import annotations

from typing import Protocol

from ..core.execution import Change, Mutation
from ..core.refs import ObjectRef
from ..core.state import Object


class StateStore(Protocol):

    def get_object(self, ref: ObjectRef) -> Object | None:
        """Return the object or None."""
        ...

    def find_objects(
        self, object_type: str, filters: dict[str, object]
    ) -> list[Object]:
        """Return objects of ``object_type`` matching Django-style filters."""
        ...

    def related(
        self, source: ObjectRef, relation: str, direction: str
    ) -> list[ObjectRef]:
        """Navigate relations; direction is 'out' or 'in'."""
        ...

    def apply(self, mutations: list[Mutation]) -> list[Change]:
        """Apply mutations atomically; return the resulting changes."""
        ...
