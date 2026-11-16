"""Unit tests for `resolve` / `compile_topology`: name binding, attribute
resolution, topology fan-out, and validation checks V1-V11 of docs/topo-dsl.md.

Methodology: each check gets a minimal source that violates exactly that rule
and nothing else, compiled end to end, and the test asserts the distinctive
part of the `CompileError` message. Where a rule also has an accepting side
(defaults, interpolation, group targets, V9), a companion test compiles a
valid source and inspects the `ResolvedSystem` directly. `topic(...)` wraps
topology statements in a complete topic so each test spells out only what it
is about.
"""

import pytest
from helpers import instance

from topoc.errors import CompileError
from topoc.model import (
    AttrKey,
    DistributedQueue,
    EdgeKind,
    GroupLocalQueue,
    LocalQueue,
    QueueDomain,
    QueueOwnName,
    QueueTopics,
    ResolvedInstance,
    ResolvedSystem,
)
from topoc.resolve import compile_topology


def compile_error(source: str) -> str:
    """Message of the `CompileError` that compiling `source` must raise."""
    with pytest.raises(CompileError) as excinfo:
        compile_topology(source, None)
    return str(excinfo.value)


def topic(statements: str) -> str:
    """A complete topic body around the given topology statements."""
    return f"topic t {{ definition c::T topology {{ {statements} }} }}"


def test_v1_rejects_duplicate_names() -> None:
    """V1: roles, topics, instances and groups share one namespace. The full
    message is asserted because it carries two locations: the first
    definition's (in the text) and the duplicate's (the error location)."""
    assert compile_error("role X { } instance X { }") == (
        "name 'X' already defined as role at line 1, column 6 at line 1, column 21"
    )


def test_v1_rejects_group_name_colliding_with_role() -> None:
    """V1 for groups: a group comes into existence by being named, and may not
    take a name already used by another kind of entity."""
    assert "group name 'G' conflicts with role" in compile_error(
        "role G { } instance A { group G }"
    )


def test_v2_rejects_unknown_topology_name() -> None:
    """V2: every name in a topology statement must be declared."""
    assert "unknown name 'Nowhere'" in compile_error(topic("local Nowhere"))


def test_v2_rejects_unknown_role_in_instance() -> None:
    """V2: every name in an instance's `roles [...]` must be declared."""
    assert "unknown role 'Nope'" in compile_error("instance A { roles [Nope] }")


def test_rejects_non_role_in_roles_list() -> None:
    """A declared name of the wrong kind in `roles [...]` is rejected, and the
    message names its actual kind."""
    assert "'B' is a instance, expected a role" in compile_error(
        "instance B { } instance A { roles [B] }"
    )


def test_rejects_duplicate_role_in_instance() -> None:
    """An instance may list each role only once."""
    assert "duplicate role 'R' in instance 'A'" in compile_error(
        "role R { } instance A { roles [R, R] }"
    )


def test_rejects_topic_as_topology_name() -> None:
    """Names in topology statements denote sets of instances, so a topic name is
    rejected there."""
    assert "'t' is a topic; expected a role, group or instance" in compile_error(topic("local t"))


def test_v3_mesh_requires_a_group() -> None:
    """V3: `mesh` connects replicas, so its target must be a group; a role is
    rejected and identified as one."""
    assert "'mesh' requires a group; 'Q' is a role" in compile_error(
        "role Q { } instance A { roles [Q] } " + topic("mesh Q")
    )


def test_v4_rejects_role_with_no_instances() -> None:
    """V4: a role used in a topology must select at least one instance."""
    assert "role 'Q' has no instances" in compile_error("role Q { } " + topic("local Q"))


def test_v5_rejects_undeclared_attr_assignment() -> None:
    """V5: an instance may only assign attributes that the role declares."""
    assert "role 'Q' declares no attribute 'key'" in compile_error(
        'role Q { } instance A { roles [Q] attr Q.key "x" }'
    )


def test_v6_requires_value_for_defaultless_attr() -> None:
    """V6: an attribute without a default must be assigned by every instance
    carrying its role."""
    assert "instance 'A' missing value for 'Q.key'" in compile_error(
        "role Q { attr key: string } instance A { roles [Q] }"
    )


def test_v6_applies_defaults() -> None:
    """V6, accepting side: an unassigned attribute with a default resolves to
    that default, keyed by (role, attribute)."""
    system = compile_topology("role Q { attr port: int = 9090 } instance A { roles [Q] }", None)
    assert system.instances[0].attrs[AttrKey("Q", "port")] == 9090


