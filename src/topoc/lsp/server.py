"""The `.topo` language server (`topoc lsp`), over stdio.

Each open document is compiled as `topoc check` would, against the context data
found for it (see `context_discovery`), whenever it is opened, changed or
saved; the result is published as diagnostics. The outline lists its
definitions. Go to definition, find references, highlighting a name's
occurrences, hover and semantic tokens read the document's `index`, which
needs it to parse; while it does not, the last tokens stand. Completion reads
the text before the cursor, and the declarations of the document's last
version that parsed.

Initialization options:

- `contextFiles`: an object mapping topology paths to context JSON files, both
  absolute or relative to the workspace root, for topologies whose context is
  not found by convention.
"""

from collections.abc import Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Any

from lsprotocol import types
from pygls.lsp.server import LanguageServer
from pygls.uris import to_fs_path
from pygls.workspace import TextDocument

from topoc.dsl import syntax
from topoc.dsl.parser import parse
from topoc.errors import CompileError
from topoc.lsp.completion import Kind, complete
from topoc.lsp.context_discovery import ContextFound, ContextLookup, find_context
from topoc.lsp.diagnostics import Severity, check
from topoc.lsp.index import Index, Occurrence, build_index
from topoc.lsp.semantic import TOKEN_MODIFIERS, TOKEN_TYPES, semantic_tokens
from topoc.lsp.symbols import Symbol, SymbolKind, outline
from topoc.lsp.text import Position, Span

_SEVERITY = {
    Severity.ERROR: types.DiagnosticSeverity.Error,
    Severity.INFORMATION: types.DiagnosticSeverity.Information,
}

_SYMBOL_KIND = {
    SymbolKind.ROLE: types.SymbolKind.Class,
    SymbolKind.ATTRIBUTE: types.SymbolKind.Property,
    SymbolKind.INSTANCE: types.SymbolKind.Object,
    SymbolKind.TOPIC: types.SymbolKind.Event,
    SymbolKind.QUEUE: types.SymbolKind.Event,
    SymbolKind.LIST: types.SymbolKind.Array,
    SymbolKind.LOOP: types.SymbolKind.Namespace,
    SymbolKind.CONTEXT: types.SymbolKind.Struct,
    SymbolKind.TYPE: types.SymbolKind.Struct,
    SymbolKind.FIELD: types.SymbolKind.Field,
    SymbolKind.TOPOLOGY: types.SymbolKind.Module,
}


class TopoServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__("topoc", version("topoc"))
        # Absolute topology path -> its configured context file.
        self.context_files: Mapping[Path, Path] = {}
        # The last semantic tokens of each document, kept while it does not
        # parse.
        self.tokens: dict[str, list[int]] = {}
        # The last syntax tree of each document that parsed, whose
        # declarations completion offers while the text being typed does not.
        self.files: dict[str, syntax.SourceFile] = {}


server = TopoServer()


@server.feature(types.INITIALIZE)
def initialize(ls: TopoServer, params: types.InitializeParams) -> None:
    root = _root(params)
    options: Any = params.initialization_options
    configured = options.get("contextFiles", {}) if isinstance(options, dict) else {}
    ls.context_files = {
        _absolute(root, topology): _absolute(root, context)
        for topology, context in configured.items()
        if isinstance(topology, str) and isinstance(context, str)
    }


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: TopoServer, params: types.DidOpenTextDocumentParams) -> None:
    _publish(ls, params.text_document.uri)


@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: TopoServer, params: types.DidChangeTextDocumentParams) -> None:
    _publish(ls, params.text_document.uri)


@server.feature(types.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: TopoServer, params: types.DidSaveTextDocumentParams) -> None:
    _publish(ls, params.text_document.uri)


@server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: TopoServer, params: types.DidCloseTextDocumentParams) -> None:
    ls.tokens.pop(params.text_document.uri, None)
    ls.files.pop(params.text_document.uri, None)
    ls.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(uri=params.text_document.uri, diagnostics=[])
    )


@server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def document_symbol(
    ls: TopoServer, params: types.DocumentSymbolParams
) -> list[types.DocumentSymbol]:
    document = ls.workspace.get_text_document(params.text_document.uri)
    try:
        file = parse(document.source)
    except CompileError:
        return []
    return [_document_symbol(document, symbol) for symbol in outline(file, document.source)]


