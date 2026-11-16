"""Unit tests for `parse`, the `.topo` parser.

Methodology: each test parses a minimal snippet isolating one grammar rule.
Successful parses are checked structurally by matching the expected definition
or statement type in the syntax tree (`only` and `isinstance` assertions fail
the test if the parser built a different shape). Rejections are checked
through the `CompileError` message: a distinctive substring when only the
diagnostic's content matters, the full text when its location matters too.
Parsing is purely syntactic, so snippets need not be semantically valid
(undeclared names are fine here).
"""

import pytest

from topoc.dsl.parser import parse
from topoc.dsl.syntax import (
    AttrType,
    Barrier,
    ContextDef,
    ContextPath,
    FieldDecl,
    ForDef,
    Ident,
    InstanceDef,
    ListDef,
    LocalStmt,
    MeshStmt,
    OwnedStmt,
    ParamKind,
    PersistentDurability,
    Placeholder,
    Priority,
    QueueDef,
    QueueOwnName,
    QueueTopics,
    RecoveryPoint,
    ReplicateStmt,
    RoleDef,
    SourceFile,
    SowStmt,
    TopicDef,
    TopologyDef,
    TopologyStmt,
    TransientDurability,
    TypeExpr,
    TypeKind,
)
from topoc.errors import CompileError, SourceLocation
from topoc.queue import (
    AtLeastOnce,
    AtMostOnce,
    ExpirationModel,
    Fairness,
    Leasing,
    QueueRecoveryPoint,
)


def parse_error(source: str) -> str:
    """Message of the `CompileError` that parsing `source` must raise."""
    with pytest.raises(CompileError) as excinfo:
        parse(source)
    return str(excinfo.value)


def only[T](file: SourceFile, kind: type[T]) -> T:
    """The single definition in `file`, which must be a `kind`."""
    assert len(file.definitions) == 1
    definition = file.definitions[0]
    assert isinstance(definition, kind)
    return definition


def definition_of(source: str) -> str:
    """The `definition` path of a source holding exactly one topic."""
    definition = only(parse(source), TopicDef).definition
    assert definition is not None
    return definition.text


def topology_of(source: str) -> list[object]:
    """The topology statements of a source holding exactly one topic."""
    topology = only(parse(source), TopicDef).topology
    assert topology is not None
    return list(topology)


def test_parses_qualified_definition_paths() -> None:
    """`definition` takes a `::`-separated path, which the parser joins back into
    one name; a single bare segment is also a valid path."""
    assert definition_of("topic t { definition Foo }") == "Foo"
    assert (
        definition_of("topic t { definition market::topics::REFDATA }") == "market::topics::REFDATA"
    )
    assert definition_of("topic t { definition a::b::C }") == "a::b::C"


def test_rejects_dangling_path_separator() -> None:
    """A `::` must be followed by another path segment."""
    assert "path segment" in parse_error("topic t { definition a:: }")


def test_parses_sow_with_durability_only() -> None:
    """A `sow` block needs nothing but a durability (keys come from the catalog),
    and it parses alongside placement statements in the same topology."""
    topology = topology_of(
        "topic t { definition c::T topology { local A "
        "sow A { durability transient { recovery_point now } } } }"
    )
    assert len(topology) == 2
    assert isinstance(topology[1], SowStmt)


def test_parses_role_with_typed_attrs_and_default() -> None:
    """Role attributes carry an explicit type and an optional default; a default
    is kept as a typed literal, and its absence is represented as such."""
    role = only(parse("role Basic { attr admin_port: int = 9090 attr name: string }"), RoleDef)
    assert role.name.text == "Basic"
    assert [(a.attr_type, a.default) for a in role.attrs] == [
        (AttrType.INT, 9090),
        (AttrType.STRING, None),
    ]


def test_attr_declaration_requires_explicit_type() -> None:
    """There is no implicit `string` type: omitting `: type` is an error whose
    message tells the user an explicit type is required."""
    assert "explicit type" in parse_error("role Quoting { attr key }")


def test_attr_declaration_rejects_dotted_names() -> None:
    """V12: attribute names are single segments, so a `Role.attr` reference
    always splits unambiguously at its last dot."""
    assert "may not contain '.'" in parse_error("role Quoting { attr a.b: string }")


def test_attr_declaration_rejects_unknown_type() -> None:
    """Only `string`, `int` and `bool` are attribute types; any other type is
    named in the error."""
    assert "unknown attribute type 'float'" in parse_error("role Quoting { attr key: float }")


def test_rejects_duplicate_attr_declaration() -> None:
    """A role may not declare the same attribute twice."""
    assert "duplicate attribute 'a' in role 'R'" in parse_error(
        "role R { attr a: int attr a: int }"
    )


