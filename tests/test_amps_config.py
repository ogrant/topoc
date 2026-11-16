"""Unit tests for AMPS configuration generation: the decisions made by
`build_configs` and how `generate` lays them out.

Methodology: each test compiles a tiny topology (usually two or three instances
and one topic `t` defined as `m::M`) with a catalog tailored to the behaviour
under test. It then searches one instance's generated XML for the element(s)
that behaviour produces, and where relevant asserts that some other element is
absent. Decisions not visible as a single element are checked on the
`AmpsConfig` model instead, and error paths assert the `GenerateError` message.
The golden test covers complete documents; these tests pin individual rules.
"""

import pytest

from topoc.amps.builder import _passthrough_regex, _regex_escape, build_configs
from topoc.amps.generator import generate
from topoc.amps.naming import endpoint_placeholder, env_var_name
from topoc.catalog import Catalog, CatalogEntry, CatalogField
from topoc.errors import GenerateError
from topoc.resolve import compile_topology


def entry(fields: list[CatalogField], *, routed: bool, wire_keys: list[str]) -> CatalogEntry:
    """A catalog entry for the `definition m::M` test sources, whose topic is
    named `t`."""
    return CatalogEntry("m::M", "t", "msgpack", routed, wire_keys, fields)


def leaf(name: str, wire: str) -> CatalogField:
    """A scalar field named `name`, serialized as `wire`."""
    return CatalogField(name, wire, [])


# A single plain `m::M` entry with no keys or fields.
PLAIN = Catalog([entry([], routed=False, wire_keys=[])])

# A single filtered distribution edge S -> A on topic `t`.
FILTERED_EDGE = """instance S { } instance A { }
    topic t { definition m::M topology { replicate S -> A where "/p < 10 AND /q > 2" } }"""


def config_for(source: str, instance: str, catalog: Catalog) -> str:
    """The generated XML of `instance` for `source`, using `catalog`."""
    files = {f.name: f.content for f in generate(compile_topology(source, None), catalog)}
    return files[f"{instance}.xml"]


def generate_error(source: str, catalog: Catalog) -> str:
    """Message of the `GenerateError` that generating `source` must raise."""
    with pytest.raises(GenerateError) as excinfo:
        generate(compile_topology(source, None), catalog)
    return str(excinfo.value)


def test_resolves_sow_keys_to_wire_paths_via_catalog() -> None:
    """SOW `<Key>`s come from the catalog's wire keys, not from the topology."""
    config = config_for(
        """instance A { } topic t { definition m::M topology { local A
           sow A { durability persistent { file "./t.sow" } } } }""",
        "A",
        Catalog([entry([], routed=False, wire_keys=["/s"])]),
    )
    assert "<Key>/s</Key>" in config


def test_errors_when_topic_missing_from_catalog() -> None:
    """A topic whose definition is not in the catalog fails generation (there is
    no fallback message type), with a message naming the topic and the
    definition."""
    assert generate_error(
        "instance A { } instance B { } topic t { definition m::M topology { replicate A -> B } }",
        Catalog([]),
    ) == ("generating configuration: topic 't': no catalog entry for definition 'm::M'")


def test_escapes_xml_significant_characters_in_filters() -> None:
    """`<` and `>` in a filter are escaped in the XML. The field map is the
    identity, so only escaping is in play."""
    catalog = Catalog([entry([leaf("p", "p"), leaf("q", "q")], routed=False, wire_keys=[])])
    assert "<Filter>/p &lt; 10 AND /q &gt; 2</Filter>" in config_for(FILTERED_EDGE, "S", catalog)


def test_quotes_in_filters_survive_as_literal_characters() -> None:
    """Single quotes, and the `/.*` inside a filter literal, reach the XML
    unchanged."""
    config = config_for(
        """instance S { } instance A { }
           topic t { definition m::M topology { replicate S -> A where "/owner LIKE 'LN/.*'" } }""",
        "S",
        Catalog([entry([leaf("owner", "owner")], routed=False, wire_keys=[])]),
    )
    assert "<Filter>/owner LIKE 'LN/.*'</Filter>" in config