@server.feature(types.TEXT_DOCUMENT_DEFINITION)
def definition(ls: TopoServer, params: types.DefinitionParams) -> list[types.Location]:
    found = _occurrence(ls, params.text_document.uri, params.position)
    if found is None:
        return []
    document, index, occurrence = found
    return [
        types.Location(uri=document.uri, range=_range(document, span))
        for span in index.entities[occurrence.key].declarations
    ]


@server.feature(types.TEXT_DOCUMENT_REFERENCES)
def references(ls: TopoServer, params: types.ReferenceParams) -> list[types.Location]:
    found = _occurrence(ls, params.text_document.uri, params.position)
    if found is None:
        return []
    document, index, occurrence = found
    return [
        types.Location(uri=document.uri, range=_range(document, other.span))
        for other in index.references(occurrence.key)
        if params.context.include_declaration or not other.declaration
    ]


@server.feature(types.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT)
def document_highlight(
    ls: TopoServer, params: types.DocumentHighlightParams
) -> list[types.DocumentHighlight]:
    found = _occurrence(ls, params.text_document.uri, params.position)
    if found is None:
        return []
    document, index, occurrence = found
    return [
        types.DocumentHighlight(
            range=_range(document, other.span),
            kind=types.DocumentHighlightKind.Write
            if other.declaration
            else types.DocumentHighlightKind.Read,
        )
        for other in index.references(occurrence.key)
    ]


@server.feature(types.TEXT_DOCUMENT_HOVER)
def hover(ls: TopoServer, params: types.HoverParams) -> types.Hover | None:
    found = _occurrence(ls, params.text_document.uri, params.position)
    if found is None:
        return None
    document, index, occurrence = found
    return types.Hover(
        contents=types.MarkupContent(
            kind=types.MarkupKind.Markdown, value=index.entities[occurrence.key].hover
        ),
        range=_range(document, occurrence.span),
    )


@server.feature(
    types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    types.SemanticTokensLegend(token_types=TOKEN_TYPES, token_modifiers=TOKEN_MODIFIERS),
)
def semantic_tokens_full(
    ls: TopoServer, params: types.SemanticTokensParams
) -> types.SemanticTokens:
    uri = params.text_document.uri
    document = ls.workspace.get_text_document(uri)
    index = _index(ls, document)
    if index is None:
        return types.SemanticTokens(data=ls.tokens.get(uri, []))
    data: list[int] = []
    line, column = 0, 0
    for token in semantic_tokens(index, document.source):
        span = _range(document, token.span)
        if span.start.line != span.end.line:
            continue
        delta_line = span.start.line - line
        delta_column = span.start.character - (column if delta_line == 0 else 0)
        data += [
            delta_line,
            delta_column,
            span.end.character - span.start.character,
            TOKEN_TYPES.index(token.token_type),
            1 if token.declaration else 0,
        ]
        line, column = span.start.line, span.start.character
    ls.tokens[uri] = data
    return types.SemanticTokens(data=data)


def _parse(ls: TopoServer, document: TextDocument) -> syntax.SourceFile | None:
    """`document`'s syntax tree, kept as its last that parsed; `None` if it
    does not parse."""
    try:
        file = parse(document.source)
    except CompileError:
        return None
    ls.files[document.uri] = file
    return file


def _index(ls: TopoServer, document: TextDocument) -> Index | None:
    """The index of `document`, or `None` if it does not parse."""
    file = _parse(ls, document)
    if file is None:
        return None
    found = _context(ls, document.uri)
    return build_index(
        file, document.source, found.context if isinstance(found, ContextFound) else None
    )


def _occurrence(
    ls: TopoServer, uri: str, position: types.Position
) -> tuple[TextDocument, Index, Occurrence] | None:
    """The document at `uri`, its index, and the occurrence at `position`;
    `None` if it does not parse or no name is there."""
    document = ls.workspace.get_text_document(uri)
    index = _index(ls, document)
    if index is None:
        return None
    at = document.position_codec.position_from_client_units(document.lines, position)
    occurrence = index.at(Position(at.line, at.character))
    return None if occurrence is None else (document, index, occurrence)