def test_parses_full_topic() -> None:
    """A topic using every topology statement kind (mesh, filtered replicate,
    local, persistent sow) parses into the matching statements in source order,
    with filter and file strings kept verbatim: `{{...}}`, `${...}` and `%n`
    included."""
    topic = only(
        parse(
            """
            topic tvgrids {
              definition market::topics::TVGRIDS
              topology {
                mesh Infra
                replicate Infra -> Quoting where "/owner LIKE '{{Quoting.key}}/.*'"
                local Standalone
                sow Infra {
                  durability persistent { file "${AMPS_DIR}/sow/%n.sow" }
                }
              }
            }
            """
        ),
        TopicDef,
    )
    assert topic.definition is not None
    assert topic.definition.text == "market::topics::TVGRIDS"
    assert topic.topology is not None
    mesh, replicate, local, sow = topic.topology

    assert isinstance(mesh, MeshStmt)
    assert mesh.group.text == "Infra"

    assert isinstance(replicate, ReplicateStmt)
    assert (replicate.source.text, replicate.target.text) == ("Infra", "Quoting")
    assert replicate.filter is not None
    assert replicate.filter.value == "/owner LIKE '{{Quoting.key}}/.*'"

    assert isinstance(local, LocalStmt)
    assert local.target.text == "Standalone"

    assert isinstance(sow, SowStmt)
    assert sow.target.text == "Infra"
    assert isinstance(sow.durability, PersistentDurability)
    assert sow.durability.file.value == "${AMPS_DIR}/sow/%n.sow"


@pytest.mark.parametrize(
    ("spelling", "expected"), [("now", RecoveryPoint.NOW), ("epoch", RecoveryPoint.EPOCH)]
)
def test_parses_transient_recovery_points(spelling: str, expected: RecoveryPoint) -> None:
    """Both transient recovery points, `now` and `epoch`, map to their enum value."""
    (sow,) = topology_of(
        f"topic q {{ topology {{ sow Q {{ durability transient "
        f"{{ recovery_point {spelling} }} }} }} }}"
    )
    assert isinstance(sow, SowStmt)
    assert isinstance(sow.durability, TransientDurability)
    assert sow.durability.recovery_point is expected


def test_recovery_point_accepts_only_now_or_epoch() -> None:
    """`recovery_point` takes a bare keyword: any other word, or a quoted "now",
    is rejected, and the message describes what was found."""
    assert "expected 'now' or 'epoch', found 'yesterday'" in parse_error(
        "topic t { topology { sow X { durability transient { recovery_point yesterday } } } }"
    )
    assert "expected 'now' or 'epoch', found a string literal" in parse_error(
        'topic t { topology { sow X { durability transient { recovery_point "now" } } } }'
    )


def test_persistent_sow_requires_file() -> None:
    """A persistent durability must name its SOW file."""
    assert "requires a 'file'" in parse_error(
        "topic t { topology { sow X { durability persistent { } } } }"
    )


def test_sow_requires_durability() -> None:
    """A `sow` block without a durability is rejected."""
    assert "'sow X' requires a 'durability'" in parse_error("topic t { topology { sow X { } } }")


def statement_names(source: str) -> list[str]:
    """A topic's topology statements, rendered `mesh G` or `A -> B`, in order."""
    topic = parse(source).definitions[0]
    assert isinstance(topic, TopicDef)
    assert topic.topology is not None
    rendered: list[str] = []
    for statement in topic.topology:
        match statement:
            case MeshStmt(group):
                rendered.append(f"mesh {group.text}")
            case LocalStmt(target):
                rendered.append(f"local {target.text}")
            case SowStmt(target, _):
                rendered.append(f"sow {target.text}")
            case ReplicateStmt(source_name, target, where, two_way):
                suffix = "" if where is None else f" where {where.value}"
                arrow = "<->" if two_way else "->"
                rendered.append(f"{source_name.text} {arrow} {target.text}{suffix}")
    return rendered


def test_reads_a_list_of_names_as_one_statement_per_name() -> None:
    """`mesh`, `local` and `sow` apply to each name of a list, in order."""
    assert statement_names(
        'topic t { topology { mesh G, H local A, "B/x" '
        "sow A, B { durability transient { recovery_point now } } } }"
    ) == ["mesh G", "mesh H", "local A", "local B/x", "sow A", "sow B"]


def test_reads_a_replicate_list_as_every_source_to_every_target() -> None:
    """`replicate` lists expand to every source and target pair, sharing the filter."""
    assert statement_names('topic t { topology { replicate A, B -> C, D where "/x = 1" } }') == [
        "A -> C where /x = 1",
        "A -> D where /x = 1",
        "B -> C where /x = 1",
        "B -> D where /x = 1",
    ]


def test_reads_a_two_way_replicate_as_two_way_statements() -> None:
    assert statement_names("topic t { topology { replicate A, B <-> C } }") == [
        "A <-> C",
        "B <-> C",
    ]


