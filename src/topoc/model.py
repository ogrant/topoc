"""The fully resolved cluster model: output of the resolver, input to the
generators.

Everything group- or role-level in the source has been fanned out to concrete
per-instance placements and replication edges, with `{{Role.attr}}` filter
interpolations applied. Generators must not need to re-derive anything the
resolver already knows. The model is independent of the syntax tree, so the
syntax tree never surfaces through it.
"""

from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import NamedTuple

from topoc.queue import Delivery, Duration, QueueRecoveryPoint

type AttrValue = str | int | bool


class AttrKey(NamedTuple):
    """Identity of an attribute: the role that declares it plus its name."""

    role: str
    attr: str


@dataclass(frozen=True, slots=True)
class ResolvedTopic:
    name: str
    # The name as written in the `.topo`: equal to `name` for a hand-written
    # topic; for one expanded from a `for` template, the template name with its
    # `{{...}}` placeholders. Generation uses it to check that interpolation only
    # fills holes of the definition's name pattern.
    name_template: str
    # The `definition` path naming this topic's catalog entry, which supplies
    # its codec, SOW keys and field names at generation time.
    definition: str
    # The instances hosting this declaration's copy of the topic, in
    # declaration order. Several declarations may share a name, each on its own
    # instances: an instance hosts one topic or queue of each name.
    instances: list[str]


class RecoveryPoint(StrEnum):
    """Recovery point of a transient SOW, valued as emitted in `<RecoveryPoint>`."""

    NOW = "now"
    EPOCH = "epoch"


@dataclass(frozen=True, slots=True)
class PersistentDurability:
    file: str


@dataclass(frozen=True, slots=True)
class TransientDurability:
    recovery_point: RecoveryPoint


type Durability = PersistentDurability | TransientDurability


@dataclass(frozen=True, slots=True)
class ResolvedSow:
    """A SOW materialisation of a topic on one instance, from a `sow` statement.
    A topic with no `sow` statement is pure pub/sub and materialises nothing."""

    topic: str
    durability: Durability


@dataclass(frozen=True, slots=True)
class ResolvedGroup:
    """A replication group. Explicit groups come from `group` clauses; every
    instance without one forms an implicit singleton group named after itself."""

    name: str
    members: list[str]
    is_explicit: bool


class EdgeKind(Enum):
    """How a replication edge was declared. `mesh` members are HA failover
    partners; `replicate` is one-way distribution to a non-partner;
    `replicate <a> <-> <b>` is one two-way edge each way, with the same filter:
    each end replicates the topic back, but they are not failover partners."""

    MESH = "mesh"
    REPLICATE = "replicate"
    TWO_WAY = "two_way"


@dataclass(frozen=True, slots=True)
class Destination:
    """One outgoing replication edge. A group target yields a single destination
    listing every member as a failover address; role and instance targets yield
    one destination per target instance."""

    topic: str
    kind: EdgeKind
    target_group: str
    target_members: list[str]
    # Verbatim AMPS filter with `{{Role.attr}}` already interpolated.
    filter: str | None
    # Groups whose replicated messages of this topic the destination relays
    # (AMPS `PassThrough`), sorted. AMPS forwards only messages its own clients
    # published unless the destination passes the group a message arrived
    # from, so a target member that receives the topic from those groups only
    # through this instance needs them here.
    passthrough: list[str]


@dataclass(frozen=True, slots=True)
class Incoming:
    """One incoming replication edge: the inversion of a `Destination`."""

    topic: str
    source: str
    filter: str | None


@dataclass(frozen=True, slots=True)
class ResolvedInstance:
    name: str
    # The replication group (the instance's own name for implicit singletons).
    group: str
    # Roles in declaration order.
    roles: list[str]
    # Every attribute of every carried role, with defaults applied.
    attrs: dict[AttrKey, AttrValue]
    # Topics hosted on this instance, sorted by name.
    topics: list[str]
    # Outgoing edges, in source declaration order.
    destinations: list[Destination]
    # Incoming edges, in source declaration order.
    incoming: list[Incoming]
    # SOW materialisations hosted on this instance.
    sows: list[ResolvedSow]
    # Queues held on this instance, sorted by name.
    queues: list[str]
    # Outgoing replication edges of queues; `topic` is the queue's name.
    # `passthrough` follows AMPS's rule for queues: every group the queue
    # arrives from, except the destination's own.
    queue_destinations: list[Destination]
    # Incoming replication edges of queues; `topic` is the queue's name.
    queue_incoming: list[Incoming]


@dataclass(frozen=True, slots=True)
class QueueTopics:
    """The queue reads other topics (`UnderlyingTopic`), as written;
    `publish_to` is set when written, and always for several sources
    (`DefaultPublishTarget`)."""

    sources: list[str]
    publish_to: str | None


@dataclass(frozen=True, slots=True)
class QueueOwnName:
    """The queue reads the topic of its own name."""


# Where a queue's messages come from.
type QueueSource = QueueTopics | QueueOwnName


@dataclass(frozen=True, slots=True)
class DistributedQueue:
    """`Queue` (`mesh`, `replicate ... <-> ...`): one queue across every
    instance holding it."""


@dataclass(frozen=True, slots=True)
class QueueDomain:
    """One `owned by` block: a queue shared by `members`, whose messages all
    start owned by `owner`. Domains do not share acknowledgements (lab F21)."""

    owner: str
    # By name.
    members: list[str]


@dataclass(frozen=True, slots=True)
class GroupLocalQueue:
    """`GroupLocalQueue` (`owned by` blocks): one queue per domain, by owner."""

    domains: list[QueueDomain]


@dataclass(frozen=True, slots=True)
class LocalQueue:
    """`LocalQueue` (`local`): an independent queue on each instance."""


type QueueKind = DistributedQueue | GroupLocalQueue | LocalQueue


@dataclass(frozen=True, slots=True)
class Priority:
    # A field path, in field names.
    path: str


@dataclass(frozen=True, slots=True)
class Barrier:
    # A filter, in field names.
    filter: str


type QueueOrdering = Priority | Barrier


@dataclass(frozen=True, slots=True)
class ResolvedQueue:
    """An AMPS message queue: what it reads and how it delivers. Where it
    lives and replicates is on each instance holding it
    (`ResolvedInstance.queues`)."""

    name: str
    # The name as written: the template name, with its `{{...}}`, for a queue a
    # loop generated. Generation uses it to check that interpolation only fills
    # holes of a queue's own definition's name pattern.
    name_template: str
    # The instances hosting this declaration's copy of the queue, in
    # declaration order (see `ResolvedTopic.instances`).
    instances: list[str]
    # The `definition` giving the queue its message type and field names: its
    # own, or the one its sources share.
    definition: str
    source: QueueSource
    kind: QueueKind
    # Only messages matching it enter the queue (field names, as written).
    filter: str | None
    delivery: Delivery
    ordering: QueueOrdering | None
    recovery_point: QueueRecoveryPoint | None
    target_depth: int | None
    file_backed_metadata: bool
    deferred_ack_expiration: Duration | None


@dataclass(frozen=True, slots=True)
class ResolvedSystem:
    topics: list[ResolvedTopic]
    queues: list[ResolvedQueue]
    # Explicit groups in order of first mention, then implicit singletons.
    groups: list[ResolvedGroup]
    # Instances in declaration order.
    instances: list[ResolvedInstance]