def test_v7_rejects_attr_for_uncarried_role() -> None:
    """V7: an attribute qualification must name a role the instance carries."""
    assert "instance 'A' does not carry role 'Q'" in compile_error(
        'role Q { attr key: string } instance A { attr Q.key "x" }'
    )


def test_v8_rejects_interpolation_on_group_target_without_role() -> None:
    """V8: `{{Role.attr}}` requires every target to carry `Role`. When the target
    is a group, the message explains that targets must be selected by that
    role."""
    message = compile_error(
        "role Q { attr key: string } instance A { group G } instance B { group G } "
        "instance S { } " + topic("replicate S -> G where \"/o LIKE '{{Q.key}}/.*'\"")
    )
    assert "target of this edge is group 'G'" in message
    assert "requires targets selected by role 'Q'" in message


def test_v8_rejects_interpolation_on_instance_target_without_role() -> None:
    """V8 with an instance target: the message names the instance that lacks the
    role."""
    assert "'{{Q.key}}' requires target instance 'A' to carry role 'Q'" in compile_error(
        "role Q { attr key: string } instance A { } instance S { } "
        + topic('replicate S -> A where "{{Q.key}}"')
    )


def test_v8_allows_interpolation_on_instance_target_carrying_role() -> None:
    """V8, accepting side: an instance target carrying the role gets the filter
    with its own attribute value substituted."""
    system = compile_topology(
        'role Q { attr key: string } instance A { roles [Q] attr Q.key "LN" } '
        "instance S { } " + topic("replicate S -> A where \"/o LIKE '{{Q.key}}/.*'\""),
        None,
    )
    assert instance(system, "S").destinations[0].filter == "/o LIKE 'LN/.*'"


def test_interpolation_renders_int_and_bool_attributes() -> None:
    """Non-string attributes interpolate as their literal text (`-3`, `false`),
    and whitespace inside the braces is ignored."""
    system = compile_topology(
        "role Q { attr n: int attr b: bool } "
        "instance A { roles [Q] attr Q.n -3 attr Q.b false } instance S { } "
        + topic('replicate S -> A where "/n = {{Q.n}} AND /b = {{ Q.b }}"'),
        None,
    )
    assert instance(system, "S").destinations[0].filter == "/n = -3 AND /b = false"


def test_v9_rejects_diverging_group_members() -> None:
    """V9: role fan-out interpolates a different filter for each member of group
    G, so the replicas would diverge; this is rejected, naming the group and the
    topic."""
    assert "members of group 'G' would diverge on topic 't'" in compile_error(
        "role Q { attr key: string } "
        'instance A { roles [Q] group G attr Q.key "a" } '
        'instance B { roles [Q] group G attr Q.key "b" } '
        "instance S { } " + topic("replicate S -> Q where \"/o LIKE '{{Q.key}}/.*'\"")
    )


def test_v9_accepts_identical_group_members() -> None:
    """V9, accepting side: a mesh plus replication into the group gives both
    members the same configuration. Also checks A's resulting edges: one mesh
    destination (to B), and incoming edges from both B (mesh) and S."""
    system = compile_topology(
        "instance A { group G } instance B { group G } instance S { } "
        + topic("mesh G replicate S -> G"),
        None,
    )
    a = instance(system, "A")
    assert len(a.destinations) == 1
    assert len(a.incoming) == 2


def test_v10_rejects_attr_type_mismatch() -> None:
    """V10: an assigned literal must match the attribute's declared type."""
    assert "attribute 'Q.key' expects string, got int" in compile_error(
        "role Q { attr key: string } instance A { roles [Q] attr Q.key 42 }"
    )


def test_v10_distinguishes_bool_from_int() -> None:
    """V10 with Python's `bool`-is-an-`int` pitfall: `true` is not a valid `int`
    value, and `1` is not a valid `bool` value."""
    assert "expects int, got bool" in compile_error(
        "role Q { attr n: int } instance A { roles [Q] attr Q.n true }"
    )
    assert "expects bool, got int" in compile_error(
        "role Q { attr b: bool } instance A { roles [Q] attr Q.b 1 }"
    )


def test_v10_rejects_default_type_mismatch() -> None:
    """V10 for defaults: checked at the declaration, even when no instance
    carries the role."""
    assert "default value for 'Q.key' is int, expected string" in compile_error(
        "role Q { attr key: string = 42 }"
    )


def test_v11_rejects_overlapping_source_and_target() -> None:
    """V11: an instance may not be on both sides of a `replicate` edge."""
    assert "edge from 'S' to 'S' includes instance 'S' on both sides" in compile_error(
        "instance S { } " + topic("replicate S -> S")
    )