SYMBOL_FILTER = """instance S { } instance A { }
    topic t { definition m::M topology { replicate S -> A where "/symbol > 100" } }"""


def test_rewrites_replicate_filter_source_names_to_wire() -> None:
    """Replicate filters are written in field names and emitted in wire
    names (`symbol` -> `s`)."""
    catalog = Catalog([entry([leaf("symbol", "s")], routed=False, wire_keys=[])])
    assert "<Filter>/s &gt; 100</Filter>" in config_for(SYMBOL_FILTER, "S", catalog)


def test_routed_replicate_filter_is_msg_prefixed() -> None:
    """For a routed topic the rewritten filter path also gets the `/msg` envelope
    prefix."""
    catalog = Catalog([entry([leaf("symbol", "s")], routed=True, wire_keys=[])])
    assert "<Filter>/msg/s &gt; 100</Filter>" in config_for(SYMBOL_FILTER, "S", catalog)


def test_routed_replicate_filter_can_match_the_route() -> None:
    """On a routed topic, `$route` reaches the envelope's route (`/route`, not
    `/msg`-prefixed), and per-target `{{Role.attr}}` interpolation still
    applies."""
    config = config_for(
        """role Q { attr key: string }
           instance S { } instance A { roles [Q] attr Q.key "LN" }
           topic t { definition m::M
             topology { replicate S -> Q where "$route LIKE '^{{Q.key}}/' AND /symbol > 1" } }""",
        "S",
        Catalog([entry([leaf("symbol", "s")], routed=True, wire_keys=[])]),
    )
    assert "<Filter>/route LIKE '^LN/' AND /msg/s &gt; 1</Filter>" in config


def test_errors_on_route_in_unrouted_replicate_filter() -> None:
    """An unrouted topic has no envelope, so `$route` in its replicate filter
    fails generation."""
    assert "'$route' is only valid on a routed topic" in generate_error(
        """instance S { } instance A { }
           topic t { definition m::M topology { replicate S -> A where "$route = 'LN/A'" } }""",
        Catalog([entry([leaf("symbol", "s")], routed=False, wire_keys=[])]),
    )


# ---- topic names against their definition's name pattern -------------------

# A topology with one quoting instance (key `LN`, attribute `x` = `A`), for name checks.
QUOTER = (
    "role Q { attr key: string attr x: string } "
    'instance S { roles [Q] attr Q.key "LN" attr Q.x "A" } '
)


def written(name: str) -> str:
    """A hand-written topic called `name`, defined by `m::M`."""
    return QUOTER + f'topic "{name}" {{ definition m::M topology {{ local S }} }}'


def templated(name: str) -> str:
    """A topic template called `name`, iterating over role Q, defined by `m::M`."""
    return QUOTER + f'for q in Q {{ topic "{name}" {{ definition m::M topology {{ local q }} }} }}'


def name_check(source: str, pattern: str) -> str:
    """Generates `source` with `m::M` named by `pattern`; returns the error, or ""
    on success."""
    catalog = Catalog([CatalogEntry("m::M", pattern, "msgpack", False, [], [])])
    try:
        generate(compile_topology(source, None), catalog)
    except GenerateError as error:
        return str(error)
    return ""


def test_static_definitions_require_their_exact_name() -> None:
    """A static definition's pattern has no holes: its topic must carry exactly
    its name."""
    assert name_check(written("quotes"), "quotes") == ""
    assert name_check(written("quote"), "quotes") == (
        "generating configuration: topic 'quote' does not match 'quotes', the name pattern "
        "of its definition 'm::M'"
    )


@pytest.mark.parametrize("name", ["orders/", "orders", "orders/LN/A", "order/LN"])
def test_holes_accept_one_non_empty_segment_written_by_hand(name: str) -> None:
    """A hole accepts one non-empty segment, which may be written by hand."""
    assert name_check(written("orders/LN"), "orders/{}") == ""
    assert "does not match" in name_check(written(name), "orders/{}")


