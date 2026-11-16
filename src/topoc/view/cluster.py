"""The cluster model topoview draws, read from the cluster's AMPS XML
configuration files — the files `ampServer` itself runs, one per instance.

What a configuration reveals, and so what the model holds:

- the instance: `<Name>` and `<Group>` (an instance without a group is its own
  group, as in AMPS);
- the topics on it: those with a `<SOW>`, those journaled in its
  `<TransactionLog>`, and those it replicates. A plain pub/sub topic leaves no
  trace in a configuration, so it cannot appear;
- its replication: each `<Destination>` targets the group its `<Group>` names,
  which is how AMPS addresses replication. Addresses are not compared, since in
  hand-written configurations they may be literal and unresolvable.

Anything else in a configuration is ignored.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from topoc.errors import ClusterError


@dataclass(frozen=True, slots=True)
class Sow:
    """A topic's SOW on one instance."""

    # The key's wire paths, as the configuration writes them.
    keys: list[str]
    # The same paths in field names (`/symbol` for `/s`), when a catalog gave
    # them; `None` otherwise.
    key_names: list[str] | None
    # `persistent` or `transient`, as written; `None` if the configuration
    # does not say.
    durability: str | None
    # The SOW file, for a persistent SOW.
    file: str | None
    # Where a transient SOW recovers from (`now`, `epoch`).
    recovery_point: str | None


@dataclass(frozen=True, slots=True)
class HostedTopic:
    """A topic as an instance's configuration reveals it."""

    name: str
    message_type: str | None
    # Materialised in a SOW, if it is.
    sow: Sow | None
    # Recorded in the instance's transaction log.
    journaled: bool


@dataclass(frozen=True, slots=True)
class ReplicatedTopic:
    """A topic a destination replicates, with its replication settings."""

    name: str
    message_type: str | None
    # The filter as the configuration writes it, with wire paths.
    filter: str | None
    # The same filter in field names, when a catalog gave them; `None`
    # otherwise.
    filter_names: str | None
    # The validation checks the destination skips (`cascade`, `replicate`, ...).
    excluded_validation: list[str]


@dataclass(frozen=True, slots=True)
class Destination:
    """One `<Destination>` of an instance's `<Replication>`, as written."""

    name: str | None
    # The group it replicates to.
    group: str
    # `sync` or `async`; `None` if the configuration leaves AMPS's default.
    sync_type: str | None
    topics: list[ReplicatedTopic]
    # The replication addresses, as written (often environment placeholders).
    addresses: list[str]


class QueueKind(Enum):
    """Which AMPS queue element a queue is."""

    DISTRIBUTED = "distributed"  # `Queue`
    GROUP_LOCAL = "group_local"  # `GroupLocalQueue`
    LOCAL = "local"  # `LocalQueue`


# The queue elements under `<SOW>`, by tag.
_QUEUE_ELEMENTS = {
    "Queue": QueueKind.DISTRIBUTED,
    "GroupLocalQueue": QueueKind.GROUP_LOCAL,
    "LocalQueue": QueueKind.LOCAL,
}

# The queue elements read into their own fields; every other child element is
# kept, as written, in `Queue.settings`.
_QUEUE_FIELDS = frozenset(
    {
        "Name",
        "Topic",
        "MessageType",
        "UnderlyingTopic",
        "DefaultPublishTarget",
        "InitialOwner",
        "GroupLocalQueueDomain",
        "Semantics",
        "Filter",
        "Priority",
        "BarrierExpression",
    }
)


@dataclass(frozen=True, slots=True)
class QueueSetting:
    """A queue setting as written: its element and text (`LeasePeriod`, `60s`)."""

    name: str
    value: str


@dataclass(frozen=True, slots=True)
class Queue:
    """A queue as an instance's configuration declares it."""

    name: str
    kind: QueueKind
    message_type: str | None
    # `UnderlyingTopic` as written: a topic name or a pattern; `None` when the
    # queue reads the topic of its own name.
    underlying_topic: str | None
    # The topics on this instance it reads, by name: the underlying topic, or
    # those the pattern matches; the queue's own name when there is none.
    sources: list[str]
    default_publish_target: str | None
    initial_owner: str | None
    domain: str | None
    # `at-least-once` or `at-most-once`; `None` if left to AMPS's default.
    semantics: str | None
    # As written, with wire paths; `*_names` the same in field names when a
    # catalog gave them.
    filter: str | None
    filter_names: str | None
    priority: str | None
    priority_names: str | None
    barrier: str | None
    barrier_names: str | None
    # Every other setting, as written, in configuration order.
    settings: list[QueueSetting]


@dataclass(frozen=True, slots=True)
class Instance:
    """One AMPS instance: one configuration file."""

    name: str
    group: str
    # The configuration file it was read from.
    source: str
    # By name. A queue's own topic is not among them: it is the queue.
    topics: list[HostedTopic]
    # By name.
    queues: list[Queue]
    # In configuration order.
    destinations: list[Destination]


