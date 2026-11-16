"""Field names for a cluster's SOW keys and filters, from the topic catalog.

Methodology: the reference cluster with the reference catalog, where `refdata`
and `microprices` key on `symbol` (wire `s`), shows the mapping end to end;
each other rule — routed topics, nested fields, topics the catalog cannot place
— is pinned with a minimal configuration and a hand-written catalog entry.
"""

from helpers import TESTDATA

from topoc.catalog import Catalog, CatalogEntry, CatalogField
from topoc.catalog_json import parse_catalog_json
from topoc.view.cluster import Cluster, build_cluster, parse_instance, read_cluster
from topoc.view.field_names import with_field_names
from topoc.view.model_json import cluster_to_json


def reference() -> Cluster:
    catalog = parse_catalog_json((TESTDATA / "reference.catalog.json").read_text(encoding="utf-8"))
    cluster = read_cluster("reference", sorted((TESTDATA / "expected").glob("*.xml")))
    return with_field_names(cluster, catalog)


def entry(pattern: str, *, routed: bool = False, keys: list[str] | None = None) -> CatalogEntry:
    """An entry whose `book/venue` is `b/v` on the wire, beside `symbol` (`s`)."""
    fields = [
        CatalogField("symbol", "s", []),
        CatalogField("book", "b", [CatalogField("venue", "v", [])]),
    ]
    return CatalogEntry(f"defs::{pattern}", pattern, "json", routed, keys or [], fields)


def single(body: str, *entries: CatalogEntry) -> Cluster:
    """A one-instance cluster `A`, replicating to `B`, with `body` inside A."""
    a = parse_instance(f"<AMPSConfig><Name>A</Name>{body}</AMPSConfig>", "A.xml")
    b = parse_instance("<AMPSConfig><Name>B</Name></AMPSConfig>", "B.xml")
    return with_field_names(build_cluster("c", [a, b]), Catalog(entries))


def sow(topic: str, *keys: str) -> str:
    key_elements = "".join(f"<Key>{key}</Key>" for key in keys)
    return f"<SOW><Topic><Name>{topic}</Name>{key_elements}</Topic></SOW>"


def replicated(topic: str, filter_text: str) -> str:
    return (
        "<Replication><Destination><Group>B</Group><Topic>"
        f"<Name>{topic}</Name><Filter>{filter_text}</Filter>"
        "</Topic></Destination></Replication>"
    )


def test_reference_keys_and_filters_get_field_names() -> None:
    cluster = reference()
    assert cluster.field_names
    infra1 = {t.name: t for t in next(i for i in cluster.instances if i.name == "Infra1").topics}
    assert infra1["refdata"].sow is not None
    assert (infra1["refdata"].sow.keys, infra1["refdata"].sow.key_names) == (["/s"], ["/symbol"])
    to_london = next(e for e in cluster.edges if (e.source, e.target) == ("Infra1", "Quoting.LN"))
    tvgrids = next(t for t in to_london.destination.topics if t.name == "tvgrids")
    assert (tvgrids.filter, tvgrids.filter_names) == ("/owner LIKE 'LN/.*'",) * 2
    assert cluster.warnings == []


def test_without_a_catalog_names_are_absent() -> None:
    cluster = read_cluster("reference", sorted((TESTDATA / "expected").glob("*.xml")))
    document = cluster_to_json(cluster)
    assert document["fieldNames"] is False
    sows = [t["sow"] for i in document["instances"] for t in i["topics"] if t["sow"] is not None]
    assert all(s["keyNames"] is None for s in sows)


def test_a_topic_matches_its_entry_through_pattern_holes() -> None:
    cluster = single(sow("Grids/Native", "/b/v", "/s"), entry("Grids/{}"))
    sow_ = cluster.instances[0].topics[0].sow
    assert sow_ is not None
    assert sow_.key_names == ["/book/venue", "/symbol"]


def test_routed_topics_drop_the_envelope() -> None:
    body = sow("t", "/msg/s") + replicated("t", "/msg/b/v = 'X' AND /route = 'LN'")
    cluster = single(body, entry("t", routed=True))
    sow_ = cluster.instances[0].topics[0].sow
    assert sow_ is not None
    assert sow_.key_names == ["/symbol"]
    assert (
        cluster.edges[0].destination.topics[0].filter_names == "/book/venue = 'X' AND $route = 'LN'"
    )


def test_unplaceable_topics_keep_wire_names_with_a_warning() -> None:
    body = (
        sow("lost", "/s") + sow("twice/x", "/s") + sow("t", "/nope") + replicated("t", "/nope = 1")
    )
    cluster = single(body, entry("t"), entry("twice/x"), entry("twice/{}"))
    topics = {t.name: t for t in cluster.instances[0].topics}
    assert all(t.sow is not None and t.sow.key_names is None for t in topics.values())
    assert cluster.edges[0].destination.topics[0].filter_names is None
    assert cluster.warnings == [
        "topic 'lost' matches no catalog entry: its SOW keys and filters show wire names",
        "A: topic 't' has a SOW key (/nope) its catalog entry 'defs::t' does not describe: it "
        "shows wire names",
        "topic 'twice/x' matches several catalog entries (defs::twice/x, defs::twice/{}): "
        "its SOW keys and filters show wire names",
        "A: the filter on topic 't' to 'B' shows wire names: filter path '/nope' names no field",
    ]


def test_queue_filters_and_priority_get_field_names() -> None:
    body = sow("t", "/s") + (
        "<Queue><Name>q</Name><UnderlyingTopic>t</UnderlyingTopic>"
        "<Filter>/b/v = 'X'</Filter><Priority>/s</Priority>"
        "<BarrierExpression>/s = 'EOD'</BarrierExpression></Queue>"
    )
    body = body.replace("</SOW><Queue>", "<Queue>").replace("</Queue>", "</Queue></SOW>")
    cluster = single(body, entry("t"))
    [queue] = cluster.instances[0].queues
    assert queue.filter_names == "/book/venue = 'X'"
    assert queue.priority_names == "/symbol"
    assert queue.barrier_names == "/symbol = 'EOD'"
