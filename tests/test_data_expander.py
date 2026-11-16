"""Expands loops over data — lists and the generation context — into instances
and topics: nesting, map and list iteration, context paths, typed attribute
placeholders, loops over instances mixed with loops over data, the iteration
notes on errors, and checks V13, V15, V17, V19 and V22-V25.

Methodology: data expansion runs inside resolution, so every test compiles a
small source end to end against a JSON context. Accepting tests inspect the
instances and topics the templates generated; rejecting tests break exactly one
rule and assert the distinctive part of the error message (or all of it where
the iteration note is the point).
"""

import pytest
from helpers import instance

from topoc.context import Context
from topoc.errors import TopocError
from topoc.model import AttrKey, ResolvedSystem
from topoc.resolve import compile_topology

# The design doc's example: a location map driving instances and, per location,
# one topic per order interface.
LOCATIONS = """
    context {
      type location_info {
        order_interfaces: string[]
        port_base: int
      }
      locations: location_info[string]
      region: string
    }
    role Quoting { attr key: string attr port_base: int }
    instance Infra { }
    for loc in @locations {
      instance "Quoting.{{loc}}" {
        roles [Quoting]
        attr Quoting.key "{{loc}}"
        attr Quoting.port_base {{loc.port_base}}
      }
      for oi in loc.order_interfaces {
        topic "orders/{{loc}}/{{oi}}" {
          definition c::ORDERS
          topology { replicate Infra -> "Quoting.{{loc}}" where "/region = '{{@region}}'" }
        }
      }
    }
"""

LOCATIONS_DATA = """{
    "locations": {
      "NY": { "order_interfaces": ["A", "B"], "port_base": 9200 },
      "LN": { "order_interfaces": ["C"], "port_base": 9100 }
    },
    "region": "emea"
}"""


def compile_with(source: str, text: str) -> ResolvedSystem:
    return compile_topology(source, Context.from_json(text))


def compile_error(source: str, text: str) -> str:
    """Message of the error compiling `source` against `text` raises."""
    with pytest.raises(TopocError) as excinfo:
        compile_with(source, text)
    return str(excinfo.value)


def plain_error(source: str) -> str:
    """Message of the error compiling `source`, which declares no context, raises."""
    with pytest.raises(TopocError) as excinfo:
        compile_topology(source, None)
    return str(excinfo.value)


def topic_names(system: ResolvedSystem) -> list[str]:
    return [topic.name for topic in system.topics]


def test_generates_instances_and_topics_from_the_context() -> None:
    """Loops over context data declare instances and topics in document order
    (the context's, not sorted); placeholders take the key, a field, or a
    top-level context field, and a bare placeholder keeps the value's type."""
    system = compile_with(LOCATIONS, LOCATIONS_DATA)
    assert [i.name for i in system.instances] == ["Infra", "Quoting.NY", "Quoting.LN"]
    assert topic_names(system) == ["orders/NY/A", "orders/NY/B", "orders/LN/C"]

    ny = instance(system, "Quoting.NY")
    assert ny.attrs[AttrKey("Quoting", "key")] == "NY"
    assert ny.attrs[AttrKey("Quoting", "port_base")] == 9200
    assert ny.topics == ["orders/NY/A", "orders/NY/B"]

    edges = instance(system, "Infra").destinations
    assert [(d.topic, d.target_members, d.filter) for d in edges][2] == (
        "orders/LN/C",
        ["Quoting.LN"],
        "/region = 'emea'",
    )
    # The topic-name check sees the names as written.
    assert system.topics[0].name_template == "orders/{{loc}}/{{oi}}"


def test_empty_collections_generate_nothing() -> None:
    """An empty map or list generates nothing."""
    system = compile_with(LOCATIONS, '{ "locations": {}, "region": "x" }')
    assert len(system.instances) == 1
    assert system.topics == []


def test_context_fields_interpolate_outside_loops() -> None:
    """Top-level context fields interpolate outside loops too; a topic's name as
    written keeps the placeholder."""
    system = compile_with(
        """context { region: string }
           instance "Infra.{{@region}}" { }
           topic "status/{{@region}}" { definition c::T topology { local "Infra.{{@region}}" } }""",
        '{ "region": "emea" }',
    )
    assert system.instances[0].name == "Infra.emea"
    assert topic_names(system) == ["status/emea"]
    assert system.topics[0].name_template == "status/{{@region}}"


def test_two_variable_loops_iterate_maps_of_scalars() -> None:
    """`for k, v` iterates a map of scalars; a bare `{{v}}` keeps its type."""
    system = compile_with(
        """context { ports: int[string] }
           role R { attr port: int }
           for name, port in @ports { instance "I.{{name}}" { roles [R] attr R.port {{port}} } }""",
        '{ "ports": { "b": 2, "a": 1 } }',
    )
    assert [i.name for i in system.instances] == ["I.b", "I.a"]
    assert system.instances[1].attrs[AttrKey("R", "port")] == 1


