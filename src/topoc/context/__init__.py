"""The generation context: deployment facts a `.topo` declares in its `context`
block and the caller supplies, as JSON.

`Context` is the data as supplied: an untyped tree read in document order, so
generated instances and topics follow the order it was written in. It is
checked against the declaration (see `topoc.context.schema`) when the topology
is compiled.
"""

import json
from dataclasses import dataclass
from typing import Self

from topoc.errors import ContextError

_INT_MIN = -(2**63)
_INT_MAX = 2**63 - 1


@dataclass(frozen=True, slots=True)
class ObjectData:
    """A JSON object: members in document order; keys are distinct."""

    members: list[tuple[str, "Data"]]


# One value of the supplied data, before it is checked against a type. A float
# is kept so a mismatch can name the value; no declared type accepts it.
type Data = bool | int | float | str | list[Data] | ObjectData | None


def describe(data: Data) -> str:
    """The value as an error message shows it: `null`, `3`, `'LN'`, `a list`."""
    match data:
        case None:
            return "null"
        case bool():
            return "true" if data else "false"
        case int() | float():
            return str(data)
        case str():
            return f"'{data}'"
        case list():
            return "a list"
        case ObjectData():
            return "an object"


@dataclass(frozen=True, slots=True)
class Context:
    """Generation context data for a `.topo` that declares a `context` block."""

    # The top-level object's members, in document order.
    members: list[tuple[str, Data]]

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Read context data from JSON text: an object at the top level.

        Raises `ContextError` if the text is not JSON, its top level is not an
        object, an object repeats a key, or an integer does not fit 64 signed
        bits."""
        try:
            root = json.loads(text, object_pairs_hook=_object, parse_int=_integer)
        except json.JSONDecodeError as error:
            raise ContextError(f"invalid JSON: {error}") from error
        if not isinstance(root, ObjectData):
            raise ContextError(f"the context must be an object, got {describe(root)}")
        return cls(root.members)


def _object(pairs: list[tuple[str, Data]]) -> ObjectData:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise ContextError(f"duplicate key '{key}'")
        seen.add(key)
    return ObjectData(pairs)


def _integer(text: str) -> int:
    value = int(text)
    if not _INT_MIN <= value <= _INT_MAX:
        raise ContextError(f"integer {text} does not fit 64 signed bits")
    return value