@pytest.mark.parametrize(
    ("name", "pattern"),
    [
        ("orders/{{q.Q.key}}", "orders/{}"),
        ("orders/{{q.Q.key}}/A", "orders/{}/{}"),
        ("orders/{{q.Q.key}}-{{q}}", "orders/{}"),
    ],
)
def test_interpolation_may_fill_holes(name: str, pattern: str) -> None:
    """Interpolation may fill holes, alongside holes filled literally; several
    placeholders may share one segment, which is one hole."""
    assert name_check(templated(name), pattern) == ""


def test_interpolation_where_the_pattern_expects_a_constant_is_rejected() -> None:
    """Interpolation where the pattern expects a constant is rejected, even when
    the value happens to equal the constant."""
    assert name_check(templated("orders/{{q.Q.key}}/{{q.Q.x}}"), "orders/{}/A") == (
        "generating configuration: topic 'orders/{{q.Q.key}}/{{q.Q.x}}' interpolates "
        "'{{q.Q.x}}' where 'orders/{}/A', the name pattern of its definition 'm::M', "
        "expects the constant 'A'"
    )


def test_templates_cannot_use_a_static_definition() -> None:
    """A template produces several names, so it cannot use a static definition's
    single name."""
    assert "topic 'orders/S' (from 'orders/{{q}}') does not match 'orders'" in name_check(
        templated("orders/{{q}}"), "orders"
    )


def test_errors_on_replicate_filter_unknown_field() -> None:
    """A filter naming a field absent from the catalog fails generation, naming
    both the filter and the field."""
    message = generate_error(
        """instance S { } instance A { }
           topic t { definition m::M topology { replicate S -> A where "/nope > 1" } }""",
        Catalog([entry([leaf("symbol", "s")], routed=False, wire_keys=[])]),
    )
    assert "replicate filter" in message
    assert "unknown field '/nope'" in message


def test_distribution_edges_exclude_failover_validation_and_replicate_async() -> None:
    """A `replicate` edge, on the sender: async, with the failover-partner checks
    excluded. On the receiver: no replication section (edges are one-way), but
    the received topic is journaled."""
    source = (
        "instance S { } instance A { } topic t { definition m::M topology { replicate S -> A } }"
    )
    sender = config_for(source, "S", PLAIN)
    assert "<ExcludeValidation>cascade,replicate,sow</ExcludeValidation>" in sender
    assert "<SyncType>async</SyncType>" in sender

    target = config_for(source, "A", PLAIN)
    assert "<Replication>" not in target
    assert "<TransactionLog>" in target


@pytest.mark.parametrize(("instance", "opened"), [("S", False), ("A", True), ("B", False)])
def test_opens_the_replication_transport_only_on_instances_that_receive(
    instance: str, opened: bool
) -> None:
    """The replication transport only accepts incoming replication, so only an
    instance that receives some opens it: S sends on its destination's own
    transport, and B takes no part in replication."""
    source = (
        "instance S { } instance A { } instance B { } "
        "topic t { definition m::M topology { replicate S -> A local B } }"
    )
    assert ("<Name>replication</Name>" in config_for(source, instance, PLAIN)) == opened


def test_mesh_edges_waive_cascade_for_topics_distributed_onward() -> None:
    """A topic both meshed in G and distributed to V: on A, the mesh destination
    waives only `cascade` and the distribution destination excludes the full
    set; exactly one of each."""
    config = config_for(
        "instance A { group G } instance B { group G } instance V { } "
        "topic t { definition m::M topology { mesh G replicate G -> V } }",
        "A",
        PLAIN,
    )
    assert config.count("<ExcludeValidation>cascade</ExcludeValidation>") == 1
    assert config.count("<ExcludeValidation>cascade,replicate,sow</ExcludeValidation>") == 1