@dataclass(frozen=True, slots=True)
class Group:
    """A replication group: the instances declaring it, by name."""

    name: str
    members: list[str]


class TargetKind(Enum):
    """What a replication edge lands on."""

    # The one other member of the destination's group.
    INSTANCE = "instance"
    # Several members: a failover list, delivered to whichever is up.
    GROUP = "group"
    # A group none of the configuration files declares.
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class Edge:
    """A replication edge: one destination of one instance, resolved to what it
    lands on."""

    source: str
    target_kind: TargetKind
    # The instance, group or external group name.
    target: str
    # The members the destination can reach (none for an external target).
    members: list[str]
    destination: Destination


@dataclass(frozen=True, slots=True)
class Cluster:
    name: str
    # By name.
    instances: list[Instance]
    # By name.
    groups: list[Group]
    # By source instance, then in configuration order.
    edges: list[Edge]
    # Oddities that do not stop the cluster from being drawn.
    warnings: list[str]
    # Whether SOW keys and filters were given field names from a catalog.
    field_names: bool


def read_cluster(name: str, files: Sequence[Path]) -> Cluster:
    """Read the cluster `name` from its configuration files. Raises
    `ClusterError` if a file cannot be read as an AMPS configuration, or two
    declare the same instance."""
    instances: list[Instance] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ClusterError(f"{path}: cannot open file ({error.strerror})") from error
        except UnicodeDecodeError as error:
            raise ClusterError(f"{path}: file is not valid UTF-8") from error
        instances.append(parse_instance(text, str(path)))
    return build_cluster(name, instances)