def test_reads_a_filtered_two_way_replicate() -> None:
    assert statement_names('topic t { topology { replicate A <-> B where "/x = 1" } }') == [
        "A <-> B where /x = 1"
    ]


def test_shares_the_durability_across_sow_targets() -> None:
    topic = parse(
        'topic t { topology { sow A, B { durability persistent { file "f" } } } }'
    ).definitions[0]
    assert isinstance(topic, TopicDef)
    assert topic.topology is not None
    first, second = topic.topology
    assert isinstance(first, SowStmt)
    assert isinstance(second, SowStmt)
    assert first.durability == second.durability


def test_rejects_a_name_listed_twice() -> None:
    assert "'A' is listed twice" in parse_error("topic t { topology { local A, B, A } }")
    assert "'B' is listed twice" in parse_error("topic t { topology { replicate A -> B, B } }")


def test_rejects_a_dangling_comma_in_a_name_list() -> None:
    assert "role, group or instance name" in parse_error("topic t { topology { local A, } }")


def test_rejects_duplicate_topic_property() -> None:
    """Each topic property may appear only once; the message names the property
    and the topic."""
    assert "duplicate 'definition' in 't'" in parse_error(
        "topic t { definition a::A definition b::B }"
    )


def test_parses_instance_with_roles_group_and_attrs() -> None:
    """An instance using every property: roles keep their declaration order, the
    group is recorded, and each `attr Role.name literal` is split into role and
    attribute with its literal typed as string, int or bool."""
    instance = only(
        parse(
            """
            instance Quoting.LN {
              roles [Basic, Quoting]
              group Emea
              attr Basic.admin_port 9090
              attr Basic.primary true
              attr Quoting.key "LN"
            }
            """
        ),
        InstanceDef,
    )
    assert instance.name.text == "Quoting.LN"
    assert [role.text for role in instance.roles] == ["Basic", "Quoting"]
    assert instance.group is not None
    assert instance.group.text == "Emea"
    assigned = [(a.role.text, a.attr.text, a.value) for a in instance.attrs]
    assert assigned == [
        ("Basic", "admin_port", 9090),
        ("Basic", "primary", True),
        ("Quoting", "key", "LN"),
    ]
    # `True == 1` in Python, so also pin the literal's type.
    assert type(instance.attrs[1].value) is bool


def test_qualified_split_uses_last_dot() -> None:
    """Role names may contain dots, so a qualified attribute splits at the last
    dot: `Quoting.Fast.key` is attribute `key` of role `Quoting.Fast`."""
    assign = only(parse('instance I { attr Quoting.Fast.key "x" }'), InstanceDef).attrs[0]
    assert (assign.role.text, assign.attr.text) == ("Quoting.Fast", "key")


def test_attr_assignment_requires_qualified_name() -> None:
    """Attribute assignments must be qualified with their role; a bare attribute
    name is rejected."""
    assert "qualified name 'Role.attr'" in parse_error('instance I { attr key "LN" }')


def test_rejects_duplicate_attr_assignment() -> None:
    """An instance may assign each qualified attribute only once."""
    assert "duplicate attribute assignment 'R.key'" in parse_error(
        'instance I { attr R.key "a" attr R.key "b" }'
    )


def test_rejects_unknown_top_level_keyword() -> None:
    """Only `role`, `topic`, `instance`, `list` and `for` start a definition; in
    particular, `group` is not a top-level block."""
    assert (
        "expected 'role', 'topic', 'queue', 'instance', 'list', 'for', 'context' or "
        "'topology', found 'group'" in parse_error("group Infra { }")
    )


def test_reports_unexpected_end_of_input() -> None:
    """Input ending inside a block reports what was expected next, without a
    location (there is no token to point at)."""
    assert parse_error("role Infra {") == "unexpected end of input, expected 'attr' or '}'"


def test_contextual_keywords_are_valid_names() -> None:
    """Keywords are contextual: `topic` and `key` are ordinary names wherever the
    grammar expects a name."""
    role = only(parse("role topic { attr key: string }"), RoleDef)
    assert role.name.text == "topic"
    assert role.attrs[0].name.text == "key"


def test_empty_source_parses_to_empty_file() -> None:
    """A file containing only comments is a valid, empty topology."""
    assert parse("// nothing here\n").definitions == []


def test_error_location_points_at_the_offending_token() -> None:
    """The missing `:` after `attr key` is discovered at the next token, so the
    error is located at the `}` on line 3; checked through both `location` and
    the message."""
    with pytest.raises(CompileError) as excinfo:
        parse("role Infra {\n  attr key\n}")
    assert excinfo.value.location == SourceLocation(3, 1)
    assert "found '}' at line 3, column 1" in str(excinfo.value)


# ---- quoted topic names, lists and `for` templates -------------------------