def test_rejects_missing_topic_property() -> None:
    """`definition` and `topology` are optional in the grammar but required by
    resolution; each omission is reported by name."""
    assert "topic 't' is missing 'definition'" in compile_error("topic t { topology { local A } }")
    assert "topic 't' is missing 'topology'" in compile_error("topic t { definition c::T }")


def test_sow_target_must_host_the_topic() -> None:
    """A `sow` may only materialise the topic on instances that host it; here B
    has no placement for `t`."""
    assert "'sow B' for topic 't': instance 'B' does not host the topic" in compile_error(
        "instance A { } instance B { } "
        + topic("local A sow B { durability transient { recovery_point now } }")
    )


def test_group_target_produces_single_destination_with_failover_members() -> None:
    """Fan-out shape for a group target: one destination per source, addressed to
    the group and listing every member, in declaration order, as a failover
    address."""
    system = compile_topology(
        "instance A { group G } instance B { group G } instance S { } " + topic("replicate S -> G"),
        None,
    )
    (destination,) = instance(system, "S").destinations
    assert destination.kind is EdgeKind.REPLICATE
    assert destination.target_group == "G"
    assert destination.target_members == ["A", "B"]


def test_group_target_rejects_conflicting_interpolated_filters() -> None:
    """A group target yields a single destination, so its filter must render
    identically for every member; members with different attribute values are
    rejected."""
    assert "members of group 'G' produce conflicting filters for topic 't'" in compile_error(
        "role Q { attr key: string } "
        'instance A { roles [Q] group G attr Q.key "a" } '
        'instance B { roles [Q] group G attr Q.key "b" } '
        "instance S { } " + topic('replicate S -> G where "{{Q.key}}"')
    )


def test_interpolation_rejects_unqualified_reference() -> None:
    """`{{...}}` must reference a qualified `Role.attr`."""
    assert "interpolation '{{key}}' must be a qualified 'Role.attr'" in compile_error(
        'role Q { attr key: string } instance A { roles [Q] attr Q.key "x" } '
        "instance S { } " + topic('replicate S -> A where "{{key}}"')
    )


def test_interpolation_rejects_unterminated_braces() -> None:
    """An opening `{{` without a closing `}}` is rejected rather than passed
    through."""
    assert "unterminated '{{' in filter" in compile_error(
        "instance A { } instance S { } " + topic('replicate S -> A where "{{oops"')
    )


def test_passthrough_tokens_survive_interpolation() -> None:
    """Only `{{...}}` belongs to the DSL: `${...}` (deploy-time environment) and
    `%n` (AMPS instance name) pass through untouched."""
    system = compile_topology(
        'role Q { attr key: string } instance A { roles [Q] attr Q.key "LN" } '
        "instance S { } " + topic("replicate S -> A where \"/o = '${ENV}/{{Q.key}}/%n'\""),
        None,
    )
    assert instance(system, "S").destinations[0].filter == "/o = '${ENV}/LN/%n'"


def test_groups_list_explicit_then_implicit() -> None:
    """Resolved groups list explicit groups first, then implicit singletons, even
    when the singleton's instance is declared first; an instance without a
    `group` clause belongs to the group named after itself."""
    system = compile_topology(
        "instance Solo { } instance A { group G } instance B { group G }", None
    )
    assert [(g.name, g.is_explicit) for g in system.groups] == [("G", True), ("Solo", False)]
    assert instance(system, "Solo").group == "Solo"


# ---- two-way replication and relaying (passthrough) -------------------------

PAIR = "instance A1 { group A } instance A2 { group A } "


def edge(source: str, instance: str, target_group: str) -> tuple[EdgeKind, list[str]]:
    """The kind and passthrough groups of `instance`'s destination toward
    `target_group`."""
    system = compile_topology(source, None)
    found = next(i for i in system.instances if i.name == instance)
    destination = next(d for d in found.destinations if d.target_group == target_group)
    return destination.kind, destination.passthrough


def test_a_meshed_group_relays_what_a_failover_list_delivers_to_one_member() -> None:
    """Q reaches only one member of A; the mesh must pass Q to the other."""
    source = f"{PAIR} instance Q {{ }} {topic('mesh A replicate Q -> A')}"
    assert edge(source, "A1", "A")[1] == ["Q"]
    assert edge(source, "A2", "A")[1] == ["Q"]
    assert edge(source, "Q", "A")[1] == []


