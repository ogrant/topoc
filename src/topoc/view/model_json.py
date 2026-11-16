"""The JSON form of a cluster, as embedded in a topoview page.

The page's TypeScript types (`topoview/src/model.ts`) mirror this format; bump
`FORMAT_VERSION` on any incompatible change. Keys are camelCase, for the
page's sake. Configuration files are named, not located: the page is meant to
be shared, so it records file names only.
"""

from pathlib import PurePath
from typing import Any

from topoc.view.cluster import Cluster, Destination, Edge, HostedTopic, Instance, Queue, Sow

FORMAT_VERSION = 3

type Json = dict[str, Any]


def cluster_to_json(cluster: Cluster) -> Json:
    return {
        "formatVersion": FORMAT_VERSION,
        "name": cluster.name,
        "instances": [_instance(instance) for instance in cluster.instances],
        "groups": [{"name": group.name, "members": group.members} for group in cluster.groups],
        "edges": [_edge(edge) for edge in cluster.edges],
        "warnings": cluster.warnings,
        "fieldNames": cluster.field_names,
    }


def _instance(instance: Instance) -> Json:
    return {
        "name": instance.name,
        "group": instance.group,
        "source": PurePath(instance.source).name,
        "topics": [_hosted_topic(topic) for topic in instance.topics],
        "queues": [_queue(queue) for queue in instance.queues],
    }


def _hosted_topic(topic: HostedTopic) -> Json:
    return {
        "name": topic.name,
        "messageType": topic.message_type,
        "sow": None if topic.sow is None else _sow(topic.sow),
        "journaled": topic.journaled,
    }


def _queue(queue: Queue) -> Json:
    return {
        "name": queue.name,
        "kind": queue.kind.value,
        "messageType": queue.message_type,
        "underlyingTopic": queue.underlying_topic,
        "sources": queue.sources,
        "defaultPublishTarget": queue.default_publish_target,
        "initialOwner": queue.initial_owner,
        "domain": queue.domain,
        "semantics": queue.semantics,
        "filter": queue.filter,
        "filterNames": queue.filter_names,
        "priority": queue.priority,
        "priorityNames": queue.priority_names,
        "barrier": queue.barrier,
        "barrierNames": queue.barrier_names,
        "settings": [{"name": s.name, "value": s.value} for s in queue.settings],
    }


def _sow(sow: Sow) -> Json:
    return {
        "keys": sow.keys,
        "keyNames": sow.key_names,
        "durability": sow.durability,
        "file": sow.file,
        "recoveryPoint": sow.recovery_point,
    }


def _edge(edge: Edge) -> Json:
    return {
        "source": edge.source,
        "targetKind": edge.target_kind.value,
        "target": edge.target,
        "members": edge.members,
        "destination": _destination(edge.destination),
    }


def _destination(destination: Destination) -> Json:
    return {
        "name": destination.name,
        "group": destination.group,
        "syncType": destination.sync_type,
        "addresses": destination.addresses,
        "topics": [
            {
                "name": topic.name,
                "messageType": topic.message_type,
                "filter": topic.filter,
                "filterNames": topic.filter_names,
                "excludedValidation": topic.excluded_validation,
            }
            for topic in destination.topics
        ],
    }