def test_two_way_edges_keep_the_replicate_check_and_replicate_async() -> None:
    """Both ends of `G <-> V` exclude only `cascade,sow`, async; the mesh still
    waives `cascade` for a topic leaving over a two-way edge."""
    source = (
        "instance A { group G } instance B { group G } instance V { } "
        "topic t { definition m::M topology { mesh G replicate G <-> V } }"
    )
    for name in ["A", "V"]:
        config = config_for(source, name, PLAIN)
        assert "<ExcludeValidation>cascade,sow</ExcludeValidation>" in config, name
        assert "<SyncType>async</SyncType>" in config, name
    assert (
        config_for(source, "A", PLAIN).count("<ExcludeValidation>cascade</ExcludeValidation>") == 1
    )


def test_a_filtered_two_way_edge_rewrites_the_filter_on_both_sides() -> None:
    source = (
        "instance A { } instance B { } "
        'topic t { definition m::M topology { replicate A <-> B where "/qty > 1" } }'
    )
    catalog = Catalog([entry([leaf("qty", "q")], routed=False, wire_keys=[])])
    for name in ["A", "B"]:
        config = config_for(source, name, catalog)
        assert "<Filter>/q &gt; 1</Filter>" in config, name
        assert "<ExcludeValidation>cascade,sow</ExcludeValidation>" in config, name


def test_relays_a_failover_delivery_inside_the_mesh_as_an_escaped_passthrough() -> None:
    source = (
        "instance A { group G } instance B { group G } instance Quoting.LN { } "
        "topic t { definition m::M topology { mesh G replicate Quoting.LN -> G } }"
    )
    assert "<PassThrough>^(Quoting\\.LN)$</PassThrough>" in config_for(source, "A", PLAIN)
    assert "<PassThrough>" not in config_for(source, "Quoting.LN", PLAIN)


def test_a_destination_passes_the_groups_of_all_its_topics() -> None:
    catalog = Catalog(
        [
            CatalogEntry("m::M1", "t1", "msgpack", False, [], []),
            CatalogEntry("m::M2", "t2", "msgpack", False, [], []),
        ]
    )
    config = config_for(
        "instance A { group G } instance B { group G } instance X { } instance Y { } "
        "topic t1 { definition m::M1 topology { mesh G replicate Y -> G } } "
        "topic t2 { definition m::M2 topology { mesh G replicate X -> G } }",
        "A",
        catalog,
    )
    assert config.count("<PassThrough>") == 1
    assert "<PassThrough>^(X|Y)$</PassThrough>" in config


def test_escapes_every_regex_metacharacter_in_group_names() -> None:
    assert _regex_escape("a.b+c*d?e(f)g|h[i]j{k}l^m$n\\o") == (
        "a\\.b\\+c\\*d\\?e\\(f\\)g\\|h\\[i\\]j\\{k\\}l\\^m\\$n\\\\o"
    )
    assert _passthrough_regex([]) is None


def test_mesh_edges_stay_sync_and_fully_validated() -> None:
    """A topic that is only meshed: the partner destination is sync, with no
    validation exclusions."""
    config = config_for(
        "instance A { group G } instance B { group G } "
        "topic t { definition m::M topology { mesh G } }",
        "A",
        PLAIN,
    )
    assert "<SyncType>sync</SyncType>" in config
    assert "<ExcludeValidation>" not in config


def test_destinations_to_the_same_group_with_different_members_are_disambiguated() -> None:
    """A three-member mesh gives A one destination per peer, both in group G. The
    second is named `G-2` so destination names stay unique, and each carries its
    own peer's address. Checked on the model, where names and addresses are
    explicit."""
    configs = build_configs(
        compile_topology(
            "instance A { group G } instance B { group G } instance C { group G } "
            "topic t { definition m::M topology { mesh G } }",
            None,
        ),
        PLAIN,
    )
    assert [(d.name, d.inet_addrs) for d in configs[0].replication] == [
        ("G", ["${B_REPLICATION_INETADDR}"]),
        ("G-2", ["${C_REPLICATION_INETADDR}"]),
    ]


def test_only_replicating_instances_get_the_replication_health_action() -> None:
    """Every instance gets the startup and shutdown actions; only an instance with
    outgoing destinations (S, not A) also gets the scheduled replication-health
    action."""
    configs = build_configs(
        compile_topology(
            "instance S { } instance A { } "
            "topic t { definition m::M topology { replicate S -> A } }",
            None,
        ),
        PLAIN,
    )
    assert [len(c.actions) for c in configs] == [3, 2]


