"""Topic catalog: the deployment facts behind each topic's `definition` —
codec, routing, wire SOW keys and the field-name tree used to rewrite filters.
A `.topo` file only names these; they are supplied as data.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from topoc.errors import CatalogError


@dataclass(frozen=True, slots=True)
class CatalogField:
    """One field of a message: its `name`, which filters and keys are written
    against, and its `wire_name`, the serialized name AMPS matches on."""

    name: str
    wire_name: str
    # Nested fields of a nested message; empty for a scalar leaf.
    children: list["CatalogField"]


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    # The `definition` path the `.topo` references; the catalog key.
    definition: str
    # The definition's topic name — or, for a family of topics sharing this
    # contract, its name pattern, where each `{}` stands for one whole
    # `/`-separated segment. Every `.topo` topic naming this definition must
    # match it (checked at generation).
    name_pattern: str
    # Codec / AMPS message type (`msgpack`, `json`).
    codec: str
    # Whether publishes are envelope-wrapped (message fields under `/msg`).
    routed: bool
    # Final AMPS `<Key>` wire paths. Empty for a pub/sub topic.
    wire_keys: list[str]
    # The message's field tree: names and their wire names.
    fields: list[CatalogField]

    def resolve_wire_path(self, path: str) -> str | None:
        """Resolve an absolute field-name path (`/book/venue`) to its wire path
        (`/b/v`), or `None` if a segment names no field or descends into a
        scalar leaf."""
        if not path.startswith("/"):
            return None
        wire = ""
        level = self.fields
        segments = path[1:].split("/")
        for depth, segment in enumerate(segments):
            found = next((f for f in level if f.name == segment), None)
            if found is None:
                return None
            wire += "/" + found.wire_name
            is_last = depth == len(segments) - 1
            if not is_last and not found.children:
                return None
            level = found.children
        return wire

    def resolve_field_path(self, wire_path: str) -> str | None:
        """Resolve an absolute wire path (`/b/v`) back to its field-name path
        (`/book/venue`): the inverse of `resolve_wire_path`. `None` if a segment
        names no field's wire name or descends into a scalar leaf."""
        if not wire_path.startswith("/"):
            return None
        path = ""
        level = self.fields
        segments = wire_path[1:].split("/")
        for depth, segment in enumerate(segments):
            found = next((f for f in level if f.wire_name == segment), None)
            if found is None:
                return None
            path += "/" + found.name
            is_last = depth == len(segments) - 1
            if not is_last and not found.children:
                return None
            level = found.children
        return path

    def matches(self, topic: str) -> bool:
        """Whether `topic` fits this entry's name pattern: a literal segment
        equal, a `{}` hole any non-empty segment."""
        pattern = self.name_pattern.split("/")
        segments = topic.split("/")
        return len(pattern) == len(segments) and all(
            segment != "" if expected == "{}" else segment == expected
            for expected, segment in zip(pattern, segments, strict=True)
        )


class Catalog:
    """Catalog entries indexed by `definition`."""

    def __init__(self, entries: Iterable[CatalogEntry]) -> None:
        """Raises `CatalogError` if two entries share a `definition`."""
        self._by_definition: dict[str, CatalogEntry] = {}
        for entry in entries:
            if entry.definition in self._by_definition:
                raise CatalogError(f"duplicate entry for '{entry.definition}'")
            self._by_definition[entry.definition] = entry

    def find(self, definition: str) -> CatalogEntry | None:
        """The entry for `definition`, or `None` if the catalog has none."""
        return self._by_definition.get(definition)

    def entries_matching(self, topic: str) -> list[CatalogEntry]:
        """The entries whose name pattern `topic` fits, in catalog order."""
        return [entry for entry in self._by_definition.values() if entry.matches(topic)]
