"""Reads a cluster from its AMPS XML configurations: instances and groups, the
topics each configuration reveals (SOW, journal, replication), replication
edges resolved from each destination's group, warnings for oddities, errors for
files that are not a cluster, and the model's JSON form.

Methodology: the reference cluster is read from `testdata/expected/`, which are
real AMPS configurations, and spot-checked; each rule is then pinned with a
minimal hand-written configuration built by `config`, which keeps only the
elements the rule is about.
"""

from pathlib import Path

import pytest
from helpers import TESTDATA

from topoc.errors import ClusterError
from topoc.view.cluster import (
    Cluster,
    Edge,
    HostedTopic,
    QueueKind,
    ReplicatedTopic,
    Sow,
    TargetKind,
    build_cluster,
    parse_instance,
    read_cluster,
)
from topoc.view.model_json import FORMAT_VERSION, cluster_to_json


def reference() -> Cluster:
    return read_cluster("reference", sorted((TESTDATA / "expected").glob("*.xml")))


def config(name: str, body: str = "", group: str | None = None) -> str:
    """A minimal AMPS configuration for instance `name`, with `body` inside."""
    group_element = "" if group is None else f"<Group>{group}</Group>"
    return f"<AMPSConfig><Name>{name}</Name>{group_element}{body}</AMPSConfig>"


def destination(group: str, topics: str = "<Topic><Name>t</Name></Topic>") -> str:
    return f"<Replication><Destination><Group>{group}</Group>{topics}</Destination></Replication>"


def cluster(*configs: str) -> Cluster:
    return build_cluster(
        "test", [parse_instance(text, f"{ix}.xml") for ix, text in enumerate(configs)]
    )


def edges(built: Cluster) -> list[tuple[str, str, str]]:
    return [(e.source, e.target_kind.value, e.target) for e in built.edges]


def test_reads_the_reference_cluster() -> None:
    """Instances and groups by name; a mesh lands member to member, distribution
    to a single instance, and a quoter's failover list on the Infra group."""
    built = reference()
    assert [i.name for i in built.instances] == [
        "Admin",
        "Infra1",
        "Infra2",
        "Quoting.LN",
        "Quoting.NY",
    ]
    assert [(g.name, g.members) for g in built.groups] == [
        ("Admin", ["Admin"]),
        ("Infra", ["Infra1", "Infra2"]),
        ("Quoting.LN", ["Quoting.LN"]),
        ("Quoting.NY", ["Quoting.NY"]),
    ]
    assert edges(built) == [
        ("Infra1", "instance", "Infra2"),
        ("Infra1", "instance", "Quoting.LN"),
        ("Infra1", "instance", "Quoting.NY"),
        ("Infra2", "instance", "Infra1"),
        ("Infra2", "instance", "Quoting.LN"),
        ("Infra2", "instance", "Quoting.NY"),
        ("Quoting.LN", "group", "Infra"),
        ("Quoting.NY", "group", "Infra"),
    ]
    to_infra = built.edges[6]
    assert to_infra.members == ["Infra1", "Infra2"]
    assert to_infra.destination.sync_type == "async"
    assert to_infra.destination.addresses == [
        "${INFRA1_REPLICATION_INETADDR}",
        "${INFRA2_REPLICATION_INETADDR}",
    ]
    assert built.warnings == []


def test_reveals_topics_from_sow_journal_and_replication() -> None:
    """A topic's SOW (persistent with a file, transient with a recovery point)
    and journaling; a pure pub/sub topic leaves no trace, so `Admin`, which only
    hosts one, shows none."""
    instances = {i.name: i for i in reference().instances}
    london = {t.name: t for t in instances["Quoting.LN"].topics}
    assert london["quotes"] == HostedTopic(
        "quotes", "msgpack", Sow(["/isin"], None, "transient", None, "now"), journaled=True
    )
    assert london["tvgrids"] == HostedTopic("tvgrids", "msgpack", None, journaled=True)
    infra = {t.name: t for t in instances["Infra1"].topics}
    assert infra["refdata"].sow == Sow(["/s"], None, "persistent", "${AMPS_DIR}/sow/%n.sow", None)
    assert infra["signals/momentum"].message_type == "json"
    assert instances["Admin"].topics == []


def test_keeps_replication_settings_per_topic() -> None:
    """Each replicated topic keeps its filter and excluded validation checks."""
    to_london = next(
        e for e in reference().edges if (e.source, e.target) == ("Infra1", "Quoting.LN")
    )
    assert to_london.destination.topics[2] == ReplicatedTopic(
        "tvgrids", "msgpack", "/owner LIKE 'LN/.*'", None, ["cascade", "replicate", "sow"]
    )


def test_an_instance_without_a_group_is_its_own_group() -> None:
    """As in AMPS, a configuration without `<Group>` forms a singleton group."""
    instance = parse_instance(config("A"), "a.xml")
    assert instance.group == "A"


def test_replicated_topics_are_hosted_even_without_sow_or_journal() -> None:
    """A topic an instance replicates out is on that instance."""
    instance = parse_instance(
        config(
            "A", destination("B", "<Topic><Name>t</Name><MessageType>json</MessageType></Topic>")
        ),
        "a.xml",
    )
    assert instance.topics == [HostedTopic("t", "json", None, journaled=False)]
    assert instance.destinations[0].sync_type is None


def test_a_destination_to_an_unknown_group_is_external() -> None:
    """A group no configuration declares becomes an external target, kept rather
    than dropped."""
    built = cluster(config("A", destination("Elsewhere")))
    assert built.edges == [
        Edge("A", TargetKind.EXTERNAL, "Elsewhere", [], built.instances[0].destinations[0])
    ]