def test_parses_quoted_topic_names() -> None:
    """A topic name may be a quoted string, which allows characters an identifier
    cannot hold (`/`)."""
    assert only(parse('topic "foo/bar" { }'), TopicDef).name.text == "foo/bar"


def test_rejects_empty_topic_name() -> None:
    """A quoted topic name must not be empty."""
    assert parse_error('topic "" { }') == "topic name may not be empty at line 1, column 7"


def test_parses_list_definition() -> None:
    """`list` declares named values; items may be identifiers or quoted strings
    and keep their text."""
    lst = only(parse('list Strategies [momentum, "mean-revert"]'), ListDef)
    assert lst.name.text == "Strategies"
    assert [item.value for item in lst.items] == ["momentum", "mean-revert"]


def test_rejects_duplicate_list_items() -> None:
    """Items must be distinct, in a named list as in an inline one, so no two
    iterations collide."""
    assert "duplicate item 'a' in list 'S'" in parse_error("list S [a, b, a]")
    assert "duplicate item 'a' in inline list" in parse_error(
        'for s in [a, "a"] { topic "t/{{s}}" { } }'
    )


def test_parses_for_over_named_domain() -> None:
    """A `for` over a named domain keeps the variable, the domain name and every
    topic template, with placeholders and the variable used as a name left as
    written (expansion is the resolver's job)."""
    loop = only(
        parse(
            """
        for q in Quoting {
          topic "orders/{{q.Quoting.key}}" { topology { replicate q -> Infra } }
          topic "fills/{{q}}" { }
        }
    """
        ),
        ForDef,
    )
    assert loop.location == SourceLocation(2, 9)
    assert loop.variable.text == "q"
    assert isinstance(loop.domain, Ident)
    assert loop.domain.text == "Quoting"
    topics = [item for item in loop.body if isinstance(item, TopicDef)]
    assert [t.name.text for t in topics] == ["orders/{{q.Quoting.key}}", "fills/{{q}}"]
    assert topics[0].topology is not None
    replicate = topics[0].topology[0]
    assert isinstance(replicate, ReplicateStmt)
    assert replicate.source.text == "q"


def test_parses_for_over_inline_list() -> None:
    """A `for` may iterate over an inline list instead of a named one."""
    loop = only(parse('for s in [a, "b/c"] { topic "x/{{s}}" { } }'), ForDef)
    assert isinstance(loop.domain, list)
    assert [item.value for item in loop.domain] == ["a", "b/c"]


def test_for_body_holds_topics_instances_and_nested_loops() -> None:
    """A `for` body holds topics, instances and nested loops, in source order;
    other definitions are rejected. (Which loops may declare instances is the
    resolver's call: it depends on what the domains are.)"""
    loop = only(
        parse(
            """
        for loc in @locations {
          instance "Quoting.{{loc}}" { roles [Quoting] group "G.{{loc}}" }
          for oi in loc.order_interfaces {
            topic "orders/{{loc}}/{{oi}}" { topology { replicate Infra -> "Quoting.{{loc}}" } }
          }
        }
    """
        ),
        ForDef,
    )
    assert loop.domain == ContextPath("locations", SourceLocation(2, 20))
    instance, nested = loop.body
    assert isinstance(instance, InstanceDef)
    assert instance.name.text == "Quoting.{{loc}}"
    assert instance.group is not None
    assert instance.group.text == "G.{{loc}}"
    assert isinstance(nested, ForDef)
    assert isinstance(nested.domain, Ident)
    assert nested.domain.text == "loc.order_interfaces"
    (topic,) = nested.body
    assert isinstance(topic, TopicDef)
    assert topic.topology is not None
    replicate = topic.topology[0]
    assert isinstance(replicate, ReplicateStmt)
    assert replicate.target.text == "Quoting.{{loc}}"

    assert (
        "only 'topic', 'queue', 'instance' or 'for' definitions may appear in a 'for' block, "
        "found 'role'" in parse_error("for s in L { role R { } }")
    )


def test_for_requires_a_body() -> None:
    """An empty `for` body is rejected rather than silently producing nothing,
    nested or not."""
    assert parse_error("for s in L { }") == "'for' block defines nothing at line 1, column 1"
    assert (
        parse_error("for s in L { for t in M { } }")
        == "'for' block defines nothing at line 1, column 14"
    )


def test_parses_two_variable_for() -> None:
    """`for k, v` names a map's key and value; the two variables must differ."""
    loop = only(parse('for k, v in flags { topic "x/{{k}}" { } }'), ForDef)
    assert loop.variable.text == "k"
    assert loop.value is not None
    assert loop.value.text == "v"
    assert only(parse('for k in m { topic "x/{{k}}" { } }'), ForDef).value is None
    assert "loop variables must differ, both are 'k'" in parse_error(
        'for k, k in m { topic "x/{{k}}" { } }'
    )
    assert "loop variable 'v.w' may not contain '.'" in parse_error(
        'for k, v.w in m { topic "x/{{k}}" { } }'
    )


