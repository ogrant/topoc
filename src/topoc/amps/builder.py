"""Decides each instance's AMPS configuration.

Destinations resolved to the same target group and member set are coalesced
into a single destination carrying one topic entry per replicated topic, which
is the idiomatic AMPS shape.

`mesh` edges connect HA failover partners: they replicate synchronously and keep
AMPS's full replication validation. `replicate` edges distribute one way to a
non-partner: they replicate asynchronously (so the sender's persisted acks do
not depend on downstream availability) and exclude the failover-partner
checks. Because the `cascade` check is recursive, mesh edges waive it for
topics that are also distributed onward. Two-way (`replicate <->`) edges keep
the `replicate` check and exclude `cascade` and `sow`.

A queue is a `Queue`, `GroupLocalQueue` or `LocalQueue` under `<SOW>`, with
only the settings the `.topo` wrote. It reads the transaction log, so the
queue and the topics it reads are journaled wherever it is held. A queue edge
replicates the queue's own topic with full validation, plus each topic it
reads (AMPS loses acks without the one and messages without the others),
coalesced with the topic edges to the same target: a topic both edges carry
appears once.
"""

from collections import Counter
from dataclasses import dataclass, replace

from topoc.amps import policy
from topoc.amps.config import (
    Action,
    Admin,
    AmpsConfig,
    DestinationTopic,
    Logging,
    ModuleCall,
    QueueConfig,
    ReplicationDestination,
    SowTopic,
    SyncType,
    TopicRef,
    TransactionLog,
    Transport,
)
from topoc.amps.naming import endpoint_placeholder
from topoc.amps.topic_name import check_topic_name
from topoc.catalog import Catalog, CatalogEntry
from topoc.errors import GenerateError
from topoc.filters import FilterError, rewrite
from topoc.model import (
    Barrier,
    Destination,
    EdgeKind,
    GroupLocalQueue,
    LocalQueue,
    Priority,
    QueueOwnName,
    QueueTopics,
    ResolvedInstance,
    ResolvedQueue,
    ResolvedSystem,
    ResolvedTopic,
)
from topoc.queue import AtLeastOnce, Delivery


def build_configs(system: ResolvedSystem, catalog: Catalog) -> list[AmpsConfig]:
    """Decide each instance's AMPS configuration, in instance declaration order.

    Message types and SOW keys come from the catalog; replicate filters are
    rewritten from field names to wire names. Raises `GenerateError` if a
    topic's definition is missing from the catalog, a topic's name does not fit
    its definition's name pattern, or a filter names an unknown field.
    """
    builder = _ConfigBuilder(system, catalog)
    builder.check_topic_names(system)
    return [builder.build(instance) for instance in system.instances]


@dataclass(frozen=True, slots=True)
class _DestinationBucket:
    """Replication edges sharing a target group and member set: one AMPS destination."""

    first: Destination
    entries: list[Destination]


def _coalesce(destinations: list[Destination]) -> list[_DestinationBucket]:
    buckets: dict[tuple[str, tuple[str, ...]], _DestinationBucket] = {}
    for destination in destinations:
        key = (destination.target_group, tuple(destination.target_members))
        if (bucket := buckets.get(key)) is None:
            buckets[key] = _DestinationBucket(destination, [destination])
        else:
            bucket.entries.append(destination)
    return list(buckets.values())


def _transports(instance: ResolvedInstance) -> list[Transport]:
    """The instance's listening transports. The replication transport only
    accepts incoming replication, so it is opened only on instances that receive
    some: outgoing replication connects from each destination's own transport.
    Leaving it closed elsewhere keeps undeclared replication paths out."""
    transports = [
        Transport(
            "client", "tcp", endpoint_placeholder(instance.name, policy.CLIENT_ENDPOINT), "amps"
        ),
        Transport(
            policy.SQL_TRANSPORT,
            "tcp",
            endpoint_placeholder(instance.name, policy.WEBSOCKET_ENDPOINT),
            "websocket",
        ),
    ]
    if instance.incoming or instance.queue_incoming:
        transports.append(
            Transport(
                "replication",
                "amps-replication",
                endpoint_placeholder(instance.name, policy.REPLICATION_ENDPOINT),
                None,
            )
        )
    return transports


