"""StateStore Port: persistence of State only.

It never owns schema, function definitions or execution logic.
``apply`` receives already schema-validated mutations and returns changes.

v0.2 additions (all optional for a conforming store, but the two shipped
stores implement them):
    apply_delta  - upsert objects + assert relations + apply mutations
                   atomically, returning the full change list
    count_objects - object count (cheap; used for empty-store seeding)
    list_changes - read the append-only change log by sequence
"""

from __future__ import annotations

from typing import Protocol

from ..core.execution import Change, Mutation
from ..core.refs import ObjectRef
from ..core.state import Object, Relation


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

    def apply(
        self, mutations: list[Mutation], *, execution_id: str | None = None
    ) -> list[Change]:
        """Apply mutations atomically; return the resulting changes."""
        ...

    def apply_delta(
        self,
        objects: list[Object],
        relations: list[Relation],
        mutations: list[Mutation],
        *,
        execution_id: str | None = None,
        source: str | None = None,
    ) -> list[Change]:
        """Upsert objects, assert relations and apply mutations in one
        transaction; return the resulting changes."""
        ...

    def delete_objects(
        self,
        refs: list[ObjectRef],
        *,
        execution_id: str | None = None,
    ) -> list[Change]:
        """Delete objects (and their incident relations) atomically; return
        ``unlink``/``delete_object`` changes.  Idempotent: missing refs are
        skipped."""
        ...

    def count_objects(self) -> int:
        """Number of objects in state."""
        ...

    def list_changes(self, after_seq: int = 0) -> list[Change]:
        """Change-log entries with ``seq > after_seq``, oldest first."""
        ...
