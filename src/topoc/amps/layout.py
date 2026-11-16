"""Lays an `AmpsConfig` out as its `<AMPSConfig>` element.

Pure layout: empty sections are omitted, and nothing else is decided here.
"""

from collections.abc import Callable
from xml.etree.ElementTree import Element, SubElement

from topoc.amps.config import (
    Action,
    AmpsConfig,
    DestinationTopic,
    ModuleCall,
    QueueConfig,
    ReplicationDestination,
    SowTopic,
    TopicRef,
    TransactionLog,
    Transport,
)
from topoc.model import PersistentDurability, TransientDurability


def to_xml(config: AmpsConfig) -> Element:
    root = Element("AMPSConfig")
    _leaf(root, "Name", config.name)
    _leaf(root, "Group", config.group)

    admin = SubElement(root, "Admin")
    _leaf(admin, "InetAddr", config.admin.inet_addr)
    _leaf(admin, "SQLTransport", config.admin.sql_transport)

    target = SubElement(SubElement(root, "Logging"), "Target")
    _leaf(target, "Protocol", config.logging.protocol)
    _leaf(target, "Level", config.logging.level)

    _section(root, "Transports", config.transports, _transport)
    if config.transaction_log is not None:
        _transaction_log(root, config.transaction_log)
    if config.sow_topics or config.queues:
        sow = SubElement(root, "SOW")
        for topic in config.sow_topics:
            _sow_topic(sow, topic)
        for queue in config.queues:
            _queue(sow, queue)
    _section(root, "Replication", config.replication, _destination)
    _section(root, "Actions", config.actions, _action)
    return root


def _leaf(parent: Element, name: str, text: str) -> None:
    """A text element `<name>text</name>` appended to `parent`."""
    SubElement(parent, name).text = text


def _optional_leaf(parent: Element, name: str, text: str | None) -> None:
    if text is not None:
        _leaf(parent, name, text)


def _section[T](
    parent: Element, name: str, items: list[T], append_item: Callable[[Element, T], None]
) -> None:
    """A container named `name` holding each item, or nothing when there are none."""
    if not items:
        return
    section = SubElement(parent, name)
    for item in items:
        append_item(section, item)


def _transport(parent: Element, transport: Transport) -> None:
    element = SubElement(parent, "Transport")
    _leaf(element, "Name", transport.name)
    _leaf(element, "Type", transport.transport_type)
    _leaf(element, "InetAddr", transport.inet_addr)
    _optional_leaf(element, "Protocol", transport.protocol)


def _topic_ref(parent: Element, topic: TopicRef) -> None:
    element = SubElement(parent, "Topic")
    _leaf(element, "Name", topic.name)
    _leaf(element, "MessageType", topic.message_type)


def _transaction_log(parent: Element, log: TransactionLog) -> None:
    element = SubElement(parent, "TransactionLog")
    _leaf(element, "JournalDirectory", log.journal_directory)
    for topic in log.topics:
        _topic_ref(element, topic)


def _sow_topic(parent: Element, topic: SowTopic) -> None:
    element = SubElement(parent, "Topic")
    _leaf(element, "Name", topic.name)
    _leaf(element, "MessageType", topic.message_type)
    for key in topic.keys:
        _leaf(element, "Key", key)
    match topic.durability:
        case PersistentDurability(file):
            _leaf(element, "Durability", "persistent")
            _leaf(element, "FileName", file)
        case TransientDurability(recovery_point):
            _leaf(element, "Durability", "transient")
            _leaf(element, "RecoveryPoint", recovery_point.value)


def _queue(parent: Element, queue: QueueConfig) -> None:
    """A queue: every optional setting is present only when the `.topo` wrote
    it, so AMPS's own default applies otherwise."""
    element = SubElement(parent, queue.element)
    _leaf(element, "Name", queue.name)
    _leaf(element, "MessageType", queue.message_type)
    _optional_leaf(element, "UnderlyingTopic", queue.underlying_topic)
    _optional_leaf(element, "DefaultPublishTarget", queue.default_publish_target)
    _optional_leaf(element, "InitialOwner", queue.initial_owner)
    _optional_leaf(element, "GroupLocalQueueDomain", queue.domain)
    _optional_leaf(element, "Filter", queue.filter)
    _leaf(element, "Semantics", queue.semantics)
    for name, value in queue.settings:
        _leaf(element, name, value)


def _destination_topic(parent: Element, topic: DestinationTopic) -> None:
    element = SubElement(parent, "Topic")
    _leaf(element, "Name", topic.name)
    _leaf(element, "MessageType", topic.message_type)
    _optional_leaf(element, "Filter", topic.filter)
    _optional_leaf(element, "ExcludeValidation", topic.exclude_validation)


def _destination(parent: Element, destination: ReplicationDestination) -> None:
    element = SubElement(parent, "Destination")
    _leaf(element, "Name", destination.name)
    _leaf(element, "Group", destination.group)
    for topic in destination.topics:
        _destination_topic(element, topic)
    _optional_leaf(element, "PassThrough", destination.passthrough)
    _leaf(element, "SyncType", destination.sync_type.value)
    transport = SubElement(element, "Transport")
    _leaf(transport, "Type", "amps-replication")
    for inet_addr in destination.inet_addrs:
        _leaf(transport, "InetAddr", inet_addr)


def _module_call(parent: Element, call: ModuleCall) -> None:
    """`<Module>` followed by `<Options>` when the call has any."""
    _leaf(parent, "Module", call.module)
    if call.options:
        options = SubElement(parent, "Options")
        for name, value in call.options:
            _leaf(options, name, value)


def _action(parent: Element, action: Action) -> None:
    element = SubElement(parent, "Action")
    _module_call(SubElement(element, "On"), action.on)
    for command in action.commands:
        _module_call(SubElement(element, "Do"), command)