def test_distribution_from_every_member_needs_no_relay() -> None:
    """Each member of A reaches each quoter directly: nothing to relay."""
    source = (
        f"role R {{ }} {PAIR} instance Q1 {{ roles [R] }} instance Q2 {{ roles [R] }} "
        + topic("mesh A replicate A -> R")
    )
    for sender, target in [("A1", "A"), ("A2", "A"), ("A1", "Q1"), ("A2", "Q2")]:
        assert edge(source, sender, target)[1] == [], (sender, target)


def test_a_chain_relays_the_group_messages_arrive_from() -> None:
    source = "instance A { } instance B { } instance C { } " + topic(
        "replicate A -> B replicate B -> C"
    )
    assert edge(source, "B", "C")[1] == ["A"]
    assert edge(source, "A", "B")[1] == []


def test_two_way_replicate_is_an_edge_each_way() -> None:
    source = f"{PAIR} instance B {{ }} {topic('mesh A replicate A <-> B')}"
    assert edge(source, "A1", "B") == (EdgeKind.TWO_WAY, [])
    assert edge(source, "B", "A")[0] is EdgeKind.TWO_WAY
    # B reaches A through a failover list, so the mesh relays B.
    assert edge(source, "A1", "A") == (EdgeKind.MESH, ["B"])


def filter_of(source: str, instance: str, target_group: str) -> str | None:
    """The filter of `instance`'s destination toward `target_group`."""
    system = compile_topology(source, None)
    found = next(i for i in system.instances if i.name == instance)
    return next(d for d in found.destinations if d.target_group == target_group).filter


def test_a_filtered_two_way_replicate_filters_both_directions() -> None:
    source = f"{PAIR} instance B {{ }} " + topic('mesh A replicate A <-> B where "/x = 1"')
    assert filter_of(source, "A1", "B") == "/x = 1"
    assert filter_of(source, "B", "A") == "/x = 1"


def test_a_two_way_filter_may_interpolate_values_shared_by_both_sides() -> None:
    source = (
        "role R { attr region: string } "
        'instance A { roles [R] attr R.region "EU" } '
        'instance B { roles [R] attr R.region "EU" } '
        + topic("replicate A <-> B where \"/region = '{{R.region}}'\"")
    )
    assert filter_of(source, "A", "B") == "/region = 'EU'"
    assert filter_of(source, "B", "A") == "/region = 'EU'"


def test_rejects_a_two_way_filter_that_differs_by_direction() -> None:
    message = compile_error(
        "role R { attr key: string } "
        'instance A { roles [R] attr R.key "LN" } '
        'instance B { roles [R] attr R.key "NY" } '
        + topic("replicate A <-> B where \"/owner = '{{R.key}}'\"")
    )
    assert "the same way in both directions" in message
    assert "'/owner = 'NY'' for 'B'" in message
    assert "'/owner = 'LN'' for 'A'" in message


def test_v11_rejects_overlapping_sides_of_a_two_way_replicate() -> None:
    assert "on both sides" in compile_error(f"{PAIR} {topic('replicate A <-> A1')}")


# ---- queues -------------------------------------------------------------------

# A pair A1/A2 meshing topics `t` and `u`, and B also hosting them, linked
# two-way to A: the shape most queue tests build on.
QUEUE_BASE = (
    "instance A1 { group A } instance A2 { group A } instance B { } "
    "topic t { definition c::T topology { mesh A replicate A <-> B } } "
    "topic u { definition c::U topology { mesh A replicate A <-> B } } "
)
AT_LEAST_ONCE = "delivery at_least_once { lease 60s }"


def queue(body: str) -> str:
    return f"{QUEUE_BASE} queue q {{ {body} }}"


def holder(system: ResolvedSystem, name: str) -> ResolvedInstance:
    return next(i for i in system.instances if i.name == name)


def test_a_distributed_queue_resolves_with_its_own_placement_and_edges() -> None:
    system = compile_topology(
        queue(f"from t {AT_LEAST_ONCE} topology {{ mesh A replicate A <-> B }}"),
        None,
    )
    [resolved] = system.queues
    assert (resolved.definition, resolved.source) == ("c::T", QueueTopics(["t"], None))
    assert isinstance(resolved.kind, DistributedQueue)
    for name in ["A1", "A2", "B"]:
        found = holder(system, name)
        assert found.queues == ["q"], name
        # Queues stay out of the topic tables.
        assert "q" not in found.topics, name
        assert all(d.topic != "q" for d in found.destinations), name
    assert len(holder(system, "A1").queue_destinations) == 2


