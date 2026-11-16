"""The flat namespace shared by the resolver and the template expander. Context
fields are not in it: they are written `@field`."""

from dataclasses import dataclass
from enum import Enum

from topoc.errors import SourceLocation


class NameKind(Enum):
    """What a declared name denotes, valued by its description."""

    ROLE = "role"
    TOPIC = "topic"
    QUEUE = "queue"
    INSTANCE = "instance"
    GROUP = "group"
    LIST = "list"
    TOPOLOGY = "topology block"


@dataclass(frozen=True, slots=True)
class NameEntry:
    kind: NameKind
    location: SourceLocation