def test_names_may_be_quoted() -> None:
    """Instance, group and topology-statement names may be quoted, like topic
    names, so they can interpolate loop variables; they may not be empty."""
    file = parse(
        """instance "I.{{x}}" { group "G" }
        topic t { topology { mesh "G" local "I" replicate "I" -> "J"
          sow "I" { durability transient { recovery_point now } } } }"""
    )
    instance, topic = file.definitions
    assert isinstance(instance, InstanceDef)
    assert instance.name.text == "I.{{x}}"
    assert isinstance(topic, TopicDef)
    assert topic.topology is not None
    assert len(topic.topology) == 4
    assert parse_error('instance "" { }') == "instance name may not be empty at line 1, column 10"


def test_attribute_values_may_be_placeholders() -> None:
    """An attribute value may be a bare placeholder, kept as its trimmed
    expression; a role default may not (it has no context to read)."""
    instance = only(parse("instance I { attr Quoting.port {{ loc.port }} }"), InstanceDef)
    assert instance.attrs[0].value == Placeholder("loc.port")
    assert instance.attrs[0].value_location == SourceLocation(1, 32)
    assert "expected a literal (string, integer, 'true' or 'false'), found '{{x}}'" in parse_error(
        "role R { attr port: int = {{x}} }"
    )


def test_loop_variable_rejects_dots() -> None:
    """The loop variable is a single segment, so `{{s.Role.attr}}` splits
    unambiguously."""
    assert "loop variable 'a.b' may not contain '.'" in parse_error(
        'for a.b in L { topic "x/{{a.b}}" { } }'
    )


def test_for_requires_in() -> None:
    """The variable and the domain are separated by the keyword `in`."""
    assert "expected 'in', found 'of'" in parse_error("for s of L { }")


# ---- context declarations ----------------------------------------------------


def shape(type_expr: TypeExpr) -> str:
    """A type's shape, without locations: `string`, `info[string]`,
    `string[][string]`."""
    match type_expr.kind:
        case TypeKind.RECORD:
            assert type_expr.record is not None
            return type_expr.record.text
        case TypeKind.LIST:
            assert type_expr.inner is not None
            return shape(type_expr.inner) + "[]"
        case TypeKind.MAP:
            assert type_expr.inner is not None
            return shape(type_expr.inner) + "[string]"
        case _:
            return str(type_expr.kind.value)


def field_shapes(fields: list[FieldDecl]) -> list[str]:
    return [f"{field.name.text}: {shape(field.type_expr)}" for field in fields]


def test_parses_context_declarations() -> None:
    """A context block holds record types and top-level fields; types are
    written postfix and compose left to right."""
    context = only(
        parse(
            """context {
               type location_info { order_interfaces: string[] port_base: int }
               locations: location_info[string]
               flags: bool[string]
               routes: string[][string]
               region: string
             }"""
        ),
        ContextDef,
    )
    assert context.location == SourceLocation(1, 1)
    (location_info,) = context.types
    assert location_info.name.text == "location_info"
    assert field_shapes(location_info.fields) == ["order_interfaces: string[]", "port_base: int"]
    assert field_shapes(context.fields) == [
        "locations: location_info[string]",
        "flags: bool[string]",
        "routes: string[][string]",
        "region: string",
    ]
    assert context.fields[0].type_location == SourceLocation(3, 27)


def test_a_context_field_may_be_named_type() -> None:
    """`type` followed by ':' is a field named `type`, not a declaration."""
    context = only(parse("context { type: string }"), ContextDef)
    assert context.types == []
    assert context.fields[0].name.text == "type"


def test_map_keys_must_be_strings() -> None:
    """Map keys are strings; the key type is written all the same."""
    assert "map keys must be 'string', found 'int'" in parse_error("context { m: int[int] }")


def test_rejects_duplicate_or_dotted_context_names() -> None:
    """Duplicate context blocks, types and fields are rejected; field and type
    names are single segments."""
    assert "duplicate 'context' block (the first is at line 1, column 1)" in parse_error(
        "context { } context { }"
    )
    assert "duplicate type 't' (the first is at line 1, column 16)" in parse_error(
        "context { type t { } type t { } }"
    )
    assert "duplicate field 'a' in context" in parse_error("context { a: int a: string }")
    assert "duplicate field 'a' in type 't'" in parse_error("context { type t { a: int a: int } }")
    assert "field name 'a.b' may not contain '.'" in parse_error("context { a.b: int }")


# ---- queues -------------------------------------------------------------------

MINIMAL = "from t delivery at_least_once { lease 60s } topology { mesh G }"


def queue_of(source: str) -> QueueDef:
    queue = parse(source).definitions[0]
    assert isinstance(queue, QueueDef)
    return queue