def _actions(instance: ResolvedInstance) -> list[Action]:
    actions = [
        Action(
            ModuleCall("amps-action-on-startup", []),
            [ModuleCall("amps-action-do-truncate-statistics", [("Age", "5d")])],
        ),
        Action(
            ModuleCall("amps-action-on-shutdown", []),
            [ModuleCall("amps-action-do-remove-journal", [("Age", "10s")])],
        ),
    ]
    if instance.destinations or instance.queue_destinations:
        actions.append(
            Action(
                ModuleCall("amps-action-on-schedule", [("Every", "30s")]),
                [
                    ModuleCall(
                        "amps-action-do-downgrade-replication",
                        [("Age", "3m"), ("GracePeriod", "5m")],
                    ),
                    ModuleCall("amps-action-do-upgrade-replication", [("Age", "1m")]),
                ],
            )
        )
    return actions


class _ConfigBuilder:
    def __init__(self, system: ResolvedSystem, catalog: Catalog) -> None:
        self._catalog = catalog
        # Every declaration of a name shares its definition (the resolver
        # checks it), so definitions are looked up by name.
        self._definitions = {topic.name: topic.definition for topic in system.topics}
        self._queue_definitions = {queue.name: queue.definition for queue in system.queues}
        self._all_queues = system.queues
        # The queues of the instance being built: several declarations may
        # share a name, each on its own instances.
        self._queues: dict[str, ResolvedQueue] = {}
        # Topics leaving any instance over a non-mesh edge.
        self._distributed = {
            destination.topic
            for instance in system.instances
            for destination in instance.destinations
            if destination.kind is not EdgeKind.MESH
        }

    def check_topic_names(self, system: ResolvedSystem) -> None:
        """Every topic's name must fit its definition's name pattern, and so must
        the name of a queue reading the topic of its own name."""
        for topic in system.topics:
            check_topic_name(topic, self._entry_for(topic.name))
        for queue in system.queues:
            if isinstance(queue.source, QueueOwnName):
                own = ResolvedTopic(
                    queue.name, queue.name_template, queue.definition, queue.instances
                )
                check_topic_name(own, self._entry_for(queue.name))

    def build(self, instance: ResolvedInstance) -> AmpsConfig:
        self._queues = {q.name: q for q in self._all_queues if instance.name in q.instances}
        return AmpsConfig(
            name=instance.name,
            group=instance.group,
            admin=Admin(
                endpoint_placeholder(instance.name, policy.ADMIN_ENDPOINT), policy.SQL_TRANSPORT
            ),
            logging=Logging(policy.LOGGING_PROTOCOL, policy.LOGGING_LEVEL),
            transports=_transports(instance),
            transaction_log=self._transaction_log(instance),
            sow_topics=self._sow_topics(instance),
            queues=[self._queue(self._queues[name], instance.name) for name in instance.queues],
            replication=self._replication(instance),
            actions=_actions(instance),
        )

    def _transaction_log(self, instance: ResolvedInstance) -> TransactionLog | None:
        """Every replicated topic is journaled, on both ends of each edge. A
        queue reads the transaction log, so every queue held here is journaled
        with the topics it reads, replicated or not."""
        journaled = {d.topic for d in instance.destinations} | {i.topic for i in instance.incoming}
        for name in instance.queues:
            journaled.add(name)
            source = self._queues[name].source
            if isinstance(source, QueueTopics):
                journaled.update(source.sources)
        if not journaled:
            return None
        return TransactionLog(
            policy.JOURNAL_DIRECTORY,
            [TopicRef(topic, self._entry_for(topic).codec) for topic in sorted(journaled)],
        )

    def _sow_topics(self, instance: ResolvedInstance) -> list[SowTopic]:
        sows: list[SowTopic] = []
        for sow in instance.sows:
            entry = self._entry_for(sow.topic)
            sows.append(SowTopic(sow.topic, entry.codec, entry.wire_keys, sow.durability))
        return sows

    def _replication(self, instance: ResolvedInstance) -> list[ReplicationDestination]:
        destinations: list[ReplicationDestination] = []
        used_names: Counter[str] = Counter()
        for bucket in _coalesce(instance.destinations + instance.queue_destinations):
            # Usually named after the target group; disambiguate the rare case of
            # two member sets addressing the same group.
            group = bucket.first.target_group
            clashes = used_names[group]
            used_names[group] += 1
            # Mesh partners ack synchronously; a destination carrying only
            # distribution edges must not gate the sender's persisted acks.
            is_mesh = any(entry.kind is EdgeKind.MESH for entry in bucket.entries)
            destinations.append(
                ReplicationDestination(
                    name=group if clashes == 0 else f"{group}-{clashes + 1}",
                    group=group,
                    topics=self._destination_topics(bucket),
                    passthrough=_passthrough_regex(
                        sorted({group for entry in bucket.entries for group in entry.passthrough})
                    ),
                    sync_type=SyncType.SYNC if is_mesh else SyncType.ASYNC,
                    inet_addrs=[
                        endpoint_placeholder(member, policy.REPLICATION_ENDPOINT)
                        for member in bucket.first.target_members
                    ],
                )
            )
        return destinations

    def _destination_topics(self, bucket: _DestinationBucket) -> list[DestinationTopic]:
        """A destination's topics: each topic edge's topic, and what each queue
        edge replicates; a topic both carry appears once."""
        topics: list[DestinationTopic] = []
        for entry in bucket.entries:
            queue = self._queues.get(entry.topic)
            views = (
                [
                    DestinationTopic(
                        entry.topic,
                        self._entry_for(entry.topic).codec,
                        self._wire_filter(entry),
                        self._exclusions(entry),
                    )
                ]
                if queue is None
                else self._queue_destination_topics(entry, queue)
            )
            for view in views:
                existing = next((t for t in topics if t.name == view.name), None)
                if existing is None:
                    topics.append(view)
                elif existing.filter != view.filter:
                    raise GenerateError(
                        f"topic '{view.name}' is replicated to '{bucket.first.target_group}' with "
                        f"two different filters ({existing.filter or 'none'} and "
                        f"{view.filter or 'none'}), by its own topology and by a queue reading "
                        "it; AMPS takes one filter per topic and destination"
                    )
        return topics

    def _queue_destination_topics(
        self, destination: Destination, queue: ResolvedQueue
    ) -> list[DestinationTopic]:
        """What a queue edge replicates (lab F1-F3, F18): the queue's own topic,
        and each topic it reads, the edge's filter applying to the topics the
        messages enter through. The queue's entry keeps full validation (AMPS
        checks the queue downstream is the same queue); a source keeps what a
        topic edge of the same kind would."""
        entry = self._entry_for(queue.name)
        wire = (
            None
            if destination.filter is None
            else self._rewrite(destination.filter, entry, f"queue '{queue.name}': replicate filter")
        )
        if isinstance(queue.source, QueueOwnName):
            return [DestinationTopic(queue.name, entry.codec, wire, None)]
        return [DestinationTopic(queue.name, entry.codec, None, None)] + [
            DestinationTopic(
                source,
                entry.codec,
                wire,
                self._exclusions(replace(destination, topic=source)),
            )
            for source in queue.source.sources
        ]

    def _queue(self, queue: ResolvedQueue, instance: str) -> QueueConfig:
        """The queue element of `queue` on `instance`, with every decision made."""
        entry = self._entry_for(queue.name)
        underlying: str | None = None
        publish_target: str | None = None
        if isinstance(queue.source, QueueTopics):
            sources = queue.source.sources
            underlying = (
                sources[0]
                if len(sources) == 1
                else "^(" + "|".join(_regex_escape(s) for s in sources) + ")$"
            )
            publish_target = queue.source.publish_to
        element = "Queue"
        owner: str | None = None
        domain: str | None = None
        match queue.kind:
            case LocalQueue():
                element = "LocalQueue"
            case GroupLocalQueue(domains=domains):
                # The domain the instance belongs to, named after its owner:
                # always written, so the queue's identity never depends on group
                # names (lab F22). The resolver places every holder in exactly
                # one domain.
                held = next(d for d in domains if instance in d.members)
                element, owner, domain = "GroupLocalQueue", held.owner, held.owner
        semantics, settings = _delivery_settings(queue.delivery)
        settings += self._queue_settings(queue, entry)
        return QueueConfig(
            element=element,
            name=queue.name,
            message_type=entry.codec,
            underlying_topic=underlying,
            default_publish_target=publish_target,
            initial_owner=owner,
            domain=domain,
            filter=(
                None
                if queue.filter is None
                else self._rewrite(queue.filter, entry, f"queue '{queue.name}': filter")
            ),
            semantics=semantics,
            settings=settings,
        )

    def _queue_settings(self, queue: ResolvedQueue, entry: CatalogEntry) -> list[tuple[str, str]]:
        """The queue's other optional settings the `.topo` wrote, in a fixed
        order, with paths and filters in wire names."""
        settings: list[tuple[str, str]] = []
        match queue.ordering:
            case Priority(path):
                wire = entry.resolve_wire_path(path)
                if wire is None:
                    raise GenerateError(f"queue '{queue.name}': priority '{path}' names no field")
                settings.append(("Priority", f"/msg{wire}" if entry.routed else wire))
            case Barrier(text):
                barrier = self._rewrite(text, entry, f"queue '{queue.name}': barrier")
                settings.append(("BarrierExpression", barrier))
        if queue.recovery_point is not None:
            settings.append(("RecoveryPoint", queue.recovery_point.value))
        if queue.target_depth is not None:
            settings.append(("TargetQueueDepth", str(queue.target_depth)))
        if queue.file_backed_metadata:
            settings.append(("FileBackedMetadata", "enabled"))
        if queue.deferred_ack_expiration is not None:
            settings.append(("DeferredAckExpiration", str(queue.deferred_ack_expiration)))
        return settings

    @staticmethod
    def _rewrite(filter_text: str, entry: CatalogEntry, context: str) -> str:
        """A filter in field names, in the wire names AMPS matches on."""
        try:
            return rewrite(filter_text, entry.resolve_wire_path, routed=entry.routed)
        except FilterError as error:
            raise GenerateError(f"{context} '{filter_text}': {error}") from error

    def _exclusions(self, destination: Destination) -> str | None:
        if destination.kind is EdgeKind.REPLICATE:
            return policy.DISTRIBUTION_EXCLUSIONS
        if destination.kind is EdgeKind.TWO_WAY:
            return policy.TWO_WAY_EXCLUSIONS
        if destination.topic in self._distributed:
            return policy.MESH_EXCLUSIONS_FOR_DISTRIBUTED
        return None

    def _wire_filter(self, destination: Destination) -> str | None:
        """The edge's filter with field names rewritten to wire names (and
        `/msg`-prefixed for a routed topic)."""
        if destination.filter is None:
            return None
        entry = self._entry_for(destination.topic)
        try:
            return rewrite(destination.filter, entry.resolve_wire_path, routed=entry.routed)
        except FilterError as error:
            raise GenerateError(
                f"topic '{destination.topic}': replicate filter '{destination.filter}': {error}"
            ) from error

    def _entry_for(self, name: str) -> CatalogEntry:
        """The catalog entry behind a topic's or a queue's `definition`. A miss
        is an error: there is no fallback for a message type or keys."""
        queue_definition = self._queue_definitions.get(name)
        what, definition = (
            ("queue", queue_definition)
            if queue_definition is not None
            else ("topic", self._definitions[name])
        )
        entry = self._catalog.find(definition)
        if entry is None:
            raise GenerateError(f"{what} '{name}': no catalog entry for definition '{definition}'")
        return entry


