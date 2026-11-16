"""Tests for completion: each case is a source with `|` at the cursor, the
candidates offered there, and the text they replace."""

import pytest

from topoc.dsl.parser import parse
from topoc.lsp.completion import Completion, complete
from topoc.lsp.text import Position

# The declarations completion offers, from the document's last version that
# parsed.
DECLARED = parse("""context {
  type site { workers: int  port_base: int }
  sites: site[string]
  region: string
}
role Basic { attr admin_port: int = 9090 }
role Quoting { attr key: string  attr max_orders: int }
instance Infra1 { roles [Basic] group Infra }
instance Infra2 { roles [Basic] group Infra }
instance Q1 { roles [Quoting] attr Quoting.key "a" attr Quoting.max_orders 1 }
list Strategies [a, b]
topology ha { mesh Infra }
topology fanout(from: group, to: role, at: selector) { replicate from -> to }
topic orders { definition c::T topology { local Q1 } }
topic fills { definition c::T topology { local Q1 } }
""")


def at(text: str) -> Completion:
    source = text.replace("|", "")
    before = text[: text.index("|")].split("\n")
    return complete(source, Position(len(before) - 1, len(before[-1])), DECLARED)


def labels(text: str) -> list[str]:
    return [candidate.label for candidate in at(text).candidates]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Statement keywords, by block.
        ("|", ["role", "topic", "queue", "instance", "list", "for", "context", "topology"]),
        ("topic t { | }", ["definition", "topology"]),
        ("topic t { topology { | } }", ["mesh", "replicate", "local", "sow", "owned", "use"]),
        (
            "topic t { topology { mesh Infra | } }",
            ["mesh", "replicate", "local", "sow", "owned", "use"],
        ),
        ("instance I { | }", ["roles", "group", "attr"]),
        ("for s in [a] { | }", ["topic", "queue", "instance", "for"]),
        ("topology x { owned by A { | } }", ["mesh", "replicate"]),
        ("topic t { topology { sow A { | } } }", ["durability"]),
        ("topic t { topology { sow A { durability persistent { | } } } }", ["file"]),
        # Names, by the keyword they follow.
        ("topic t { topology { mesh | } }", ["Infra"]),
        ("topic t { topology { mesh Infra, | } }", ["Infra"]),
        ("topic t { topology { use | } }", ["ha", "fanout"]),
        # A `use`'s arguments: the parameters not yet given, then names of
        # each parameter's kind.
        ("topic t { topology { use fanout(| } }", ["from", "to", "at"]),
        ("topic t { topology { use fanout(from = Infra, | } }", ["to", "at"]),
        ("topic t { topology { use fanout(from = | } }", ["Infra"]),
        ("topic t { topology { use fanout(to = | } }", ["Basic", "Quoting"]),
        (
            "for q in Quoting { topic t { topology { use fanout(at = | } } }",
            ["q", "Basic", "Quoting", "Infra", "Infra1", "Infra2", "Q1"],
        ),
        # A block's parameters, where their kind fits.
        ("topology b(g: group, r: role) { mesh | }", ["g", "Infra"]),
        (
            "topic t { topology { replicate Infra -> | } }",
            ["Basic", "Quoting", "Infra", "Infra1", "Infra2", "Q1"],
        ),
        (
            "topic t { topology { local | } }",
            ["Basic", "Quoting", "Infra", "Infra1", "Infra2", "Q1"],
        ),
        ("queue q { from | }", ["orders", "fills"]),
        ("queue q { from orders, | }", ["orders", "fills"]),
        ("instance I { roles [| }", ["Basic", "Quoting"]),
        ("instance I { roles [Basic, | }", ["Basic", "Quoting"]),
        ("instance I { group | }", ["Infra"]),
        ("instance I { roles [Quoting] attr | }", ["Quoting.key", "Quoting.max_orders"]),
        ("instance I { attr Quoting.| }", ["key", "max_orders"]),
        # Values.
        ("topic t { topology { sow A { durability | } } }", ["persistent", "transient"]),
        ("queue q { delivery | }", ["at_least_once", "at_most_once"]),
        (
            "queue q { delivery at_least_once { fairness | } }",
            ["fast", "round_robin", "proportional"],
        ),
        # `for` domains, and the variables in scope.
        (
            "for x in |",
            [
                "@sites",
                "@region",
                "Strategies",
                "Basic",
                "Quoting",
                "Infra",
                "Infra1",
                "Infra2",
                "Q1",
            ],
        ),
        (
            "for s in @sites { for w in | }",
            [
                "@sites",
                "@region",
                "s",
                "Strategies",
                "Basic",
                "Quoting",
                "Infra",
                "Infra1",
                "Infra2",
                "Q1",
            ],
        ),
        (
            "for q in Quoting { topic t { topology { replicate | } } }",
            ["q", "Basic", "Quoting", "Infra", "Infra1", "Infra2", "Q1"],
        ),
        # Context paths.
        ("for x in @|", ["sites", "region"]),
        ("for s in @sites { for w in s.| }", ["workers", "port_base"]),
        # Placeholders: in strings and bare.
        ('for s in @sites { topic "t/{{| }', ["s", "Basic", "Quoting"]),
        ('for s in @sites { topic "t/{{s.| }', ["workers", "port_base"]),
        ("topic t { topology { replicate A -> B where \"/r = '{{@| }", ["sites", "region"]),
        (
            "topic t { topology { replicate A -> Quoting where \"/o = '{{Quoting.| }",
            ["key", "max_orders"],
        ),
        ('for q in Quoting { topic "t/{{q.| }', ["Basic", "Quoting"]),
        ('for q in Quoting { topic "t/{{q.Quoting.| }', ["key", "max_orders"]),
        (
            'for s in @sites { instance "I.{{s}}" { attr Basic.admin_port {{s.| } }',
            ["workers", "port_base"],
        ),
        # Nothing inside a string's plain text, or after a comment.
        ('topic "or| ', []),
    ],
)
def test_candidates(text: str, expected: list[str]) -> None:
    assert labels(text) == expected


def test_candidates_replace_the_partial_word() -> None:
    completion = at("topic t { topology { mesh In| } }")
    assert completion.replace.start == Position(0, 26)
    assert completion.replace.end == Position(0, 28)
    # After `@` or `.`, only what follows them.
    completion = at("for x in @si|")
    assert (completion.replace.start, completion.replace.end) == (Position(0, 10), Position(0, 12))
    completion = at("instance I { attr Quoting.ke| }")
    assert completion.replace.start == Position(0, 26)


def test_owned_inserts_owned_by() -> None:
    [owned] = [c for c in at("queue q { topology { | } }").candidates if c.label == "owned"]
    assert owned.insert == "owned by "


def test_comments_are_ignored() -> None:
    assert labels("topic t { // mesh\n  topology { mesh | } }") == ["Infra"]
    assert labels("// topic t {\n|") == labels("|")


def test_parameters_insert_their_equals() -> None:
    [first, *_] = at("topic t { topology { use fanout(| } }").candidates
    assert (first.label, first.insert) == ("from", "from = ")