def test_parses_every_queue_property() -> None:
    queue = queue_of(
        'queue "work/orders" { '
        'from "orders/LN", "orders/NY" publish_to "orders/LN" '
        "where \"/isin LIKE '^GB'\" "
        "delivery at_least_once { lease 60s leasing strict max_deliveries 5 max_cancels 3 "
        "backlog 100 per_subscription_backlog 10 expiration 1d expiration_model queue "
        "fairness proportional } "
        "barrier \"/kind = 'EOD'\" recovery_point creation file_backed_metadata "
        "deferred_ack_expiration 2h "
        "topology { owned by Infra1 { mesh Infra replicate Infra <-> Backup } } }"
    )
    assert queue.name.text == "work/orders"
    assert isinstance(queue.source, QueueTopics)
    assert [s.text for s in queue.source.sources] == ["orders/LN", "orders/NY"]
    assert queue.source.publish_to is not None
    assert queue.source.publish_to.text == "orders/LN"
    assert queue.filter is not None
    assert queue.filter.value == "/isin LIKE '^GB'"
    delivery = queue.delivery
    assert isinstance(delivery, AtLeastOnce)
    assert str(delivery.lease) == "60s"
    assert (delivery.leasing, delivery.max_deliveries, delivery.max_cancels) == (
        Leasing.STRICT,
        5,
        3,
    )
    limits = delivery.limits
    assert (limits.backlog, limits.per_subscription_backlog) == (100, 10)
    assert str(limits.expiration) == "1d"
    assert limits.expiration_model is ExpirationModel.QUEUE
    assert limits.fairness is Fairness.PROPORTIONAL
    assert isinstance(queue.ordering, Barrier)
    assert queue.recovery_point is QueueRecoveryPoint.CREATION
    assert queue.file_backed_metadata
    assert str(queue.deferred_ack_expiration) == "2h"
    [owned] = queue.topology
    assert isinstance(owned, OwnedStmt)
    assert (owned.owner.text, len(owned.statements)) == ("Infra1", 2)


def test_parses_an_at_most_once_local_queue_with_a_priority() -> None:
    queue = queue_of(
        'queue q { from t priority "/urgency" '
        "delivery at_most_once { fairness round_robin } topology { local A } }"
    )
    assert isinstance(queue.delivery, AtMostOnce)
    assert isinstance(queue.ordering, Priority)
    assert queue.ordering.path.value == "/urgency"


def test_a_queue_without_from_reads_its_own_name_under_a_definition() -> None:
    queue = queue_of(
        "queue work { definition market::queues::WORK "
        "delivery at_most_once { } topology { local A } }"
    )
    assert isinstance(queue.source, QueueOwnName)
    assert queue.source.definition.text == "market::queues::WORK"


def test_a_queue_reads_either_from_topics_or_its_own_name() -> None:
    rest = "delivery at_least_once { lease 1s } topology { mesh G }"
    assert "requires 'from' (the topics it reads) or 'definition'" in parse_error(
        f"queue q {{ {rest} }}"
    )
    assert "has both 'from' and 'definition'" in parse_error(
        f"queue q {{ from t definition c::Q {rest} }}"
    )
    assert "takes no 'publish_to'" in parse_error(
        f"queue q {{ definition c::Q publish_to t {rest} }}"
    )


@pytest.mark.parametrize(
    ("missing", "body"),
    [
        ("delivery", "from t topology { mesh G }"),
        ("topology", "from t delivery at_least_once { lease 1s }"),
    ],
)
def test_a_queue_requires_delivery_and_topology(missing: str, body: str) -> None:
    assert f"queue 'q' requires '{missing}'" in parse_error(f"queue q {{ {body} }}")


def test_at_least_once_requires_a_lease() -> None:
    assert "requires a 'lease'" in parse_error(
        "queue q { from t delivery at_least_once { } topology { mesh G } }"
    )


def test_at_most_once_rejects_lease_settings_and_other_fairness() -> None:
    assert "applies only to 'at_least_once'" in parse_error(
        "queue q { from t delivery at_most_once { lease 1s } topology { local A } }"
    )
    assert "only supports 'round_robin'" in parse_error(
        "queue q { from t delivery at_most_once { fairness fast } topology { local A } }"
    )


def test_rejects_combined_orderings_and_target_depth_with_one() -> None:
    assert "both 'priority' and 'barrier'" in parse_error(
        f'queue q {{ {MINIMAL} priority "/p" barrier "/b = 1" }}'
    )
    assert "both 'target_depth' and 'priority'" in parse_error(
        f'queue q {{ {MINIMAL} target_depth 5 priority "/p" }}'
    )


def test_priority_is_a_single_field_path() -> None:
    assert "a single field path" in parse_error(f'queue q {{ {MINIMAL} priority "/price * /qty" }}')


