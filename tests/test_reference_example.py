"""Parses and resolves the reference example (testdata/reference.topo, against
its context in testdata/reference.context.json) and checks both the syntax tree
and the fully fanned-out model, including the instances and topics its `for`
templates expand to.

Methodology: an integration test of the front half of the pipeline on a
realistic file. Rather than comparing whole structures, it spot-checks the
facts the example exists to demonstrate (explicit versus implicit groups, role
fan-out, per-target filter interpolation, attribute defaults, template expansion
over a context list, a role and a list). The golden test covers the complete generated
output.
"""

from collections import Counter

from helpers import instance, read_testdata, reference_context

from topoc.dsl.parser import parse
from topoc.dsl.syntax import (
    AttrType,
    ContextDef,
    ContextPath,
    Definition,
    ForDef,
    Ident,
    InstanceDef,
    ListDef,
    LocalStmt,
    MeshStmt,
    PersistentDurability,
    QueueDef,
    RecoveryPoint,
    ReplicateStmt,
    RoleDef,
    SowStmt,
    TopicDef,
    TransientDurability,
    TypeKind,
)
from topoc.model import AttrKey, EdgeKind
from topoc.model import PersistentDurability as ModelPersistent
from topoc.resolve import compile_topology

type _Named = RoleDef | TopicDef | InstanceDef | ListDef


def find_definition[T: _Named](definitions: list[Definition], kind: type[T], name: str) -> T:
    """The definition of type `kind` named `name`; raises `StopIteration`
    (failing the test) if there is none."""
    return next(d for d in definitions if isinstance(d, kind) and d.name.text == name)


def test_parses() -> None:
    """The syntax tree: definition counts per kind, the role default, every
    statement kind the topics use, a definition path, the list and `for`
    templates as written, and the instances' group clauses and attribute
    assignments."""
    definitions = parse(read_testdata("reference.topo")).definitions
    assert Counter(type(d) for d in definitions) == {
        ContextDef: 1,
        RoleDef: 2,
        TopicDef: 6,
        InstanceDef: 3,
        ListDef: 1,
        ForDef: 3,
        QueueDef: 2,
    }

    # The context declares the quoting locations, and a loop over them declares
    # the instances.
    context = next(d for d in definitions if isinstance(d, ContextDef))
    assert [f.name.text for f in context.fields] == ["quoting_locations"]
    assert context.fields[0].type_expr.kind is TypeKind.LIST

    # The two templates: one over a role's instances, one over a list.
    strategies = find_definition(definitions, ListDef, "Strategies")
    assert [item.value for item in strategies.items] == ["momentum", "arbitrage"]
    locations, orders, _ = (d for d in definitions if isinstance(d, ForDef))
    assert locations.variable.text == "loc"
    assert isinstance(locations.domain, ContextPath)
    assert locations.domain.path == "quoting_locations"
    quoting = locations.body[0]
    assert isinstance(quoting, InstanceDef)
    assert quoting.name.text == "Quoting.{{loc}}"
    assert quoting.group is None
    assert [(a.role.text, a.attr.text, a.value) for a in quoting.attrs] == [
        ("Quoting", "key", "{{loc}}")
    ]

    assert orders.variable.text == "q"
    assert isinstance(orders.domain, Ident)
    assert orders.domain.text == "Quoting"
    template = orders.body[0]
    assert isinstance(template, TopicDef)
    assert template.name.text == "orders/{{q.Quoting.key}}"
    assert template.topology is not None
    replicate_orders = template.topology[1]
    assert isinstance(replicate_orders, ReplicateStmt)
    assert replicate_orders.source.text == "q"

    basic = find_definition(definitions, RoleDef, "Basic")
    assert basic.attrs[0].name.text == "admin_port"
    assert basic.attrs[0].attr_type is AttrType.INT
    assert basic.attrs[0].default == 9090

    tvgrids = find_definition(definitions, TopicDef, "tvgrids").topology
    assert tvgrids is not None
    mesh, replicate, sow = tvgrids
    assert isinstance(mesh, MeshStmt)
    assert mesh.group.text == "Infra"
    assert isinstance(replicate, ReplicateStmt)
    assert replicate.filter is not None
    assert replicate.filter.value == "/owner LIKE '{{Quoting.key}}/.*'"
    assert isinstance(sow, SowStmt)
    assert isinstance(sow.durability, PersistentDurability)
    assert sow.durability.file.value == "${AMPS_DIR}/sow/%n.sow"

    quotes = find_definition(definitions, TopicDef, "quotes").topology
    assert quotes is not None
    local, quotes_sow = quotes
    assert isinstance(local, LocalStmt)
    assert local.target.text == "Quoting"
    assert isinstance(quotes_sow, SowStmt)
    assert isinstance(quotes_sow.durability, TransientDurability)
    assert quotes_sow.durability.recovery_point is RecoveryPoint.NOW

    microprices = find_definition(definitions, TopicDef, "microprices").definition
    assert microprices is not None
    assert microprices.text == "market::topics::MICROPRICES"

    infra1 = find_definition(definitions, InstanceDef, "Infra1")
    assert infra1.group is not None
    assert infra1.group.text == "Infra"


