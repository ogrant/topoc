"""Tests for semantic tokens: every name outside strings and placeholders gets
the type of what it is, whatever it is spelled like."""

from topoc.dsl.parser import parse
from topoc.lsp.index import build_index
from topoc.lsp.semantic import semantic_tokens

SOURCE = """context { sites: string[] }
role Quoting { attr key: string }
instance Infra1 { roles [Quoting] attr Quoting.key "a" group Infra }
topology ha { mesh Infra }
for instance in Quoting {
  topic "t/{{instance}}" { definition c::T topology { use ha  replicate instance -> Infra } }
}
for site in @sites { topic "s/{{site}}" { definition c::T topology { local Infra1 } } }
"""


def tokens() -> list[tuple[int, str, str, bool]]:
    lines = SOURCE.split("\n")
    return [
        (
            token.span.start.line,
            lines[token.span.start.line][token.span.start.column : token.span.end.column],
            token.token_type,
            token.declaration,
        )
        for token in semantic_tokens(build_index(parse(SOURCE), SOURCE, None), SOURCE)
    ]


def test_names_get_the_type_of_what_they_are() -> None:
    assert tokens() == [
        (0, "sites", "contextField", True),
        (1, "Quoting", "role", True),
        (1, "key", "attribute", True),
        (2, "Infra1", "instance", True),
        (2, "Quoting", "role", False),
        (2, "Quoting", "role", False),
        (2, "key", "attribute", False),
        (2, "Infra", "group", True),
        (3, "ha", "topologyBlock", True),
        (3, "Infra", "group", False),
        # A loop variable spelled like a keyword is a loop variable.
        (4, "instance", "loopVariable", True),
        (4, "Quoting", "role", False),
        # Nothing inside "t/{{instance}}": it keeps the string's colours.
        (5, "ha", "topologyBlock", False),
        (5, "instance", "loopVariable", False),
        (5, "Infra", "group", False),
        (7, "site", "loopVariable", True),
        (7, "@sites", "contextField", False),
        (7, "Infra1", "instance", False),
    ]


def test_block_parameters_are_tokens() -> None:
    source = "topology b(g: group) { mesh g }\n"
    tokens = semantic_tokens(build_index(parse(source), source, None), source)
    assert [(t.span.start.column, t.token_type, t.declaration) for t in tokens] == [
        (9, "topologyBlock", True),
        (11, "blockParameter", True),
        (28, "blockParameter", False),
    ]
