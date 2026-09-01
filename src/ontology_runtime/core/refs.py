"""Object identity: (type, id).

``Vehicle/V001`` and ``Station/V001`` can coexist; ids are never global.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import InvalidArgument


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """Canonical reference to one object instance."""

    type: str
    id: str

    def __post_init__(self) -> None:
        if not self.type or "/" in self.type:
            raise InvalidArgument(f"invalid object type: {self.type!r}")
        if not self.id or "/" in self.id:
            raise InvalidArgument(f"invalid object id: {self.id!r}")

    @classmethod
    def parse(cls, text: str) -> "ObjectRef":
        """Parse ``"Vehicle/V001"`` into an ObjectRef."""
        if "/" not in text:
            raise InvalidArgument(
                f"object reference must be 'Type/Id', got {text!r}"
            )
        type_, id_ = text.split("/", 1)
        return cls(type_, id_)

    def __str__(self) -> str:
        return f"{self.type}/{self.id}"
