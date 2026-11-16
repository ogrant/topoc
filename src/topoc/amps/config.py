"""One AMPS instance's configuration document, with every decision already
made: which topics are journaled, how destinations are named and grouped, sync
types and validation exclusions. Laying it out as XML is a separate,
decision-free step (`topoc.amps.layout`).
"""

from dataclasses import dataclass
from enum import StrEnum

from topoc.model import Durability


@dataclass(frozen=True, slots=True)
class Admin:
    inet_addr: str
    sql_transport: str


@dataclass(frozen=True, slots=True)
class Logging:
    protocol: str
    level: str


@dataclass(frozen=True, slots=True)
class Transport:
    name: str
    transport_type: str
    inet_addr: str
    protocol: str | None


@dataclass(frozen=True, slots=True)
class TopicRef:
    """A topic reference: name plus AMPS message type."""

    name: str
    message_type: str


@dataclass(frozen=True, slots=True)
class TransactionLog:
    journal_directory: str
    topics: list[TopicRef]


@dataclass(frozen=True, slots=True)
class SowTopic:
    name: str
    message_type: str
    keys: list[str]
    durability: Durability


@dataclass(frozen=True, slots=True)
class QueueConfig:
    """One queue element under `<SOW>`, with every decision made."""

    # `Queue`, `GroupLocalQueue` or `LocalQueue`.
    element: str
    name: str
    message_type: str
    underlying_topic: str | None
    default_publish_target: str | None
    initial_owner: str | None
    domain: str | None
    # In wire names.
    filter: str | None
    # `at-least-once` or `at-most-once`.
    semantics: str
    # The optional settings the `.topo` wrote, as `(element, value)`, in a
    # fixed order.
    settings: list[tuple[str, str]]


class SyncType(StrEnum):
    SYNC = "sync"
    ASYNC = "async"


@dataclass(frozen=True, slots=True)
class DestinationTopic:
    name: str
    message_type: str
    # Filter in wire field names.
    filter: str | None
    # Comma-separated AMPS validation checks to skip, e.g. `cascade`.
    exclude_validation: str | None


@dataclass(frozen=True, slots=True)
class ReplicationDestination:
    name: str
    group: str
    topics: list[DestinationTopic]
    # `<PassThrough>`: an anchored regex of the groups whose replicated
    # messages this destination relays, or `None` to relay nothing.
    passthrough: str | None
    sync_type: SyncType
    # Failover addresses, one per target member.
    inet_addrs: list[str]


@dataclass(frozen=True, slots=True)
class ModuleCall:
    """An AMPS module invocation: `<Module>` plus its `<Options>`, in order."""

    module: str
    options: list[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class Action:
    """`<Action>`: when to fire (`<On>`) and what to run (`<Do>`, in order)."""

    on: ModuleCall
    commands: list[ModuleCall]


@dataclass(frozen=True, slots=True)
class AmpsConfig:
    name: str
    group: str
    admin: Admin
    logging: Logging
    transports: list[Transport]
    # Present only when the instance replicates (AMPS requires a journal for
    # every replicated topic on both ends of an edge).
    transaction_log: TransactionLog | None
    sow_topics: list[SowTopic]
    queues: list[QueueConfig]
    replication: list[ReplicationDestination]
    actions: list[Action]