def _passthrough_regex(groups: list[str]) -> str | None:
    """A `<PassThrough>` matching exactly `groups`: `^(A|Quoting\\.LN)$`. Group
    names are escaped (they may contain `.`) and the pattern is anchored, so a
    group never matches another whose name contains it. `None` when there is
    nothing to relay."""
    if not groups:
        return None
    return "^(" + "|".join(_regex_escape(group) for group in groups) + ")$"


def _regex_escape(text: str) -> str:
    return "".join("\\" + c if c in "\\.+*?()|[]{}^$" else c for c in text)


def _delivery_settings(delivery: Delivery) -> tuple[str, list[tuple[str, str]]]:
    """`Semantics`, and the delivery settings the `.topo` wrote, in a fixed order."""
    settings: list[tuple[str, str]] = []
    if isinstance(delivery, AtLeastOnce):
        semantics = "at-least-once"
        settings.append(("LeasePeriod", str(delivery.lease)))
        if delivery.leasing is not None:
            settings.append(("Leasing", delivery.leasing.value))
        if delivery.max_deliveries is not None:
            settings.append(("MaxDeliveries", str(delivery.max_deliveries)))
        if delivery.max_cancels is not None:
            settings.append(("MaxCancels", str(delivery.max_cancels)))
    else:
        semantics = "at-most-once"
    limits = delivery.limits
    if limits.backlog is not None:
        settings.append(("MaxBacklog", str(limits.backlog)))
    if limits.per_subscription_backlog is not None:
        settings.append(("MaxPerSubscriptionBacklog", str(limits.per_subscription_backlog)))
    if limits.expiration is not None:
        settings.append(("Expiration", str(limits.expiration)))
    if limits.expiration_model is not None:
        settings.append(("ExpirationModel", limits.expiration_model.value))
    if limits.fairness is not None:
        # AMPS spells it with a hyphen.
        settings.append(("FairnessModel", limits.fairness.value.replace("_", "-")))
    return semantics, settings