def parse_instance(text: str, source: str) -> Instance:
    """One instance from the text of its configuration, read from `source`.
    Raises `ClusterError`."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise ClusterError(f"{source}: invalid XML: {error}") from error
    if root.tag != "AMPSConfig":
        raise ClusterError(f"{source}: not an AMPS configuration (root element <{root.tag}>)")
    name = _text(root, "Name")
    if name is None:
        raise ClusterError(f"{source}: the configuration has no <Name>")
    destinations = [
        _destination(element, source) for element in root.findall("Replication/Destination")
    ]
    hosted = _hosted_topics(root, destinations)
    queue_elements = [
        element for element in root.findall("SOW/*") if element.tag in _QUEUE_ELEMENTS
    ]
    queue_names = {_text(element, "Name") or _text(element, "Topic") for element in queue_elements}
    topics = [topic for topic in hosted if topic.name not in queue_names]
    queues = sorted(
        (_queue(element, source, [topic.name for topic in topics]) for element in queue_elements),
        key=lambda queue: queue.name,
    )
    return Instance(
        name=name,
        group=_text(root, "Group") or name,
        source=source,
        topics=topics,
        queues=queues,
        destinations=destinations,
    )


def build_cluster(name: str, instances: list[Instance]) -> Cluster:
    """The cluster of `instances`, with their replication resolved to edges.
    Raises `ClusterError` if two instances share a name."""
    by_name: dict[str, Instance] = {}
    for instance in instances:
        if (first := by_name.get(instance.name)) is not None:
            raise ClusterError(
                f"{instance.source}: instance '{instance.name}' is already declared by "
                f"{first.source}"
            )
        by_name[instance.name] = instance
    ordered = sorted(instances, key=lambda instance: instance.name)

    members: dict[str, list[str]] = {}
    for instance in ordered:
        members.setdefault(instance.group, []).append(instance.name)
    groups = [Group(group, members[group]) for group in sorted(members)]

    warnings: list[str] = []
    edges: list[Edge] = []
    for instance in ordered:
        warnings.extend(_message_type_conflicts(instance))
        warnings.extend(
            f"{instance.name}: queue '{queue.name}' reads '{queue.underlying_topic}', which "
            "matches no topic of the instance"
            for queue in instance.queues
            if queue.underlying_topic is not None and not queue.sources
        )
        for destination in instance.destinations:
            edge = _edge(instance, destination, members)
            if edge is None:
                warnings.append(
                    f"{instance.name}: destination to group '{destination.group}' reaches no "
                    "other instance"
                )
                continue
            edges.append(edge)
    return Cluster(name, ordered, groups, edges, warnings, field_names=False)


def _edge(
    instance: Instance, destination: Destination, members: dict[str, list[str]]
) -> Edge | None:
    """What `destination` lands on: the one other member of its group, the group
    (several members), or an external group; `None` if the group holds only the
    instance itself."""
    if destination.group not in members:
        return Edge(instance.name, TargetKind.EXTERNAL, destination.group, [], destination)
    reachable = [member for member in members[destination.group] if member != instance.name]
    match reachable:
        case []:
            return None
        case [member]:
            return Edge(instance.name, TargetKind.INSTANCE, member, reachable, destination)
        case _:
            return Edge(instance.name, TargetKind.GROUP, destination.group, reachable, destination)


def _hosted_topics(root: ET.Element, destinations: list[Destination]) -> list[HostedTopic]:
    """The topics an instance's configuration reveals: SOW, journal and
    replication, merged by name."""
    message_types: dict[str, str | None] = {}
    sows: dict[str, Sow] = {}
    journaled: set[str] = set()

    def note(name: str, message_type: str | None) -> None:
        if message_types.get(name) is None:
            message_types[name] = message_type

    for element in root.findall("SOW/Topic"):
        if (name := _text(element, "Name")) is not None:
            note(name, _text(element, "MessageType"))
            sows[name] = Sow(
                keys=_texts(element, "Key"),
                key_names=None,
                durability=_text(element, "Durability"),
                file=_text(element, "FileName"),
                recovery_point=_text(element, "RecoveryPoint"),
            )
    for element in root.findall("TransactionLog/Topic"):
        if (name := _text(element, "Name")) is not None:
            note(name, _text(element, "MessageType"))
            journaled.add(name)
    for destination in destinations:
        for topic in destination.topics:
            note(topic.name, topic.message_type)
    return [
        HostedTopic(name, message_types[name], sows.get(name), name in journaled)
        for name in sorted(message_types)
    ]


def _queue(element: ET.Element, source: str, topics: list[str]) -> Queue:
    """A queue element. `topics` are the instance's topics, which a pattern in
    `UnderlyingTopic` is matched against."""
    name = _text(element, "Name") or _text(element, "Topic")
    if name is None:
        raise ClusterError(f"{source}: a <{element.tag}> has no <Name>")
    underlying = _text(element, "UnderlyingTopic")
    if underlying is None:
        sources = [name]
    elif _QUEUE_PATTERN_CHARACTERS.isdisjoint(underlying):
        sources = [underlying] if underlying in topics else []
    else:
        try:
            pattern = re.compile(underlying)
        except re.error as error:
            raise ClusterError(
                f"{source}: queue '{name}': UnderlyingTopic '{underlying}' is not a valid pattern "
                f"({error})"
            ) from error
        # AMPS matches topic patterns anywhere in the name, as `re.search` does.
        sources = [topic for topic in topics if pattern.search(topic)]
    return Queue(
        name=name,
        kind=_QUEUE_ELEMENTS[element.tag],
        message_type=_text(element, "MessageType"),
        underlying_topic=underlying,
        sources=sources,
        default_publish_target=_text(element, "DefaultPublishTarget"),
        initial_owner=_text(element, "InitialOwner"),
        domain=_text(element, "GroupLocalQueueDomain"),
        semantics=_text(element, "Semantics"),
        filter=_text(element, "Filter"),
        filter_names=None,
        priority=_text(element, "Priority"),
        priority_names=None,
        barrier=_text(element, "BarrierExpression"),
        barrier_names=None,
        settings=[
            QueueSetting(child.tag, child.text.strip())
            for child in element
            if child.tag not in _QUEUE_FIELDS and child.text is not None and child.text.strip()
        ],
    )


# Characters that make an `UnderlyingTopic` a pattern rather than a name.
_QUEUE_PATTERN_CHARACTERS = frozenset("^$.*+?()[]{}|\\")


def _message_type_conflicts(instance: Instance) -> list[str]:
    """Warnings for topics an instance's configuration gives several message
    types."""
    declared: dict[str, set[str]] = {}
    for topic in instance.topics:
        if topic.message_type is not None:
            declared.setdefault(topic.name, set()).add(topic.message_type)
    for destination in instance.destinations:
        for replicated in destination.topics:
            if replicated.message_type is not None:
                declared.setdefault(replicated.name, set()).add(replicated.message_type)
    return [
        f"{instance.name}: topic '{name}' has several message types ({', '.join(sorted(types))})"
        for name, types in sorted(declared.items())
        if len(types) > 1
    ]


def _destination(element: ET.Element, source: str) -> Destination:
    group = _text(element, "Group")
    if group is None:
        raise ClusterError(f"{source}: a <Destination> has no <Group>")
    return Destination(
        name=_text(element, "Name"),
        group=group,
        sync_type=_text(element, "SyncType"),
        topics=[
            ReplicatedTopic(
                name=name,
                message_type=_text(topic, "MessageType"),
                filter=_text(topic, "Filter"),
                filter_names=None,
                excluded_validation=[
                    check.strip()
                    for check in (_text(topic, "ExcludeValidation") or "").split(",")
                    if check.strip()
                ],
            )
            for topic in element.findall("Topic")
            if (name := _text(topic, "Name")) is not None
        ],
        addresses=_texts(element, "Transport/InetAddr"),
    )


def _text(element: ET.Element, path: str) -> str | None:
    """The stripped text of the first element at `path`, or `None` if there is
    none or it is empty."""
    found = element.find(path)
    if found is None or found.text is None or not found.text.strip():
        return None
    return found.text.strip()


def _texts(element: ET.Element, path: str) -> list[str]:
    """The stripped, non-empty texts of every element at `path`."""
    return [
        found.text.strip()
        for found in element.findall(path)
        if found.text is not None and found.text.strip()
    ]