def test_rejects_duplicate_queue_properties_and_zero_counts() -> None:
    assert "'where'" in parse_error(f'queue q {{ {MINIMAL} where "/a = 1" where "/b = 1" }}')
    assert "must be a positive integer" in parse_error(f"queue q {{ {MINIMAL} target_depth 0 }}")


def test_parses_owned_by_blocks() -> None:
    queue = queue_of(
        "queue q { from t delivery at_least_once { lease 1s } topology { "
        "owned by A1 { mesh A replicate A <-> B } owned by N1 { mesh N } "
        "replicate A <-> N } }"
    )
    owned = [
        (st.owner.text, len(st.statements)) for st in queue.topology if isinstance(st, OwnedStmt)
    ]
    assert owned == [("A1", 2), ("N1", 1)]
    assert len(queue.topology) == 3


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        ("local A", "takes only 'mesh' and two-way 'replicate"),
        ("sow A { durability transient { recovery_point now } }", "takes only 'mesh'"),
        ("owned by B1 { mesh B }", "takes only 'mesh'"),
        ("replicate A -> B", "replicated both ways"),
        ("", "places nothing"),
    ],
)
def test_an_owned_by_block_takes_only_the_statements_sharing_a_queue(
    inner: str, expected: str
) -> None:
    assert expected in parse_error(
        "queue q { from t delivery at_least_once { lease 1s } "
        f"topology {{ owned by A1 {{ {inner} }} }} }}"
    )


def test_owned_needs_by() -> None:
    assert "'owned by <instance> { ... }'" in parse_error(
        "queue q { from t delivery at_least_once { lease 1s } topology { owned A1 { mesh A } } }"
    )


def test_parses_a_queue_in_a_for_block() -> None:
    loop = parse(f'list L [a] for x in L {{ queue "q/{{{{x}}}}" {{ {MINIMAL} }} }}').definitions[1]
    assert isinstance(loop, ForDef)
    [queue] = loop.body
    assert isinstance(queue, QueueDef)
    assert queue.name.text == "q/{{x}}"


# ---- topology blocks -------------------------------------------------------


def statement_summary(statements: list[TopologyStmt]) -> list[str]:
    summary: list[str] = []
    for statement in statements:
        match statement:
            case MeshStmt(group):
                summary.append(f"mesh {group.text}")
            case LocalStmt(target):
                summary.append(f"local {target.text}")
            case ReplicateStmt(source, target, _, _):
                summary.append(f"replicate {source.text} {target.text}")
            case SowStmt(target, _):
                summary.append(f"sow {target.text}")
            case OwnedStmt(owner, _):
                summary.append(f"owned {owner.text}")
    return summary


def test_use_inlines_a_block_in_place() -> None:
    """`use` stands for a block's statements, in place and in order; a block may
    be declared after its uses, and one `use` may name several."""
    file = parse(
        """
        topic t { topology { local A  use ha, fanout  local B } }
        topology ha { mesh Infra  sow Infra { durability transient { recovery_point now } } }
        topology fanout { replicate Infra -> Quoting }
        list topology_names [a]
        """
    )
    topic = file.definitions[0]
    assert isinstance(topic, TopicDef)
    assert topic.topology is not None
    assert statement_summary(topic.topology) == [
        "local A",
        "mesh Infra",
        "sow Infra",
        "replicate Infra Quoting",
        "local B",
    ]
    blocks = [d.name.text for d in file.definitions if isinstance(d, TopologyDef)]
    assert blocks == ["ha", "fanout"]


def test_queues_use_blocks_too() -> None:
    """A block may hold `owned by`; a queue may use one."""
    file = parse(
        """
        topology domains { owned by A1 { mesh A } owned by B1 { mesh B } }
        queue q { from t delivery at_least_once { lease 1s } topology { use domains } }
        """
    )
    queue = file.definitions[-1]
    assert isinstance(queue, QueueDef)
    assert statement_summary(queue.topology) == ["owned A1", "owned B1"]


def test_rejects_unknown_nested_and_empty_blocks() -> None:
    assert "unknown topology block 'nope'" in parse_error("topic t { topology { use nope } }")
    assert "a topology block cannot use another ('use a')" in parse_error(
        "topology a { mesh G } topology b { use a }"
    )
    assert "topology block 'a' places nothing" in parse_error("topology a { }")


def test_a_block_cannot_see_loop_variables() -> None:
    """A block cannot see the loop variables around its use, as a bare name or
    in a placeholder, so it means the same wherever it is used."""
    assert (
        "topology block 'to_q' refers to 'q' (line 1, column 36), the variable of the "
        "enclosing 'for' at line 2, column 16"
    ) in parse_error(
        """topology to_q { replicate Infra -> q }
               for q in Quoting { topic "t/{{q}}" { topology { use to_q } } }"""
    )
    assert "refers to 's'" in parse_error(
        """topology f { replicate Infra -> Quoting where "/k = '{{s}}'" }
               for s in [a] { topic "t/{{s}}" { topology { use f } } }"""
    )
    # Placeholders a block may hold: fan-out and context paths.
    parse(
        """topology f { replicate Infra -> Quoting where "/k = '{{Quoting.key}}/{{@r}}'" }
               for s in [a] { topic "t/{{s}}" { topology { use f } } }"""
    )


