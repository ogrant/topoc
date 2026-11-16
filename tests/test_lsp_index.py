"""Tests for the language server's index: what each name links to, found by
position, and what hovering it shows."""

from topoc.context import Context
from topoc.dsl.parser import parse
from topoc.lsp.index import Index, Key, build_index
from topoc.lsp.text import Position

SOURCE = """context {
  type site { workers: int  port_base: int }
  sites: site[string]
  region: string
}
role Basic { attr admin_port: int = 9090 }
role Quoting { attr key: string }
instance Infra1 { roles [Basic] group Infra }
instance Infra2 { roles [Basic] group Infra }
topology ha { mesh Infra }
for s in @sites {
  instance "Q.{{s}}" {
    roles [Basic, Quoting]
    attr Quoting.key "{{s}}"
    attr Basic.admin_port {{s.port_base}}
  }
  for w in s.workers {
    topic "work/{{s}}/{{w}}" { definition c::T topology { use ha  local "Q.{{s}}" } }
  }
}
for q in Quoting {
  topic "orders/{{q.Quoting.key}}" {
    definition c::T
    topology { replicate q -> Infra where "/r = '{{@region}}'" }
  }
}
topic quotes {
  definition c::Q
  topology { use ha  replicate Infra -> Quoting where "/o = '{{Quoting.key}}'" }
}
"""

CONTEXT = Context.from_json(
    '{ "sites": { "LN": { "workers": 2, "port_base": 1 }, "NY": { "workers": 1, '
    '"port_base": 2 } }, "region": "emea" }'
)

LINES = SOURCE.split("\n")


def index() -> Index:
    return build_index(parse(SOURCE), SOURCE, CONTEXT)


def at(text: str, line_containing: str, nth: int = 0) -> Position:
    """The position of the `nth` `text` on the first line containing
    `line_containing`."""
    row = next(i for i, line in enumerate(LINES) if line_containing in line)
    column = -1
    for _ in range(nth + 1):
        column = LINES[row].index(text, column + 1)
    return Position(row, column)


def key_at(text: str, line_containing: str, nth: int = 0) -> Key:
    occurrence = index().at(at(text, line_containing, nth))
    assert occurrence is not None, (text, line_containing)
    return occurrence.key


def texts(key: Key) -> list[str]:
    """The text of every occurrence of `key`, in order, `*` marking
    declarations."""
    return [
        ("*" if o.declaration else "")
        + LINES[o.span.start.line][o.span.start.column : o.span.end.column]
        for o in index().references(key)
    ]


def test_names_link_to_their_declarations() -> None:
    assert key_at("Basic", "instance Infra1") == ("role", "Basic")
    assert texts(("role", "Basic")) == ["*Basic", "Basic", "Basic", "Basic", "Basic"]
    assert key_at("Infra", "topology ha") == ("group", "Infra")
    assert texts(("group", "Infra")) == ["*Infra", "*Infra", "Infra", "Infra", "Infra"]
    assert key_at("ha", "use ha  replicate") == ("topology", "ha")
    assert texts(("topology", "ha")) == ["*ha", "ha", "ha"]
    assert key_at("Quoting", "for q in") == ("role", "Quoting")


def test_attributes_link_through_their_role() -> None:
    assert key_at("key", "attr Quoting.key") == ("attribute", "Quoting", "key")
    assert key_at("admin_port", "attr Basic.admin_port") == ("attribute", "Basic", "admin_port")
    # Fan-out and instance-loop placeholders.
    assert key_at("key", "/o = ") == ("attribute", "Quoting", "key")
    assert key_at("key", 'topic "orders') == ("attribute", "Quoting", "key")
    assert key_at("Quoting", 'topic "orders') == ("role", "Quoting")
    assert texts(("attribute", "Quoting", "key")) == ["*key", "key", "key", "key"]


