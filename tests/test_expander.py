"""Unit tests for `for` template expansion: how lists and role/group/instance
domains bind the loop variable, where it is substituted, and validation checks
V13-V18 of docs/topo-dsl.md.

Methodology: expansion runs inside resolution, so every test compiles a small
source end to end with `compile_topology`. Accepting tests inspect the resolved
topics and edges the templates produced; rejecting tests give a template that
breaks exactly one rule and assert the distinctive part of the `CompileError`
message (or all of it where the iteration note is the point).
"""

import pytest
from helpers import instance

from topoc.errors import CompileError
from topoc.model import ResolvedSystem
from topoc.resolve import compile_topology


def compile_error(source: str) -> str:
    """Message of the `CompileError` that compiling `source` must raise."""
    with pytest.raises(CompileError) as excinfo:
        compile_topology(source, None)
    return str(excinfo.value)


def topic_names(system: ResolvedSystem) -> list[str]:
    """The resolved topic names, in order."""
    return [topic.name for topic in system.topics]


# Two instances carrying role Q, with distinct keys, plus a standalone instance S.
QUOTERS = """
    role Q { attr key: string }
    instance A { roles [Q] attr Q.key "x" }
    instance B { roles [Q] attr Q.key "y" }
    instance S { }
"""


def test_expands_a_list_iteration_by_iteration() -> None:
    """A list iterates value by value; within an iteration, topics follow body
    order."""
    system = compile_topology(
        """
        instance A { }
        list L [a, b]
        for s in L {
          topic "x/{{s}}" { definition c::T topology { local A } }
          topic "y/{{s}}" { definition c::T topology { local A } }
        }
    """,
        None,
    )
    assert topic_names(system) == ["x/a", "y/a", "x/b", "y/b"]


def test_expands_an_inline_list() -> None:
    """An inline list works like a named one, and quoted items keep characters
    such as spaces."""
    system = compile_topology(
        """
        instance A { }
        for s in [a, "b c"] { topic "x/{{s}}" { definition c::T topology { local A } } }
    """,
        None,
    )
    assert topic_names(system) == ["x/a", "x/b c"]


def test_role_loop_binds_instances_attributes_and_names() -> None:
    """Over a role, the variable is each carrying instance: `{{q.Role.attr}}`
    reads its attributes, `{{q}}` is its name, and a bare `q` names it in
    topology statements."""
    system = compile_topology(
        QUOTERS
        + """
        for q in Q {
          topic "o/{{q.Q.key}}" {
            definition c::T
            topology { replicate q -> S where "/src = '{{q}}'" }
          }
        }
    """,
        None,
    )
    assert topic_names(system) == ["o/x", "o/y"]
    from_a = instance(system, "A").destinations[0]
    assert (from_a.topic, from_a.target_members, from_a.filter) == ("o/x", ["S"], "/src = 'A'")
    assert instance(system, "B").destinations[0].topic == "o/y"


def test_group_loop_iterates_members() -> None:
    """Over a group, the variable is each member, in declaration order. Members
    are replicas, so each generated topic is placed on the whole group (here a
    per-member status topic, meshed)."""
    system = compile_topology(
        """
        instance A { group G } instance B { group G }
        for m in G { topic "status/{{m}}" { definition c::T topology { mesh G } } }
    """,
        None,
    )
    assert topic_names(system) == ["status/A", "status/B"]
    assert instance(system, "B").topics == ["status/A", "status/B"]


def test_group_loop_cannot_make_replicas_diverge() -> None:
    """Expanded topics go through V9 like any other: placing a per-member topic on
    that member alone would make the group's replicas diverge."""
    assert "members of group 'G' would diverge on topic 'local/A'" in compile_error(
        """
        instance A { group G } instance B { group G }
        for m in G { topic "local/{{m}}" { definition c::T topology { local m } } }
    """
    )


def test_resolver_placeholders_survive_expansion() -> None:
    """Only the loop variable is substituted at expansion: `{{Role.attr}}` is left
    for the resolver to interpolate per replication target, as in a hand-written
    topic."""
    system = compile_topology(
        QUOTERS
        + """
        for v in [one] {
          topic "t/{{v}}" {
            definition c::T
            topology { replicate S -> Q where "/owner = '{{Q.key}}/{{v}}'" }
          }
        }
    """,
        None,
    )
    assert [d.filter for d in instance(system, "S").destinations] == [
        "/owner = 'x/one'",
        "/owner = 'y/one'",
    ]


def test_v13_rejects_variable_reusing_a_declared_name() -> None:
    """V13: the loop variable may not reuse a declared name, which would make `q`
    ambiguous."""
    assert "loop variable 's' conflicts with instance defined at line 2" in compile_error(
        """
        instance s { }
        for s in [a] { topic "x/{{s}}" { definition c::T topology { local s } } }
    """
    )


def test_v14_rejects_unknown_or_topic_domains() -> None:
    """V14: the domain must be a declared list, role, group or instance."""
    assert "unknown name 'Nope'" in compile_error('for s in Nope { topic "x/{{s}}" { } }')
    assert "'t' is a topic; a 'for' iterates over a list, role, group or instance" in (
        compile_error('topic t { } for s in t { topic "x/{{s}}" { } }')
    )