def test_an_instance_receiving_a_queue_passes_its_groups_on_every_destination() -> None:
    """Lab F19: A1's one-way topic destination to V carries no queue, yet AMPS
    requires it to pass the groups A1 receives the queue from."""
    system = compile_topology(
        f"{QUEUE_BASE} instance V {{ }} "
        "topic v { definition c::V topology { mesh A replicate A -> V } } "
        f"queue q {{ from t {AT_LEAST_ONCE} "
        "topology { mesh A replicate A <-> B } }",
        None,
    )
    to_v = next(d for d in holder(system, "A1").destinations if d.target_group == "V")
    assert to_v.passthrough == ["A", "B"]


def test_queue_edges_pass_every_other_group_they_arrive_from() -> None:
    """AMPS's queue rule (lab F8): A1's destination to B passes A (from A2) and
    its mesh destination passes B; B passes nothing back to A."""
    system = compile_topology(
        queue(f"from t {AT_LEAST_ONCE} topology {{ mesh A replicate A <-> B }}"),
        None,
    )

    def passed(name: str, group: str) -> list[str]:
        found = holder(system, name)
        return next(d for d in found.queue_destinations if d.target_group == group).passthrough

    assert passed("A1", "B") == ["A"]
    assert passed("A1", "A") == ["B"]
    assert passed("B", "A") == []


def test_queue_sources_must_be_topics_of_one_definition() -> None:
    assert "'A1' is a instance; a queue reads from topics" in compile_error(
        queue(f"from A1 {AT_LEAST_ONCE} topology {{ mesh A }}")
    )
    assert "different definitions" in compile_error(
        queue(f"from t, u publish_to t {AT_LEAST_ONCE} topology {{ mesh A }}")
    )


def test_several_sources_require_publish_to_one_of_them() -> None:
    source = (
        "instance A1 { group A } instance A2 { group A } "
        "topic t1 { definition c::T topology { mesh A } } "
        "topic t2 { definition c::T topology { mesh A } } "
    )
    assert "requires 'publish_to'" in compile_error(
        f"{source} queue q {{ from t1, t2 {AT_LEAST_ONCE} topology {{ mesh A }} }}"
    )
    assert "must name one of queue 'q''s sources" in compile_error(
        f"{source} queue q {{ from t1 publish_to t2 {AT_LEAST_ONCE} topology {{ mesh A }} }}"
    )
    system = compile_topology(
        f"{source} queue q {{ from t1, t2 publish_to t2 {AT_LEAST_ONCE} topology {{ mesh A }} }}",
        None,
    )
    assert system.queues[0].source == QueueTopics(["t1", "t2"], "t2")


@pytest.mark.parametrize(
    ("topology", "kind"),
    [("local A, B", LocalQueue()), ("mesh A replicate A <-> B", DistributedQueue())],
)
def test_the_topology_decides_the_queue_kind(topology: str, kind: object) -> None:
    system = compile_topology(queue(f"from t {AT_LEAST_ONCE} topology {{ {topology} }}"), None)
    assert system.queues[0].kind == kind


@pytest.mark.parametrize(
    ("topology", "expected"),
    [
        ("replicate A -> B", "must be replicated both ways"),
        ("local A mesh A", "'local' places an independent queue"),
        ("local B owned by A1 { mesh A }", "'local' places an independent queue"),
        ("owned by A1 { mesh A } mesh A", "belongs inside an 'owned by' block"),
        ("sow A { durability transient { recovery_point now } }", "not 'sow'"),
    ],
)
def test_rejects_queue_topologies_that_mix_kinds_or_cannot_hold_a_queue(
    topology: str, expected: str
) -> None:
    assert expected in compile_error(queue(f"from t {AT_LEAST_ONCE} topology {{ {topology} }}"))


def test_a_queue_without_from_reads_its_own_name_and_needs_no_source_topic() -> None:
    """`work` is placed on instances hosting no topic at all: its own name is
    its underlying topic (lab F16, F18)."""
    system = compile_topology(
        f"{QUEUE_BASE} instance C1 {{ group C }} instance C2 {{ group C }} "
        f"queue work {{ definition c::WORK {AT_LEAST_ONCE} "
        "topology { mesh C } }",
        None,
    )
    [work] = system.queues
    assert (work.definition, work.source) == ("c::WORK", QueueOwnName())
    assert holder(system, "C1").queues == ["work"]


def test_a_queue_instance_must_host_its_sources() -> None:
    assert "is on 'C', which does not host its source topic 't'" in compile_error(
        f"{QUEUE_BASE} instance C {{ }} queue q {{ from t {AT_LEAST_ONCE} topology {{ local C }} }}"
    )