def test_context_paths_follow_their_types() -> None:
    assert key_at("@sites", "for s in") == ("context", "sites")
    assert key_at("@region", "replicate q") == ("context", "region")
    # `s` stands for a site: its fields are the type's.
    assert key_at("workers", "for w in") == ("field", "site", "workers")
    assert key_at("port_base", "attr Basic.admin_port") == ("field", "site", "port_base")
    assert key_at("site[", "sites: site[string]") == ("type", "site")


def test_loop_variables_are_scoped_to_their_for() -> None:
    s = key_at("s", "for s in")
    assert s[0] == "loop"
    assert key_at("s}}", 'instance "Q.') == s
    assert key_at("s}}", 'local "Q.', 1) == s
    assert texts(s) == ["*s", "s", "s", "s", "s", "s", "s"]
    q = key_at("q", "replicate q")
    assert q == key_at("q", "for q in")
    assert key_at("w}}", "{{w}}") == key_at("w", "for w in")


def test_positions_inside_or_at_the_end_of_a_name() -> None:
    position = at("Quoting", "role Quoting")
    inside = Position(position.line, position.column + 3)
    end = Position(position.line, position.column + len("Quoting"))
    assert index().at(inside) is not None
    assert index().at(end) is not None
    assert index().at(Position(position.line, 0)) is None  # the `role` keyword


def test_hovers() -> None:
    entities = index().entities
    assert entities[("role", "Basic")].hover == (
        "```topo\nrole Basic {\n  attr admin_port: int = 9090\n}\n```\n\n"
        "Carried by: `Infra1`, `Infra2`, `Q.{{s}}`"
    )
    assert entities[("group", "Infra")].hover == (
        "```topo\ngroup Infra\n```\n\nMembers: `Infra1`, `Infra2`"
    )
    assert entities[("topology", "ha")].hover == "```topo\ntopology ha { mesh Infra }\n```"
    assert entities[key_at("s", "for s in")].hover == (
        "```topo\nfor s in @sites\n```\n\nValues: `LN`, `NY`"
    )
    assert entities[key_at("q", "for q in")].hover == (
        "```topo\nfor q in Quoting\n```\n\nValues: `Q.{{s}}`"
    )
    assert entities[("field", "site", "workers")].hover == (
        "```topo\nworkers: int\n```\n\nField of context type `site`."
    )
    assert entities[("instance", "Infra1")].hover == (
        "```topo\ninstance Infra1 {\n  roles [Basic]\n  group Infra\n}\n```"
    )
    assert entities[("topic", "quotes")].hover == "```topo\ntopic quotes\n```\n\nDefinition: `c::Q`"


PARAMS = """role Quoting { attr key: string }
instance Infra1 { group Infra }
instance Q1 { roles [Quoting] attr Quoting.key "a" }
topology fanout(from: group, to: role) {
  replicate from -> to where "/o = '{{to.key}}'"
}
topic t { definition c::T topology { use fanout(from = Infra, to = Quoting) } }
"""


def test_block_parameters_link_their_uses() -> None:
    index = build_index(parse(PARAMS), PARAMS, None)
    lines = PARAMS.split("\n")

    def occurrences(key: Key) -> list[tuple[int, str, bool]]:
        return [
            (
                o.span.start.line,
                lines[o.span.start.line][o.span.start.column : o.span.end.column],
                o.declaration,
            )
            for o in index.references(key)
        ]

    # Declared in the header, used in the body (as a name and in a
    # placeholder) and named at the use.
    assert occurrences(("parameter", "fanout", "to")) == [
        (3, "to", True),
        (4, "to", False),
        (4, "to", False),
        (6, "to", False),
    ]
    # The arguments link to what they name, at the use.
    assert (6, "Infra", False) in occurrences(("group", "Infra"))
    assert (6, "Quoting", False) in occurrences(("role", "Quoting"))
    assert index.entities[("parameter", "fanout", "from")].hover == (
        "```topo\nfrom: group\n```\n\nParameter of topology block `fanout`."
    )