def test_v14_rejects_role_with_no_instances() -> None:
    """V14 for roles: a role domain must select at least one instance (as V4 for
    topology names)."""
    assert "role 'Q' has no instances" in compile_error(
        'role Q { } for q in Q { topic "x/{{q}}" { } }'
    )


def test_a_name_shared_by_iterations_cannot_be_placed_twice_on_one_instance() -> None:
    """A topic name that ignores the loop variable is the same in every
    iteration, so every iteration places a copy of it: on one instance, the
    registry rejects the second copy, naming the iteration."""
    message = compile_error(
        'instance A { } for s in [a, b] { topic "fixed" { definition c::T topology { local A } } }'
    )
    assert (
        "topic 'fixed' is already on instance 'A', placed by the topic declared at line 1, "
        "column 40 (from the 'for' at line 1, column 16 with s = 'a')"
    ) in message
    assert "with s = 'b')" in message


def test_v16_rejects_expanded_name_collisions() -> None:
    """V16: expanded names join the namespace; a generated topic sharing a
    hand-written one's name cannot be placed on the same instance. The error
    names the iteration that produced the second copy."""
    message = compile_error(
        """
        instance A { }
        topic "x/a" { definition c::T topology { local A } }
        for s in [a] { topic "x/{{s}}" { definition c::T topology { local A } } }
    """
    )
    assert "topic 'x/a' is already on instance 'A', placed by the topic declared at line 3" in (
        message
    )
    assert "(in topic 'x/a', from the 'for' at line 4, column 9 with s = 'a')" in message


def test_v17_rejects_value_variable_as_instance_name() -> None:
    """V17: over a list of values, the variable is text only; it cannot name an
    instance."""
    assert "'s' iterates over a list of values; it cannot be used as an instance name" in (
        compile_error('for s in [a] { topic "x/{{s}}" { definition c::T topology { local s } } }')
    )


def test_v17_rejects_attribute_of_value_variable() -> None:
    """V17: list values have no attributes."""
    assert "'{{s.R.a}}': 's' iterates over a list of values, which have no attributes" in (
        compile_error('for s in [a] { topic "x/{{s.R.a}}" { } }')
    )


def test_v17_rejects_missing_instance_attribute() -> None:
    """V17: an instance attribute reference must name an attribute the instance
    has."""
    assert "'{{q.Q.nope}}': instance 'A' has no attribute 'Q.nope'" in compile_error(
        QUOTERS + 'for q in Q { topic "x/{{q.Q.nope}}" { } }'
    )


def test_v17_rejects_malformed_variable_reference() -> None:
    """V17: after the variable comes either nothing or a qualified `Role.attr`."""
    assert "'{{q.key}}' must be '{{q}}' or '{{q.Role.attr}}'" in compile_error(
        QUOTERS + 'for q in Q { topic "x/{{q}}/{{q.key}}" { } }'
    )


def test_v18_rejects_unresolved_placeholders_in_topic_names() -> None:
    """V18: a topic name may not keep a `{{...}}` that nothing resolves, in or out
    of a template."""
    assert "topic name 'x/{{y}}' contains an unresolved '{{...}}'" in compile_error(
        'topic "x/{{y}}" { }'
    )
    assert "topic name 'x/a/{{y}}' contains an unresolved '{{...}}'" in compile_error(
        'for s in [a] { topic "x/{{s}}/{{y}}" { } }'
    )


def test_v19_rejects_topic_name_values_that_are_not_one_segment() -> None:
    """V19: a value interpolated into a topic name must be one segment: not empty,
    and without `/`, so it can only fill one hole of a name pattern. Elsewhere
    (filters, file paths) values are unrestricted."""
    message = compile_error(
        """
        role Q { attr key: string } instance A { roles [Q] attr Q.key "LN/A" }
        for q in Q { topic "orders/{{q.Q.key}}" { } }
    """
    )
    assert "'{{q.Q.key}}' in a topic name expands to 'LN/A', which contains '/'" in message
    assert "with q = 'A')" in message
    assert "'{{s}}' in a topic name expands to '', which is empty" in compile_error(
        'for s in [""] { topic "orders/{{s}}" { } }'
    )
    compile_topology(
        """
        role Q { attr key: string } instance A { roles [Q] attr Q.key "LN/A" } instance S { }
        for q in Q { topic "orders/{{q}}" { definition c::T
          topology { replicate q -> S where "/owner = '{{q.Q.key}}'" } } }
    """,
        None,
    )


def test_list_names_are_not_topology_names() -> None:
    """A list is not a set of instances, so it cannot appear in a topology
    statement."""
    assert "'L' is a list; expected a role, group or instance" in compile_error(
        "list L [a] topic t { definition c::T topology { local L } }"
    )


def test_errors_in_expanded_topics_name_the_iteration() -> None:
    """Errors found while resolving an expanded topic keep their location in the
    template and name the iteration that produced the topic."""
    assert compile_error(
        'for s in [a, b] { topic "x/{{s}}" { definition c::T topology { local Nowhere } } }'
    ) == (
        "unknown name 'Nowhere' at line 1, column 70 (in topic 'x/a', from the 'for' at line 1, "
        "column 1 with s = 'a')"
    )