# Two groups A (A1, A2) and N (N1, N2) and a singleton B, all hosting `t` and
# linked two-way, for queues split into domains.
DOMAINS_BASE = (
    "instance A1 { group A } instance A2 { group A } instance B { } "
    "instance N1 { group N } instance N2 { group N } "
    "topic t { definition c::T topology { mesh A, N replicate A <-> B, N } } "
)


def domains(topology: str) -> str:
    return f"{DOMAINS_BASE} queue q {{ from t {AT_LEAST_ONCE} topology {{ {topology} }} }}"


def test_owned_by_blocks_make_one_domain_each() -> None:
    system = compile_topology(
        domains(
            "owned by A1 { mesh A replicate A <-> B } owned by N1 { mesh N } replicate A <-> N"
        ),
        None,
    )
    assert system.queues[0].kind == GroupLocalQueue(
        [QueueDomain("A1", ["A1", "A2", "B"]), QueueDomain("N1", ["N1", "N2"])]
    )
    # The link between domains carries the source, not the queue (F24).
    a1 = holder(system, "A1")
    assert all(d.target_group != "N" for d in a1.queue_destinations)
    to_n = next(d for d in a1.destinations if d.topic == "t" and d.target_group == "N")
    # A1 receives the queue from A2 and B: every destination passes them.
    assert "A" in to_n.passthrough


@pytest.mark.parametrize(
    ("topology", "expected"),
    [
        ("owned by N1 { mesh A }", "must be an instance it places; 'N1' is not"),
        ("owned by A1 { mesh A } owned by A1 { replicate A <-> B }", "'A1' already owns a block"),
        ("owned by A1 { mesh A } owned by A2 { replicate A <-> B }", "is in two domains"),
        (
            "owned by A1 { mesh A } owned by N1 { mesh N } replicate A <-> B",
            "but 'B' is in none",
        ),
        (
            "owned by A1 { mesh A replicate A <-> B } replicate A <-> B",
            "links instances of the same domain",
        ),
    ],
)
def test_rejects_inconsistent_domains(topology: str, expected: str) -> None:
    assert expected in compile_error(domains(topology))


def test_a_topic_has_no_owner() -> None:
    assert "applies to queues only" in compile_error(f"{PAIR} {topic('owned by A1 { mesh A }')}")


def test_v9_rejects_a_queue_on_part_of_a_group() -> None:
    assert "would diverge on queue 'q'" in compile_error(
        queue(f"from t {AT_LEAST_ONCE} topology {{ local A1 }}")
    )


@pytest.mark.parametrize("name", ["orders.work", "a*", "q|r", "x(y)"])
def test_rejects_regular_expression_characters_in_a_queue_name(name: str) -> None:
    assert "AMPS does not allow regular-expression characters" in compile_error(
        f'{QUEUE_BASE} queue "{name}" {{ from t {AT_LEAST_ONCE} topology {{ local B }} }}'
    )


def test_v1_queue_and_topic_names_share_the_namespace() -> None:
    assert "'t' already defined" in compile_error(
        f"{QUEUE_BASE} queue t {{ from u {AT_LEAST_ONCE} topology {{ local B }} }}"
    )


def test_a_filtered_two_way_queue_edge_carries_its_filter() -> None:
    system = compile_topology(
        queue(
            f"from t {AT_LEAST_ONCE} "
            "topology { mesh A replicate A <-> B where \"/tag = 'keep'\" }"
        ),
        None,
    )
    to_a = next(d for d in holder(system, "B").queue_destinations if d.target_group == "A")
    assert to_a.filter == "/tag = 'keep'"


# ---- queues in loops ----------------------------------------------------------

# Quoting instances LN and NY (role Q, key = location), each hosting its own
# `orders/<key>`, and an Infra pair hosting them all.
LOOP_BASE = (
    "role Q { attr key: string } "
    "instance A1 { group A } instance A2 { group A } "
    "list L [LN, NY] "
    "for loc in L { "
    '  instance "Q.{{loc}}" { roles [Q] attr Q.key "{{loc}}" } '
    '  topic "orders/{{loc}}" { definition c::O topology { '
    '    local "Q.{{loc}}" replicate "Q.{{loc}}" <-> A mesh A } } '
    "} "
)


def test_a_loop_over_data_generates_one_queue_per_item() -> None:
    system = compile_topology(
        f'{LOOP_BASE} for loc in L {{ queue "work/{{{{loc}}}}" {{ '
        f'from "orders/{{{{loc}}}}" {AT_LEAST_ONCE} '
        'topology { local "Q.{{loc}}" } } }',
        None,
    )
    assert [(q.name, q.name_template) for q in system.queues] == [
        ("work/LN", "work/{{loc}}"),
        ("work/NY", "work/{{loc}}"),
    ]
    assert holder(system, "Q.LN").queues == ["work/LN"]
    assert system.queues[0].source == QueueTopics(["orders/LN"], None)


