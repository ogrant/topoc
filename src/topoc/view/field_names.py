"""Field names for a cluster's SOW keys and filters.

An AMPS configuration writes them with wire paths (`/s`), the names AMPS
matches on; people think in the message's field names (`/symbol`). Given the
topic catalog the configurations were generated from, each topic is matched to
its entry by the entry's name pattern, and its keys and filters are mapped back
through the entry's field tree. The wire forms are kept beside the names.

A topic matching no entry, or several, and a path the field tree does not
cover, keep their wire form and add a warning.
"""

from dataclasses import replace

from topoc.catalog import Catalog, CatalogEntry
from topoc.filters import MSG_FIELD, FilterError, rewrite_to_names
from topoc.view.cluster import (
    Cluster,
    Destination,
    Edge,
    HostedTopic,
    Instance,
    Queue,
    ReplicatedTopic,
)


def with_field_names(cluster: Cluster, catalog: Catalog) -> Cluster:
    """`cluster` with its SOW keys and filters also in field names."""
    namer = _Namer(catalog)
    instances = [
        replace(
            i,
            topics=[namer.hosted(i, t) for t in i.topics],
            queues=[namer.queue(i, q) for q in i.queues],
        )
        for i in cluster.instances
    ]
    edges = [replace(e, destination=namer.destination(e)) for e in cluster.edges]
    return replace(
        cluster,
        instances=instances,
        edges=edges,
        warnings=[*cluster.warnings, *namer.warnings],
        field_names=True,
    )


class _Namer:
    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog
        self._entries: dict[str, CatalogEntry | None] = {}
        self.warnings: list[str] = []

    def entry(self, topic: str) -> CatalogEntry | None:
        """The one entry `topic` matches, warning once per topic otherwise."""
        if topic not in self._entries:
            matching = self._catalog.entries_matching(topic)
            if len(matching) == 1:
                self._entries[topic] = matching[0]
            else:
                self._entries[topic] = None
                if matching:
                    definitions = ", ".join(entry.definition for entry in matching)
                    reason = f"matches several catalog entries ({definitions})"
                else:
                    reason = "matches no catalog entry"
                self.warnings.append(
                    f"topic '{topic}' {reason}: its SOW keys and filters show wire names"
                )
        return self._entries[topic]

    def queue(self, instance: Instance, queue: Queue) -> Queue:
        """`queue` with its filter, barrier and priority also in field names,
        through the catalog entry of the topic it reads (its own name, or its
        first source)."""
        if not queue.sources:
            return queue
        entry = self.entry(queue.sources[0])
        if entry is None:
            return queue

        def names(text: str | None, what: str) -> str | None:
            if text is None:
                return None
            try:
                return rewrite_to_names(text, entry.resolve_field_path, routed=entry.routed)
            except FilterError as error:
                self.warnings.append(
                    f"{instance.name}: the {what} of queue '{queue.name}' shows wire names: {error}"
                )
                return None

        priority_names = None
        if queue.priority is not None:
            priority_names = _key_name(entry, queue.priority)
            if priority_names is None:
                self.warnings.append(
                    f"{instance.name}: the priority of queue '{queue.name}' ({queue.priority}) is "
                    f"not a field of '{entry.definition}': it shows wire names"
                )
        return replace(
            queue,
            filter_names=names(queue.filter, "filter"),
            barrier_names=names(queue.barrier, "barrier"),
            priority_names=priority_names,
        )

    def hosted(self, instance: Instance, topic: HostedTopic) -> HostedTopic:
        if topic.sow is None or not topic.sow.keys:
            return topic
        entry = self.entry(topic.name)
        if entry is None:
            return topic
        names = [_key_name(entry, key) for key in topic.sow.keys]
        if any(name is None for name in names):
            self.warnings.append(
                f"{instance.name}: topic '{topic.name}' has a SOW key "
                f"({', '.join(topic.sow.keys)}) its catalog entry '{entry.definition}' "
                "does not describe: it shows wire names"
            )
            return topic
        return replace(topic, sow=replace(topic.sow, key_names=[n for n in names if n is not None]))

    def destination(self, edge: Edge) -> Destination:
        topics = [self.replicated(edge, topic) for topic in edge.destination.topics]
        return replace(edge.destination, topics=topics)

    def replicated(self, edge: Edge, topic: ReplicatedTopic) -> ReplicatedTopic:
        if topic.filter is None:
            return topic
        entry = self.entry(topic.name)
        if entry is None:
            return topic
        try:
            names = rewrite_to_names(topic.filter, entry.resolve_field_path, routed=entry.routed)
        except FilterError as error:
            self.warnings.append(
                f"{edge.source}: the filter on topic '{topic.name}' to '{edge.target}' shows "
                f"wire names: {error}"
            )
            return topic
        return replace(topic, filter_names=names)


def _key_name(entry: CatalogEntry, key: str) -> str | None:
    """A key's wire path in field names: on a routed topic, message fields live
    under `/msg`, which the name drops."""
    prefix = f"/{MSG_FIELD}/"
    if entry.routed:
        if not key.startswith(prefix):
            return None
        key = key[len(prefix) - 1 :]
    return entry.resolve_field_path(key)
