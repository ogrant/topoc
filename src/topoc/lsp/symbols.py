"""The outline of a `.topo` source: its definitions, as editors list them.

A definition's span runs from its name through its closing brace or bracket;
the name is its selection. A `for` lists what its body declares.
"""

from dataclasses import dataclass
from enum import Enum

from topoc.dsl import syntax
from topoc.lsp.text import Span, block_span, token_span


class SymbolKind(Enum):
    """What a symbol is, as the editor's icons show it."""

    ROLE = "role"
    ATTRIBUTE = "attribute"
    INSTANCE = "instance"
    TOPIC = "topic"
    QUEUE = "queue"
    LIST = "list"
    LOOP = "for"
    CONTEXT = "context"
    TYPE = "type"
    FIELD = "field"
    TOPOLOGY = "topology block"


@dataclass(frozen=True, slots=True)
class Symbol:
    name: str
    kind: SymbolKind
    span: Span
    selection: Span
    children: list["Symbol"]


def outline(file: syntax.SourceFile, source: str) -> list[Symbol]:
    """The symbols of `file`, parsed from `source`, in source order."""
    lines = source.split("\n")
    return [_symbol(definition, lines) for definition in file.definitions]


def _named(
    name: syntax.Ident, kind: SymbolKind, lines: list[str], children: list[Symbol]
) -> Symbol:
    return Symbol(
        name.text,
        kind,
        block_span(lines, name.location),
        token_span(lines, name.location),
        children,
    )


def _leaf(name: syntax.Ident, kind: SymbolKind, lines: list[str]) -> Symbol:
    span = token_span(lines, name.location)
    return Symbol(name.text, kind, span, span, [])


def _fields(fields: list[syntax.FieldDecl], lines: list[str]) -> list[Symbol]:
    return [_leaf(field.name, SymbolKind.FIELD, lines) for field in fields]


def _symbol(definition: syntax.Definition | syntax.ForItem, lines: list[str]) -> Symbol:
    match definition:
        case syntax.RoleDef(name=name, attrs=attrs):
            children = [_leaf(attr.name, SymbolKind.ATTRIBUTE, lines) for attr in attrs]
            return _named(name, SymbolKind.ROLE, lines, children)
        case syntax.InstanceDef(name=name):
            return _named(name, SymbolKind.INSTANCE, lines, [])
        case syntax.TopicDef(name=name):
            return _named(name, SymbolKind.TOPIC, lines, [])
        case syntax.QueueDef(name=name):
            return _named(name, SymbolKind.QUEUE, lines, [])
        case syntax.ListDef(name=name):
            return _named(name, SymbolKind.LIST, lines, [])
        case syntax.TopologyDef(name=name):
            return _named(name, SymbolKind.TOPOLOGY, lines, [])
        case syntax.ContextDef(location=location, types=types, fields=fields):
            children = [
                _named(t.name, SymbolKind.TYPE, lines, _fields(t.fields, lines)) for t in types
            ]
            keyword = token_span(lines, location)
            return Symbol(
                "context",
                SymbolKind.CONTEXT,
                block_span(lines, location),
                keyword,
                children + _fields(fields, lines),
            )
        case syntax.ForDef(location=location, variable=variable, value=value, domain=domain):
            variables = variable.text if value is None else f"{variable.text}, {value.text}"
            match domain:
                case syntax.Ident(text=text):
                    over = text
                case syntax.ContextPath():
                    over = str(domain)
                case _:
                    over = "[" + ", ".join(item.value for item in domain) + "]"
            return Symbol(
                f"for {variables} in {over}",
                SymbolKind.LOOP,
                block_span(lines, location),
                token_span(lines, location),
                [_symbol(item, lines) for item in definition.body],
            )