# ---- topology block parameters ---------------------------------------------


def filter_of(statement: TopologyStmt) -> str | None:
    match statement:
        case ReplicateStmt(filter=where) if where is not None:
            return where.value
    return None


def test_use_replaces_parameters_by_their_arguments() -> None:
    """Each parameter is replaced by its argument, named, in any order: in name
    positions, keeping the argument's location, and in placeholders."""
    file = parse(
        """topology fanout(from: group, to: role) {
             mesh from
             replicate from -> to where "/o = '{{to.key}}' and /s = '{{from}}'"
           }
           topic t { topology { use fanout(to = Quoting, from = Infra) } }"""
    )
    topic = file.definitions[1]
    assert isinstance(topic, TopicDef)
    assert topic.topology is not None
    assert statement_summary(topic.topology) == ["mesh Infra", "replicate Infra Quoting"]
    assert filter_of(topic.topology[1]) == "/o = '{{Quoting.key}}' and /s = 'Infra'"
    mesh = topic.topology[0]
    assert isinstance(mesh, MeshStmt)
    assert mesh.group.location == SourceLocation(5, 65)
    [block_use] = file.block_uses
    assert [(a.parameter.text, a.kind, a.value.text) for a in block_use.arguments] == [
        ("to", ParamKind.ROLE, "Quoting"),
        ("from", ParamKind.GROUP, "Infra"),
    ]


def test_arguments_may_be_loop_variables() -> None:
    """A loop variable passed as an argument stands for its value; a quoted
    argument may interpolate the use's loop variables."""
    file = parse(
        """topology to_infra(source: instance) {
             replicate source -> Infra where "/from = '{{source}}'"
           }
           for q in Quoting { topic "t/{{q}}" { topology { use to_infra(source = q) } } }
           for s in [a] { topic "u/{{s}}" { topology { use to_infra(source = "I.{{s}}") } } }"""
    )
    topologies = []
    for definition in file.definitions:
        if isinstance(definition, ForDef):
            topic = definition.body[0]
            assert isinstance(topic, TopicDef)
            assert topic.topology is not None
            topologies.append(topic.topology)
    first, second = topologies
    assert statement_summary(first) == ["replicate q Infra"]
    assert filter_of(first[0]) == "/from = '{{q}}'"
    assert statement_summary(second) == ["replicate I.{{s}} Infra"]
    assert filter_of(second[0]) == "/from = 'I.{{s}}'"


BLOCK = "topology b(from: group, to: role) { replicate from -> to } "


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("topology b() { mesh G }", "declares no parameters"),
        ("topology b(g: thing) { mesh g }", "unknown parameter kind 'thing'"),
        ("topology b(g: group, g: role) { mesh g }", "'g' is declared twice"),
        (
            BLOCK + "topic t { topology { use b } }",
            "takes arguments: write 'use b(from = ..., to = ...)'",
        ),
        (BLOCK + "topic t { topology { use b() } }", "passes nothing"),
        (
            BLOCK + "topic t { topology { use b(from = A) } }",
            "use of topology block 'b' is missing 'to'",
        ),
        (
            BLOCK + "topic t { topology { use b(from = A, dest = B) } }",
            "topology block 'b' has no parameter 'dest' (its parameters are from, to)",
        ),
        (BLOCK + "topic t { topology { use b(from = A, from = B) } }", "'from' is given twice"),
        (
            "topology b { mesh G } topic t { topology { use b(x = A) } }",
            "topology block 'b' takes no arguments: write 'use b'",
        ),
        ("topology b(r: role) { mesh r }", "'mesh r' needs a group; 'r' is a role parameter"),
        (
            "topology b(g: group) { owned by g { mesh G } }",
            "'owned by g' needs an instance; 'g' is a group parameter",
        ),
        (
            "topology b(g: group) { replicate g -> X where \"/k = '{{g.key}}'\" }",
            "'{{g.key}}': only a role parameter has attributes",
        ),
        (
            "topology b(r: role) { replicate X -> r where \"/k = '{{r.a.b}}'\" }",
            "a role parameter reads one attribute",
        ),
    ],
)
def test_rejects_malformed_parameters_and_arguments(source: str, message: str) -> None:
    assert message in parse_error(source)


def test_a_selector_fits_anywhere_and_a_parameter_may_share_a_loop_variables_name() -> None:
    parse("topology b(s: selector) { mesh s  replicate s -> X }")
    parse(
        """topology b(q: instance) { replicate q -> Infra }
           for q in Quoting { topic "t/{{q}}" { topology { use b(q = q) } } }"""
    )