def test_loops_over_instances_mix_with_loops_over_data() -> None:
    """A loop over instances may sit inside a loop over data, and contain one;
    its topics see both kinds of variable."""
    system = compile_topology(
        """role Q { attr key: string }
           instance A { roles [Q] attr Q.key "a" } instance B { roles [Q] attr Q.key "b" }
           for s in [x, y] {
             for q in Q { topic "t/{{s}}/{{q.Q.key}}" { definition c::T topology { local q } } }
           }
           for q in Q {
             for s in [u] { topic "u/{{q}}/{{s}}" { definition c::T topology { local q } } }
           }""",
        None,
    )
    assert topic_names(system) == ["t/x/a", "t/x/b", "t/y/a", "t/y/b", "u/A/u", "u/B/u"]
    assert system.topics[0].name_template == "t/{{s}}/{{q.Q.key}}"


def test_errors_name_every_enclosing_iteration() -> None:
    """Errors in generated definitions name every iteration that produced them,
    the innermost loop locating the note."""
    assert compile_error(
        """context { locations: string[][string] }
               instance S { }
               for loc, interfaces in @locations {
                 for oi in interfaces {
                   topic "orders/{{loc}}/{{oi}}" { definition c::T topology { local Nowhere } }
                 }
               }""",
        '{ "locations": { "LN": ["A"] } }',
    ) == (
        "unknown name 'Nowhere' at line 5, column 85 (in topic 'orders/LN/A', from the 'for' at "
        "line 4, column 18 with loc = 'LN', interfaces = 'a list', oi = 'A')"
    )
    assert compile_error(
        """context { locations: string[] }
               for loc in @locations { instance "Q.{{loc}}" { roles [Nope] } }""",
        '{ "locations": ["LN"] }',
    ) == (
        "unknown role 'Nope' at line 2, column 70 (in instance 'Q.LN', from the 'for' at line 2, "
        "column 16 with loc = 'LN')"
    )


def test_requires_context_data_exactly_when_declared() -> None:
    """The context is supplied exactly when the topology declares one."""
    assert plain_error("context { a: int }") == (
        "this topology declares a 'context' block, but no context was supplied at line 1, column 1"
    )
    assert compile_error("instance A { }", "{}") == (
        "context: a context was supplied, but the topology declares no 'context' block"
    )
    assert compile_error("context { a: int }", '{ "a": "x" }') == (
        "context: a: expected 'int', got 'x'"
    )


def test_context_fields_have_their_own_namespace() -> None:
    """Context fields are outside the flat namespace: `@field` names one, so a
    role, list or loop variable may share its name. A bare name the source does
    not declare but the context does is a missing `@`."""
    system = compile_with(
        """context { Q: string[] }
           role Q { attr key: string }
           for Q2 in @Q { instance "I.{{Q2}}" { roles [Q] attr Q.key "{{Q2}}" } }
           for q in Q { topic "t/{{q.Q.key}}" { definition c::T topology { local q } } }""",
        '{ "Q": ["a", "b"] }',
    )
    assert topic_names(system) == ["t/a", "t/b"]
    system = compile_with(
        """context { a: string[] } instance I { }
           for a in @a { topic "t/{{a}}" { definition c::T topology { local I } } }""",
        '{ "a": ["x"] }',
    )
    assert topic_names(system) == ["t/x"]

    source = "context { type info { n: int } m: info[string] s: string } "
    data = '{ "m": {}, "s": "v" }'
    assert compile_error(source + 'for k in m { topic "t/{{k}}" { } }', data) == (
        "'m' is a context field; write 'for k in @m' at line 1, column 69"
    )
    assert compile_error(source + 'topic "t/{{s}}" { }', data) == (
        "'{{s}}': 's' is a context field; write '{{@s}}' at line 1, column 66"
    )
    assert compile_error(source + 'topic "t/{{@nope.x}}" { }', data) == (
        "'{{@nope.x}}': the context has no field 'nope' at line 1, column 66"
    )
    assert plain_error('for s in @l { topic "t/{{s}}" { } }') == (
        "'@l': this topology declares no 'context' block at line 1, column 10"
    )


def test_loop_variables_are_unambiguous() -> None:
    """V13: a loop variable may not reuse a declared name or an enclosing loop's
    variable."""
    assert "loop variable 'a' conflicts with list defined at line 1" in plain_error(
        'list a [x] for a in [x] { topic "t/{{a}}" { } }'
    )
    assert "loop variable 's' is already the variable of an enclosing 'for'" in plain_error(
        'for s in [x] { for s in [y] { topic "t/{{s}}" { } } }'
    )