def test_a_loop_over_instances_generates_one_queue_per_instance() -> None:
    """Each quoter's work queue, owned by the quoter, shared with Infra."""
    system = compile_topology(
        f'{LOOP_BASE} for q in Q {{ queue "work/{{{{q.Q.key}}}}" {{ '
        f'from "orders/{{{{q.Q.key}}}}" {AT_LEAST_ONCE} '
        "topology { owned by q { replicate q <-> A mesh A } } } }",
        None,
    )

    def domain(owner: str) -> GroupLocalQueue:
        return GroupLocalQueue([QueueDomain(owner, ["A1", "A2", owner])])

    assert [(q.name, q.kind) for q in system.queues] == [
        ("work/LN", domain("Q.LN")),
        ("work/NY", domain("Q.NY")),
    ]
    assert holder(system, "A1").queues == ["work/LN", "work/NY"]


def test_iterations_may_share_a_queue_name_on_separate_instances() -> None:
    """Each quoter's own `work` queue, independent of the others'."""
    system = compile_topology(
        f"{LOOP_BASE} for q in Q {{ queue work {{ definition c::W {AT_LEAST_ONCE} "
        "topology { local q } } }",
        None,
    )
    assert [(q.name, q.instances) for q in system.queues] == [
        ("work", ["Q.LN"]),
        ("work", ["Q.NY"]),
    ]
    assert holder(system, "Q.NY").queues == ["work"]
    # On one instance, the second iteration's copy is rejected.
    message = compile_error(
        f"{LOOP_BASE} for loc in L {{ queue work {{ definition c::W "
        f"{AT_LEAST_ONCE} topology {{ local A }} }} }}"
    )
    assert "queue 'work' is already on instance 'A1', placed by the queue declared" in message
    assert "with loc = 'LN')" in message
    assert "with loc = 'NY')" in message


def test_declarations_may_share_a_name_on_separate_instances() -> None:
    system = compile_topology(
        "instance A { } instance B { } "
        "topic t { definition c::T topology { local A } } "
        "topic t { definition c::T topology { local B } }",
        None,
    )
    assert [t.instances for t in system.topics] == [["A"], ["B"]]
    assert holder(system, "B").topics == ["t"]


@pytest.mark.parametrize("second", ["local A", "mesh G", "replicate A -> B", "replicate B -> A"])
def test_an_instance_hosts_one_copy_of_a_name(second: str) -> None:
    """The registry is per instance, whichever statement places a name."""
    message = compile_error(
        "instance A { group G } instance A2 { group G } instance B { } "
        "topic t { definition c::T topology { mesh G } } "
        f"topic t {{ definition c::T topology {{ {second} }} }}"
    )
    assert (
        "topic 't' is already on instance 'A', placed by the topic declared at line 1, column"
    ) in message


def test_replication_cannot_link_two_declarations() -> None:
    """Replication links copies of one declaration: its target cannot be another
    declaration's copy."""
    message = compile_error(
        "instance A { } instance B { } "
        "topic t { definition c::T topology { local B } } "
        "topic t { definition c::T topology { replicate A -> B } }"
    )
    assert "topic 't' is already on instance 'B', placed by the topic declared" in message


def test_declarations_of_a_name_share_their_definition() -> None:
    message = compile_error(
        "instance A { } instance B { } "
        "topic t { definition c::T topology { local A } } "
        "topic t { definition c::U topology { local B } }"
    )
    assert (
        "topic 't' has definition 'c::U' here but 'c::T' in the topic declared at line 1"
    ) in message
    message = compile_error(
        f"instance A {{ }} instance B {{ }} "
        f"queue q {{ definition c::T {AT_LEAST_ONCE} topology {{ local A }} }} "
        f"queue q {{ definition c::U {AT_LEAST_ONCE} topology {{ local B }} }}"
    )
    assert "queue 'q' has definition 'c::U' here" in message


def test_v9_rejects_group_members_hosting_different_declarations() -> None:
    """V9: members of a group are replicas, so they host copies of the same
    declarations."""
    message = compile_error(
        "instance A1 { group G } instance A2 { group G } "
        "topic t { definition c::T topology { local A1 } } "
        "topic t { definition c::T topology { local A2 } }"
    )
    assert "members of group 'G' would diverge on topic 't'" in message