def test_pub_sub_only_instance_has_no_journal_sow_or_replication() -> None:
    """An instance that only hosts a topic locally needs no transaction log, SOW
    or replication section, and none is emitted."""
    config = config_for(
        "instance A { } topic t { definition m::M topology { local A } }", "A", PLAIN
    )
    for section in ("<TransactionLog>", "<SOW>", "<Replication>"):
        assert section not in config


def test_endpoint_placeholders_sanitize_instance_names() -> None:
    """Instance names become environment-variable stems: upper-cased, with
    non-alphanumerics mapped to `_`."""
    assert (
        endpoint_placeholder("Quoting.LN", "REPLICATION_INETADDR")
        == "${QUOTING_LN_REPLICATION_INETADDR}"
    )
    assert env_var_name("a-b.c_9") == "A_B_C_9"


def test_transient_topics_have_a_recovery_point_and_no_sow_file() -> None:
    """A transient SOW emits its durability and recovery point, and no file name."""
    config = config_for(
        "instance A { } topic t { definition m::M topology { local A "
        "sow A { durability transient { recovery_point now } } } }",
        "A",
        PLAIN,
    )
    assert "<Durability>transient</Durability>" in config
    assert "<RecoveryPoint>now</RecoveryPoint>" in config
    assert "<FileName>" not in config


def test_recovery_point_epoch_maps_to_recovery_point_epoch() -> None:
    """The `epoch` recovery point is emitted as `epoch`."""
    config = config_for(
        "instance A { } topic t { definition m::M topology { local A "
        "sow A { durability transient { recovery_point epoch } } } }",
        "A",
        PLAIN,
    )
    assert "<RecoveryPoint>epoch</RecoveryPoint>" in config


def test_persistent_topics_have_no_recovery_point() -> None:
    """A persistent SOW emits its file name and no recovery point."""
    config = config_for(
        """instance A { } topic t { definition m::M topology { local A
           sow A { durability persistent { file "./t.sow" } } } }""",
        "A",
        PLAIN,
    )
    assert "<RecoveryPoint>" not in config
    assert "<FileName>./t.sow</FileName>" in config


# ---- queues -------------------------------------------------------------------


def queue_entry(definition: str, pattern: str, *, routed: bool) -> CatalogEntry:
    """A catalog entry with fields `qty` (wire `q`), `urgency` (`u`), `kind` (`k`)."""
    fields = [leaf("qty", "q"), leaf("urgency", "u"), leaf("kind", "k")]
    return CatalogEntry(definition, pattern, "json", routed, [], fields)


def block(config: str, element: str) -> str:
    """The text inside the first `<element>` block of `config`."""
    _, found, rest = config.partition(f"<{element}>")
    assert found, f"no <{element}> in:\n{config}"
    return rest.partition(f"</{element}>")[0]


PAIR = "instance A1 { group A } instance A2 { group A } "


def test_emits_every_queue_setting_in_wire_names() -> None:
    config = config_for(
        f"{PAIR} topic t {{ definition m::T topology {{ mesh A }} }} "
        'queue q { from t where "/qty > 1" '
        "delivery at_least_once { lease 60s leasing strict max_deliveries 5 max_cancels 3 "
        "backlog 100 per_subscription_backlog 10 expiration 1d expiration_model queue "
        "fairness round_robin } "
        'priority "/urgency" recovery_point creation file_backed_metadata '
        "deferred_ack_expiration 2h topology { mesh A } }",
        "A1",
        Catalog([queue_entry("m::T", "t", routed=False)]),
    )
    queue = block(config, "Queue")
    for line in [
        "<Name>q</Name>",
        "<MessageType>json</MessageType>",
        "<UnderlyingTopic>t</UnderlyingTopic>",
        "<Filter>/q &gt; 1</Filter>",
        "<Semantics>at-least-once</Semantics>",
        "<LeasePeriod>60s</LeasePeriod>",
        "<Leasing>strict</Leasing>",
        "<MaxDeliveries>5</MaxDeliveries>",
        "<MaxCancels>3</MaxCancels>",
        "<MaxBacklog>100</MaxBacklog>",
        "<MaxPerSubscriptionBacklog>10</MaxPerSubscriptionBacklog>",
        "<Expiration>1d</Expiration>",
        "<ExpirationModel>queue</ExpirationModel>",
        "<FairnessModel>round-robin</FairnessModel>",
        "<Priority>/u</Priority>",
        "<RecoveryPoint>creation</RecoveryPoint>",
        "<FileBackedMetadata>enabled</FileBackedMetadata>",
        "<DeferredAckExpiration>2h</DeferredAckExpiration>",
    ]:
        assert line in queue, line
    assert "DefaultPublishTarget" not in queue