def _context(ls: TopoServer, uri: str) -> ContextLookup:
    path = to_fs_path(uri) if uri.startswith("file:") else None
    topology = Path(path).resolve() if path is not None else None
    return find_context(topology, ls.context_files)


_COMPLETION_KIND = {
    Kind.KEYWORD: types.CompletionItemKind.Keyword,
    Kind.VALUE: types.CompletionItemKind.EnumMember,
    Kind.ROLE: types.CompletionItemKind.Class,
    Kind.ATTRIBUTE: types.CompletionItemKind.Property,
    Kind.INSTANCE: types.CompletionItemKind.Value,
    Kind.GROUP: types.CompletionItemKind.Module,
    Kind.TOPIC: types.CompletionItemKind.Event,
    Kind.QUEUE: types.CompletionItemKind.Event,
    Kind.LIST: types.CompletionItemKind.Enum,
    Kind.TOPOLOGY: types.CompletionItemKind.Snippet,
    Kind.CONTEXT_FIELD: types.CompletionItemKind.Constant,
    Kind.FIELD: types.CompletionItemKind.Field,
    Kind.VARIABLE: types.CompletionItemKind.Variable,
    Kind.PARAMETER: types.CompletionItemKind.TypeParameter,
}


@server.feature(
    types.TEXT_DOCUMENT_COMPLETION,
    types.CompletionOptions(trigger_characters=[".", "@"]),
)
def completion(ls: TopoServer, params: types.CompletionParams) -> types.CompletionList:
    uri = params.text_document.uri
    document = ls.workspace.get_text_document(uri)
    file = _parse(ls, document) or ls.files.get(uri)
    at = document.position_codec.position_from_client_units(document.lines, params.position)
    found = complete(document.source, Position(at.line, at.character), file)
    replace = _range(document, found.replace)
    return types.CompletionList(
        is_incomplete=False,
        items=[
            types.CompletionItem(
                label=candidate.label,
                kind=_COMPLETION_KIND[candidate.kind],
                detail=candidate.kind.value,
                text_edit=types.TextEdit(
                    range=replace, new_text=candidate.insert or candidate.label
                ),
            )
            for candidate in found.candidates
        ],
    )


def _publish(ls: TopoServer, uri: str) -> None:
    document = ls.workspace.get_text_document(uri)
    _parse(ls, document)
    problems = check(document.source, _context(ls, uri))
    ls.text_document_publish_diagnostics(
        types.PublishDiagnosticsParams(
            uri=uri,
            version=document.version,
            diagnostics=[
                types.Diagnostic(
                    range=_range(document, problem.span),
                    message=problem.message,
                    severity=_SEVERITY[problem.severity],
                    source="topoc",
                )
                for problem in problems
            ],
        )
    )


def _document_symbol(document: TextDocument, symbol: Symbol) -> types.DocumentSymbol:
    return types.DocumentSymbol(
        name=symbol.name,
        detail=symbol.kind.value,
        kind=_SYMBOL_KIND[symbol.kind],
        range=_range(document, symbol.span),
        selection_range=_range(document, symbol.selection),
        children=[_document_symbol(document, child) for child in symbol.children],
    )


def _range(document: TextDocument, span: Span) -> types.Range:
    """`span`, in code points, in the client's position encoding."""
    lines = document.lines

    def position(line: int, column: int) -> types.Position:
        if line >= len(lines):
            return types.Position(line=line, character=column)
        return document.position_codec.position_to_client_units(
            lines, types.Position(line=line, character=column)
        )

    return types.Range(
        start=position(span.start.line, span.start.column),
        end=position(span.end.line, span.end.column),
    )


def _root(params: types.InitializeParams) -> Path | None:
    if params.workspace_folders:
        path = to_fs_path(params.workspace_folders[0].uri)
    elif params.root_uri is not None:
        path = to_fs_path(params.root_uri)
    else:
        path = None
    return None if path is None else Path(path)


def _absolute(root: Path | None, path: str) -> Path:
    given = Path(path).expanduser()
    return (given if given.is_absolute() or root is None else root / given).resolve()


def serve() -> None:
    """Serve over stdin and stdout until the client exits."""
    server.start_io()