def test_a_sow_targets_its_own_declarations_copy() -> None:
    message = compile_error(
        "instance A { } instance B { } "
        "topic t { definition c::T topology { local A } } "
        "topic t { definition c::T topology { local B "
        "sow A { durability transient { recovery_point now } } } }"
    )
    assert "'sow A' for topic 't': instance 'A' does not host the topic" in message


def test_errors_in_a_generated_queue_name_its_iteration() -> None:
    """NY's queue does not host `orders/LN`."""
    message = compile_error(
        f'{LOOP_BASE} for q in Q {{ queue "work/{{{{q.Q.key}}}}" {{ from "orders/LN" '
        f"{AT_LEAST_ONCE} topology {{ local q }} }} }}"
    )
    assert "is on 'Q.NY', which does not host its source topic 'orders/LN'" in message
    assert "q = 'Q.NY'" in message


# ---- topology blocks -------------------------------------------------------

HA = """
    role Quoting { attr key: string }
    instance Infra1 { group Infra }
    instance Infra2 { group Infra }
    instance Q1 { roles [Quoting] attr Quoting.key "a" }
    instance Q2 { roles [Quoting] attr Quoting.key "b" }
"""


def test_a_used_block_resolves_like_its_statements_written_in_place() -> None:
    """A topic using a block resolves exactly as with the block's statements
    written in its place, fan-out placeholders included."""
    statements = """mesh Infra
        replicate Infra -> Quoting where "/owner = '{{Quoting.key}}'"
        sow Infra { durability transient { recovery_point now } }"""
    inline = compile_topology(HA + topic(statements), None)
    used = compile_topology(f"{HA} topology ha {{ {statements} }} {topic('use ha')}", None)
    assert inline == used


def test_block_names_join_the_namespace() -> None:
    """Block names join the flat namespace (V1) and are not instance sets."""
    assert "name 'Infra1' already defined as instance" in compile_error(
        HA + "topology Infra1 { mesh Infra }"
    )
    assert "'ha' is a topology block; expected a role, group or instance" in compile_error(
        f"{HA} topology ha {{ mesh Infra }} {topic('local ha')}"
    )
    looped = topic("use ha").replace("topic t", 'topic "t/{{ha}}"')
    assert "loop variable 'ha' conflicts with topology block" in compile_error(
        f"{HA} topology ha {{ mesh Infra }} for ha in Quoting {{ {looped} }}"
    )


def test_a_block_used_in_a_loop_holds_for_every_iteration() -> None:
    """A block used in a loop is the same in every iteration; the iteration
    varies only what the topic writes around it."""
    system = compile_topology(
        HA
        + """topology ha { mesh Infra }
             for q in Quoting {
               topic "orders/{{q.Quoting.key}}" {
                 definition c::T topology { use ha  replicate q -> Infra }
               }
             }""",
        None,
    )
    assert instance(system, "Infra1").topics == ["orders/a", "orders/b"]


# ---- topology block arguments ----------------------------------------------


def test_a_block_with_arguments_resolves_like_its_statements_written_in_place() -> None:
    inline = compile_topology(
        HA + topic("""mesh Infra replicate Infra -> Quoting where "/owner = '{{Quoting.key}}'\""""),
        None,
    )
    used = compile_topology(
        HA
        + """topology fanout(tier: group, to: role) {
               mesh tier
               replicate tier -> to where "/owner = '{{to.key}}'"
             }"""
        + topic("use fanout(tier = Infra, to = Quoting)"),
        None,
    )
    assert inline == used


ARGS_BLOCK = "topology b(from: group, to: selector) { replicate from -> to } "


def test_arguments_name_their_parameters_kind() -> None:
    """V32: each argument names its parameter's kind, checked where the block
    is used; a selector takes any."""
    assert (
        "argument 'Quoting' for 'from' of topology block 'b' is a role; 'from' takes a group"
    ) in compile_error(HA + ARGS_BLOCK + topic("use b(from = Quoting, to = Infra1)"))
    compile_topology(HA + ARGS_BLOCK + topic("use b(from = Infra, to = Q1)"), None)
    looped = """for q in Quoting {
        topic "t/{{q}}" { definition c::T topology { use b(from = q, to = Infra) } }
    }"""
    assert (
        "argument 'Q1' for 'from' of topology block 'b' is an instance; 'from' takes a group"
    ) in compile_error(HA + ARGS_BLOCK + looped)


def test_parameters_do_not_reuse_declared_names() -> None:
    assert "parameter 'Infra1' of topology block 'b' conflicts with instance" in compile_error(
        HA + "topology b(Infra1: group) { mesh Infra1 }"
    )