def test_a_destination_reaching_no_other_instance_is_a_warning() -> None:
    """A destination to the instance's own singleton group lands nowhere: a
    warning, not an edge."""
    built = cluster(config("A", destination("A")))
    assert built.edges == []
    assert built.warnings == ["A: destination to group 'A' reaches no other instance"]


def test_conflicting_message_types_are_a_warning() -> None:
    """A topic given two message types in one configuration is flagged."""
    built = cluster(
        config(
            "A",
            "<TransactionLog><Topic><Name>t</Name><MessageType>json</MessageType></Topic>"
            "</TransactionLog>"
            + destination("B", "<Topic><Name>t</Name><MessageType>nvfix</MessageType></Topic>"),
        ),
        config("B"),
    )
    assert built.warnings == ["A: topic 't' has several message types (json, nvfix)"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("<AMPSConfig>", "a.xml: invalid XML: "),
        ("<Config/>", "a.xml: not an AMPS configuration (root element <Config>)"),
        ("<AMPSConfig><Group>G</Group></AMPSConfig>", "a.xml: the configuration has no <Name>"),
        (
            "<AMPSConfig><Name>A</Name><Replication><Destination/></Replication></AMPSConfig>",
            "a.xml: a <Destination> has no <Group>",
        ),
    ],
)
def test_rejects_files_that_are_not_configurations(text: str, message: str) -> None:
    """A file that is not an AMPS configuration, or lacks what identifies an
    instance or a destination, stops the read, naming the file."""
    with pytest.raises(ClusterError) as excinfo:
        parse_instance(text, "a.xml")
    assert str(excinfo.value).startswith(message)


def test_rejects_two_files_declaring_one_instance() -> None:
    with pytest.raises(ClusterError) as excinfo:
        cluster(config("A"), config("A"))
    assert str(excinfo.value) == "1.xml: instance 'A' is already declared by 0.xml"


def test_reports_unreadable_files(tmp_path: Path) -> None:
    missing = tmp_path / "missing.xml"
    with pytest.raises(ClusterError) as excinfo:
        read_cluster("test", [missing])
    assert str(excinfo.value).startswith(f"{missing}: cannot open file")


def test_json_form_names_files_without_their_location() -> None:
    """The embedded form is versioned, camelCase, and records configuration
    file names only, since the page is meant to be shared."""
    document = cluster_to_json(reference())
    assert document["formatVersion"] == FORMAT_VERSION
    assert document["name"] == "reference"
    london = document["instances"][3]
    assert london["source"] == "Quoting.LN.xml"
    assert london["topics"][2] == {
        "name": "quotes",
        "messageType": "msgpack",
        "sow": {
            "keys": ["/isin"],
            "keyNames": None,
            "durability": "transient",
            "file": None,
            "recoveryPoint": "now",
        },
        "journaled": True,
    }
    to_infra = document["edges"][6]
    assert {k: to_infra[k] for k in ("source", "targetKind", "target", "members")} == {
        "source": "Quoting.LN",
        "targetKind": "group",
        "target": "Infra",
        "members": ["Infra1", "Infra2"],
    }
    assert to_infra["destination"]["topics"][0]["excludedValidation"] == [
        "cascade",
        "replicate",
        "sow",
    ]


# ---- queues -------------------------------------------------------------------


def test_reads_the_reference_queues_apart_from_its_topics() -> None:
    """A queue's own topic (journaled, replicated) is the queue, not a topic;
    a pattern in `UnderlyingTopic` is matched against the instance's topics."""
    instances = {i.name: i for i in reference().instances}
    [orders] = instances["Infra1"].queues
    assert (orders.name, orders.kind, orders.sources) == (
        "work/orders",
        QueueKind.DISTRIBUTED,
        ["orders/LN", "orders/NY"],
    )
    assert orders.underlying_topic == "^(orders/LN|orders/NY)$"
    assert orders.default_publish_target == "orders/LN"
    assert orders.semantics == "at-least-once"
    assert [(s.name, s.value) for s in orders.settings] == [
        ("LeasePeriod", "60s"),
        ("MaxPerSubscriptionBacklog", "10"),
    ]
    assert "work/orders" not in {t.name for t in instances["Infra1"].topics}
    [quotes] = instances["Quoting.LN"].queues
    assert (quotes.kind, quotes.sources) == (QueueKind.LOCAL, ["quotes"])


def test_a_queue_without_an_underlying_topic_reads_its_own_name() -> None:
    instance = parse_instance(
        config(
            "A",
            "<TransactionLog><Topic><Name>jobs</Name></Topic></TransactionLog>"
            "<SOW><GroupLocalQueue><Name>jobs</Name><InitialOwner>A</InitialOwner>"
            "<GroupLocalQueueDomain>D</GroupLocalQueueDomain></GroupLocalQueue></SOW>",
        ),
        "a.xml",
    )
    [jobs] = instance.queues
    assert (jobs.kind, jobs.underlying_topic, jobs.sources) == (
        QueueKind.GROUP_LOCAL,
        None,
        ["jobs"],
    )
    assert (jobs.initial_owner, jobs.domain) == ("A", "D")
    assert instance.topics == []


def test_warns_when_a_queue_pattern_matches_no_topic() -> None:
    instance = parse_instance(
        config(
            "A",
            "<SOW><Queue><Name>q</Name><UnderlyingTopic>^nope/.*</UnderlyingTopic></Queue></SOW>",
        ),
        "a.xml",
    )
    cluster = build_cluster("c", [instance])
    assert cluster.warnings == [
        "A: queue 'q' reads '^nope/.*', which matches no topic of the instance"
    ]