def test_resolves() -> None:
    """The resolved model: topics (expanded ones in place of their `for` block),
    groups (one explicit pair, implicit singletons), Infra1's placements and the
    edge counts its topology implies, per-target interpolated filters for London
    and New York, London's own orders topic, and attribute values from both
    assignment and default."""
    system = compile_topology(read_testdata("reference.topo"), reference_context())

    assert [t.name for t in system.topics] == [
        "admin",
        "refdata",
        "microprices",
        "ownership",
        "tvgrids",
        "quotes",
        "orders/LN",
        "orders/NY",
        "signals/momentum",
        "signals/arbitrage",
    ]
    assert system.topics[4].definition == "market::topics::TVGRIDS"
    assert system.topics[6].definition == "market::topics::ORDERS"

    assert [(g.name, g.members, g.is_explicit) for g in system.groups] == [
        ("Infra", ["Infra1", "Infra2"], True),
        ("Admin", ["Admin"], False),
        ("Quoting.LN", ["Quoting.LN"], False),
        ("Quoting.NY", ["Quoting.NY"], False),
    ]

    infra1 = instance(system, "Infra1")
    assert infra1.group == "Infra"
    assert infra1.topics == [
        "microprices",
        "orders/LN",
        "orders/NY",
        "ownership",
        "refdata",
        "signals/arbitrage",
        "signals/momentum",
        "tvgrids",
    ]
    # One mesh edge per hosted topic (8), plus the role fan-out of refdata,
    # microprices and tvgrids to both quoters (6).
    assert len(infra1.destinations) == 14
    # Mesh from Infra2 for all 8 topics, plus each quoter's own orders topic.
    assert Counter(i.source for i in infra1.incoming) == {
        "Infra2": 8,
        "Quoting.LN": 1,
        "Quoting.NY": 1,
    }
    tvgrids_sow = infra1.sows[3]
    assert tvgrids_sow.topic == "tvgrids"
    assert tvgrids_sow.durability == ModelPersistent("${AMPS_DIR}/sow/%n.sow")

    to_london = next(
        d
        for d in infra1.destinations
        if d.topic == "tvgrids" and d.target_members == ["Quoting.LN"]
    )
    assert to_london.target_group == "Quoting.LN"
    assert to_london.filter == "/owner LIKE 'LN/.*'"

    london = instance(system, "Quoting.LN")
    assert london.group == "Quoting.LN"
    assert london.roles == ["Basic", "Quoting"]
    assert london.topics == ["microprices", "orders/LN", "quotes", "refdata", "tvgrids"]
    # London publishes only its own orders topic, to the Infra pair.
    (to_infra,) = london.destinations
    assert (to_infra.topic, to_infra.kind, to_infra.target_group, to_infra.target_members) == (
        "orders/LN",
        EdgeKind.REPLICATE,
        "Infra",
        ["Infra1", "Infra2"],
    )
    assert len(london.incoming) == 6
    assert london.attrs[AttrKey("Quoting", "key")] == "LN"
    assert london.attrs[AttrKey("Basic", "admin_port")] == 9090

    new_york = instance(system, "Quoting.NY")
    assert any(
        i.topic == "tvgrids" and i.filter == "/owner LIKE 'NY/.*'" for i in new_york.incoming
    )
