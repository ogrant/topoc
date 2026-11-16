"""The exception types every stage reports through.

All derive from `TopocError`, so a front end can report any failure with one
handler.
"""

from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True, order=True, slots=True)
class SourceLocation:
    """1-based position of a token in `.topo` source. Columns count code points."""

    line: int
    column: int


class TopocError(Exception):
    """Base of every error topoc raises."""


class CompileError(TopocError):
    """The `.topo` source is invalid: a lexical, syntactic or semantic error."""

    def __init__(self, message: str, location: SourceLocation | None) -> None:
        # `message` is the complete, user-facing text; `location` is kept for
        # programmatic access (it is already part of `message` when relevant).
        super().__init__(message)
        self.location = location

    @classmethod
    def at(cls, message: str, location: SourceLocation) -> Self:
        """The canonical located form: "<message> at line L, column C"."""
        return cls(f"{message} at line {location.line}, column {location.column}", location)


class CatalogError(TopocError):
    """The topic catalog is invalid (malformed JSON, missing member, duplicate)."""

    def __init__(self, message: str) -> None:
        super().__init__(f"parsing topic catalog: {message}")


class ContextError(TopocError):
    """Generation context data that cannot be read, or that does not conform to
    the `.topo`'s `context` declaration. The message starts with the path of the
    offending value when there is one."""

    def __init__(self, message: str) -> None:
        super().__init__(f"context: {message}")


class ClusterError(TopocError):
    """A cluster's AMPS configuration files cannot be read as a cluster: a file
    that is not an AMPS configuration, an instance without a name, or two files
    declaring the same instance."""


class GenerateError(TopocError):
    """Configuration could not be generated from a valid topology, e.g. a topic
    absent from the catalog or a filter naming an unknown field."""

    def __init__(self, message: str) -> None:
        super().__init__(f"generating configuration: {message}")