def test_instance_names_reference_every_enclosing_variable() -> None:
    """V15: an instance name declared in nested loops references every
    enclosing variable."""
    assert (
        "instance name 'I' does not reference loop variable 's', so every iteration would "
        "produce the same instance"
    ) in plain_error('for s in [x, y] { instance "I" { } }')


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            'for k in @m { topic "t/{{k.names}}" { } }',
            "'{{k.names}}' is a list; only strings, ints and bools can be interpolated",
        ),
        ('for k in @m { topic "t/{{k.nope}}" { } }', "'{{k.nope}}': 'info' has no field 'nope'"),
        ('topic "t/{{@s.x}}" { }', "'{{@s.x}}': '@s' is a string, which has no fields"),
        (
            'for x in @s { topic "t/{{x}}" { } }',
            "'@s' is a string; a 'for' iterates over a list, a map or an integer",
        ),
    ],
)
def test_paths_must_reach_values_of_the_right_kind(body: str, message: str) -> None:
    """V22: only scalars interpolate; paths name declared fields; a `for`
    iterates lists and maps."""
    source = "context { type info { names: string[] } m: info[string] s: string } "
    data = '{ "m": { "k": { "names": ["a"] } }, "s": "v" }'
    assert message in compile_error(source + body, data)


def test_instances_are_declared_only_in_loops_over_data() -> None:
    """V23: instances are declared only in loops over data; a loop over instances
    holds no other loop over instances."""
    assert (
        "instances may only be declared in loops over data (a list or the context); 'q' "
        "iterates over instances (the 'for' at line 1, column 37)"
    ) in plain_error('role Q { } instance A { roles [Q] } for q in Q { instance "I.{{q}}" { } }')
    assert "a loop over instances may not contain another loop over instances" in plain_error(
        """role Q { } instance A { roles [Q] }
               for q in Q { for r in Q { topic "t/{{q}}/{{r}}" { } } }"""
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            'for k, v in @recs { topic "t/{{k}}" { } }',
            "'@recs' maps to records: iterate it with one variable ('for k in @recs') and reach "
            "their fields with 'k.field'",
        ),
        (
            'for k in @flags { topic "t/{{k}}" { } }',
            "'@flags' maps to 'bool' values: iterate it with two variables "
            "('for k, value in @flags')",
        ),
        ('for k, v in @l { topic "t/{{k}}" { } }', "'for k, v' iterates a map; '@l' is a list"),
    ],
)
def test_loop_variables_match_the_domain(body: str, message: str) -> None:
    """V24: one variable for lists and maps of records, two for other maps."""
    source = "context { type info { n: int } recs: info[string] flags: bool[string] l: int[] } "
    assert message in compile_error(source + body, '{ "recs": {}, "flags": {}, "l": [] }')


def test_data_variables_are_not_instance_names() -> None:
    """V17: a data variable is text, so it cannot name instances in topology
    statements; a quoted name interpolating it can."""
    assert "'s' iterates over a list of values; it cannot be used as an instance name" in (
        plain_error('instance I.x { } for s in [x] { topic "t/{{s}}" { topology { local s } } }')
    )
    compile_topology(
        """instance I.x { } for s in [x] {
                 topic "t/{{s}}" { definition c::T topology { local "I.{{s}}" } } }""",
        None,
    )


def test_interpolated_names_stay_well_formed() -> None:
    """V19 holds for context values in topic names; V25 for instance and group
    names."""
    assert "'{{s}}' in a topic name expands to 'a/b', which contains '/'" in compile_error(
        'context { l: string[] } for s in @l { topic "t/{{s}}" { } }', '{ "l": ["a/b"] }'
    )
    assert "'I.a b' is not a valid instance name" in compile_error(
        'context { l: string[] } for s in @l { instance "I.{{s}}" { } }', '{ "l": ["a b"] }'
    )
    assert plain_error('instance "a b" { }') == (
        "'a b' is not a valid instance name ('.'-separated segments of letters, digits and '_', "
        "each starting with a letter or '_') at line 1, column 10"
    )
    assert "'G/1' is not a valid group name" in plain_error('instance A { group "G/1" }')
    # Quoted names that spell identifiers are names like any other.
    system = compile_topology(
        """instance "Quoting.LN" { group "G" } instance B { group G }
           topic t { definition c::T topology { mesh "G" local Quoting.LN } }""",
        None,
    )
    assert (system.instances[0].name, system.instances[0].group) == ("Quoting.LN", "G")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("{{@n}}", "attribute 'R.a' expects string, got int"),
        ("{{@l}}", "'{{@l}}' is a list; an attribute value is a string, an int or a bool"),
        ("{{nope}}", "'{{nope}}' names no loop variable or context field"),
    ],
)
def test_attribute_placeholders_are_typed(value: str, message: str) -> None:
    """A bare attribute placeholder takes the value's type, checked against the
    attribute's declaration (V10); it must name a scalar."""
    source = "context { n: int l: int[] } role R { attr a: string } "
    body = f"instance I {{ roles [R] attr R.a {value} }}"
    assert message in compile_error(source + body, '{ "n": 1, "l": [] }')