def test_a_routed_queue_prefixes_its_paths_with_the_envelope() -> None:
    config = config_for(
        f"{PAIR} topic t {{ definition m::T topology {{ mesh A }} }} "
        "queue q { from t delivery at_least_once { lease 1s } "
        "barrier \"/kind = 'EOD'\" topology { mesh A } }",
        "A1",
        Catalog([queue_entry("m::T", "t", routed=True)]),
    )
    assert "<BarrierExpression>/msg/k = 'EOD'</BarrierExpression>" in block(config, "Queue")


def test_several_sources_become_an_anchored_pattern_with_a_publish_target() -> None:
    config = config_for(
        f'{PAIR} topic "o.1" {{ definition m::O topology {{ mesh A }} }} '
        'topic "o.2" { definition m::O topology { mesh A } } '
        'queue q { from "o.1", "o.2" publish_to "o.2" '
        "delivery at_most_once { } topology { mesh A } }",
        "A1",
        Catalog([queue_entry("m::O", "{}", routed=False)]),
    )
    queue = block(config, "Queue")
    assert r"<UnderlyingTopic>^(o\.1|o\.2)$</UnderlyingTopic>" in queue
    assert "<DefaultPublishTarget>o.2</DefaultPublishTarget>" in queue
    assert "<Semantics>at-most-once</Semantics>" in queue


def test_a_queue_and_its_sources_are_journaled_and_replicated_together() -> None:
    config = config_for(
        f"{PAIR} instance B {{ }} "
        "topic t { definition m::T topology { mesh A replicate A <-> B } } "
        "queue q { from t delivery at_least_once { lease 1s } "
        "topology { mesh A replicate A <-> B } }",
        "A1",
        Catalog([queue_entry("m::T", "t", routed=False)]),
    )
    journal = block(config, "TransactionLog")
    assert "<Name>q</Name>" in journal
    assert "<Name>t</Name>" in journal
    # The destination to B lists the source once and the queue, with full
    # validation; A1 receives the queue from A and B, so it passes A to B.
    to_b = config.partition("<Name>B</Name>")[2].partition("</Destination>")[0]
    assert to_b.count("<Name>t</Name>") == 1
    queue_topic = to_b.partition("<Name>q</Name>")[2].partition("</Topic>")[0]
    assert "ExcludeValidation" not in queue_topic
    assert "<PassThrough>^(A)$</PassThrough>" in to_b


def test_a_queue_reading_its_own_name_lists_only_its_name() -> None:
    config = config_for(
        "instance C1 { group C } instance C2 { group C } "
        "queue jobs { definition m::JOBS "
        "delivery at_least_once { lease 30s } topology { owned by C1 { mesh C } } }",
        "C1",
        Catalog([queue_entry("m::JOBS", "jobs", routed=False)]),
    )
    queue = block(config, "GroupLocalQueue")
    assert "UnderlyingTopic" not in queue
    assert "<InitialOwner>C1</InitialOwner>" in queue
    # The domain is named after its owner (lab F22).
    assert "<GroupLocalQueueDomain>C1</GroupLocalQueueDomain>" in queue
    assert "<Name>jobs</Name>" in block(config, "TransactionLog")
    assert block(config, "Destination").count("<Topic>") == 1


