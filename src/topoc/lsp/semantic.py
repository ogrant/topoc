"""Semantic tokens: what each name in a `.topo` is, for the editor to colour
it by, where the grammars can only guess from its spelling (a loop variable
named `instance` is not the keyword).

Names inside strings and `{{...}}` placeholders get no token, so they keep the
grammars' string and interpolation colours. The token types are topo's own,
so a theme's colours for standard types do not apply; the VS Code extension
maps each to the TextMate scope the grammar gives the same thing.
"""

from dataclasses import dataclass

from topoc.lsp.index import Index
from topoc.lsp.text import Span, embedded_regions

# The legend: the index of each type is its number in the encoding.
TOKEN_TYPES = [
    "role",
    "attribute",
    "instance",
    "group",
    "topic",
    "queue",
    "list",
    "topologyBlock",
    "contextField",
    "contextType",
    "contextTypeField",
    "loopVariable",
    "blockParameter",
]
TOKEN_MODIFIERS = ["declaration"]

# The index's entity kinds, by token type.
_TYPE_OF_KIND = {
    "role": "role",
    "attribute": "attribute",
    "instance": "instance",
    "group": "group",
    "topic": "topic",
    "queue": "queue",
    "list": "list",
    "topology": "topologyBlock",
    "context": "contextField",
    "type": "contextType",
    "field": "contextTypeField",
    "loop": "loopVariable",
    "parameter": "blockParameter",
}


@dataclass(frozen=True, slots=True)
class Token:
    span: Span
    token_type: str
    declaration: bool


def semantic_tokens(index: Index, source: str) -> list[Token]:
    """A token for every occurrence outside strings and placeholders, in
    source order."""
    regions = embedded_regions(source.split("\n"))
    tokens = []
    for occurrence in sorted(index.occurrences.values(), key=lambda o: o.span.start):
        start = occurrence.span.start
        if start.line < len(regions) and any(
            first <= start.column < end for first, end in regions[start.line]
        ):
            continue
        tokens.append(
            Token(occurrence.span, _TYPE_OF_KIND[occurrence.key[0]], occurrence.declaration)
        )
    return tokens