# ---- loops over integers ------------------------------------------------------

# A context with a top-level count and a per-site count.
COUNTS = (
    "context { type site { workers: int } count: int sites: site[string] } "
    "role W { attr index: int } "
)


def test_a_loop_over_an_integer_counts_from_zero() -> None:
    system = compile_with(
        COUNTS + "for i in @count { "
        'instance "W.w{{i}}" { roles [W] attr W.index {{i}} } '
        'topic "foo/{{i}}" { definition c::T topology { local "W.w{{i}}" } } }',
        '{ "count": 3, "sites": {} }',
    )
    assert [t.name for t in system.topics] == ["foo/0", "foo/1", "foo/2"]
    # A bare `{{i}}` is an int, so it fits an int attribute.
    assert instance(system, "W.w2").attrs[AttrKey("W", "index")] == 2


def test_a_loop_over_a_nested_integer_counts_per_item() -> None:
    system = compile_with(
        COUNTS + "instance A { } for s in @sites { for w in s.workers { "
        'topic "work/{{s}}/{{w}}" { definition c::T topology { local A } } } }',
        '{ "count": 0, "sites": { "LN": { "workers": 2 }, "NY": { "workers": 1 } } }',
    )
    assert [t.name for t in system.topics] == ["work/LN/0", "work/LN/1", "work/NY/0"]


def test_a_loop_over_zero_generates_nothing() -> None:
    system = compile_with(
        COUNTS + "instance A { } for i in @count { "
        'topic "foo/{{i}}" { definition c::T topology { local A } } }',
        '{ "count": 0, "sites": {} }',
    )
    assert system.topics == []


@pytest.mark.parametrize(
    ("header", "name", "count", "expected"),
    [
        ("for i in @count", "foo/{{i}}", -1, "'@count' is -1"),
        ("for i in @count", "foo/{{i}}", -1, "cannot be negative"),
        ("for i, v in @count", "foo/{{i}}", 2, "'@count' is an integer"),
        ("for i in @count", "foo/{{i.x}}", 2, "counts over an integer, which has no fields"),
    ],
)
def test_rejects_negative_counts_two_variables_and_fields_of_a_count(
    header: str, name: str, count: int, expected: str
) -> None:
    source = (
        COUNTS + f"instance A {{ }} {header} {{ "
        f'topic "{name}" {{ definition c::T topology {{ local A }} }} }}'
    )
    assert expected in compile_error(source, f'{{ "count": {count}, "sites": {{}} }}')


# ---- names shared across instances --------------------------------------------


def test_iterations_place_same_named_queues_on_their_own_instances() -> None:
    """Each quoting instance hosts its own queue per quoter and order interface:
    names repeat across instances, each copy independent."""
    system = compile_with(
        """context {
             type quoter { ois: int }
             type quoting { quoters: quoter[string] }
             quoting_instances: quoting[string]
           }
           instance Quoting1 { }
           instance Quoting2 { }
           for instance in @quoting_instances {
             for quoter in instance.quoters {
               for i in quoter.ois {
                 queue "orders/{{quoter}}/{{i}}" {
                   definition market::topics::orders
                   delivery at_least_once { lease 60s per_subscription_backlog 10 }
                   topology { local "{{instance}}" }
                 }
               }
             }
           }""",
        """{ "quoting_instances": {
             "Quoting1": { "quoters": { "A": { "ois": 2 }, "B": { "ois": 2 } } },
             "Quoting2": { "quoters": { "A": { "ois": 1 }, "B": { "ois": 1 } } }
           } }""",
    )
    assert instance(system, "Quoting1").queues == [
        "orders/A/0",
        "orders/A/1",
        "orders/B/0",
        "orders/B/1",
    ]
    assert instance(system, "Quoting2").queues == ["orders/A/0", "orders/B/0"]
    assert len(system.queues) == 6


# ---- topology blocks -------------------------------------------------------


def test_topology_blocks_read_the_context() -> None:
    """A block reads the context like any topology, used or not."""
    system = compile_with(
        """context { region: string sites: string[] }
           instance "Infra.{{@region}}" { }
           topology ha { local "Infra.{{@region}}" }
           for s in @sites { topic "t/{{s}}" { definition c::T topology { use ha } } }""",
        '{ "region": "emea", "sites": ["a", "b"] }',
    )
    assert system.instances[0].topics == ["t/a", "t/b"]
    assert "'{{@nope}}': the context has no field 'nope'" in compile_error(
        'context { region: string } topology unused { local "I.{{@nope}}" }',
        '{ "region": "emea" }',
    )