def test_each_domain_gets_its_owner_and_links_between_domains_carry_only_sources() -> None:
    source = (
        "instance A1 { group A } instance A2 { group A } "
        "instance N1 { group N } instance N2 { group N } "
        "topic t { definition m::T topology { mesh A, N } } "
        "queue q { from t delivery at_least_once { lease 1s } topology { "
        "owned by A1 { mesh A } owned by N1 { mesh N } replicate A <-> N } }"
    )
    catalog = Catalog([queue_entry("m::T", "t", routed=False)])
    for instance, owner in [("A2", "A1"), ("N2", "N1")]:
        queue = block(config_for(source, instance, catalog), "GroupLocalQueue")
        assert f"<InitialOwner>{owner}</InitialOwner>" in queue, instance
        assert f"<GroupLocalQueueDomain>{owner}</GroupLocalQueueDomain>" in queue, instance
    # A1's destination to N lists the source only, and passes A (lab F24).
    a1 = config_for(source, "A1", catalog)
    to_n = a1.partition("<Name>N</Name>")[2].partition("</Destination>")[0]
    assert "<Name>t</Name>" in to_n
    assert "<Name>q</Name>" not in to_n
    assert "<PassThrough>^(A)$</PassThrough>" in to_n


def test_a_queue_reading_its_own_name_must_fit_its_definition() -> None:
    message = generate_error(
        "instance C1 { } queue jobs { definition m::JOBS "
        "delivery at_most_once { } topology { local C1 } }",
        Catalog([queue_entry("m::JOBS", "work", routed=False)]),
    )
    assert "'jobs'" in message
    assert "'work'" in message


def test_a_generated_own_name_queue_fits_its_pattern_only_through_holes() -> None:
    source = (
        "role R { attr key: string } list L [x, y] "
        'for k in L { instance "I.{{k}}" { roles [R] attr R.key "{{k}}" } } '
        'for i in R { queue "work/{{i.R.key}}" { definition m::WORK '
        "delivery at_most_once { } topology { local i } } }"
    )
    config_for(source, "I.x", Catalog([queue_entry("m::WORK", "work/{}", routed=False)]))
    message = generate_error(source, Catalog([queue_entry("m::WORK", "work/x", routed=False)]))
    assert "work/{{i.R.key}}" in message


def test_same_named_queues_on_separate_instances_keep_their_own_settings() -> None:
    """Two declarations of one name: each instance gets its own copy's settings."""
    source = (
        "instance V { } instance W { } "
        "queue q { definition m::Q delivery at_least_once { lease 5s } topology { local V } } "
        "queue q { definition m::Q delivery at_least_once { lease 9s } topology { local W } }"
    )
    catalog = Catalog([queue_entry("m::Q", "q", routed=False)])
    assert "<LeasePeriod>5s</LeasePeriod>" in config_for(source, "V", catalog)
    assert "<LeasePeriod>9s</LeasePeriod>" in config_for(source, "W", catalog)


def test_a_local_queue_is_journaled_without_replication() -> None:
    config = config_for(
        "instance V { } topic t { definition m::T topology { local V } } "
        "queue q { from t delivery at_most_once { } topology { local V } }",
        "V",
        Catalog([queue_entry("m::T", "t", routed=False)]),
    )
    assert "<LocalQueue>" in config
    journal = block(config, "TransactionLog")
    assert "<Name>q</Name>" in journal
    assert "<Name>t</Name>" in journal
    assert "<Replication>" not in config
    assert "amps-replication" not in config


def test_rejects_a_topic_replicated_with_two_filters_to_one_destination() -> None:
    assert "two different filters" in generate_error(
        f"{PAIR} instance B {{ }} "
        'topic t { definition m::T topology { mesh A replicate A <-> B where "/qty > 1" } } '
        "queue q { from t delivery at_least_once { lease 1s } "
        "topology { mesh A replicate A <-> B } }",
        Catalog([queue_entry("m::T", "t", routed=False)]),
    )
