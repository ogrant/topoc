"""Tests for the language server: diagnostics and their ranges, context
discovery, and the outline, as plain functions; then `topoc lsp` end to end
over stdio, as an editor drives it."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from topoc.dsl.parser import parse
from topoc.lsp.context_discovery import (
    ContextFound,
    ContextNotFound,
    ContextUnreadable,
    find_context,
)
from topoc.lsp.diagnostics import Problem, Severity, check
from topoc.lsp.symbols import SymbolKind, outline
from topoc.lsp.text import Position, Span

NO_CONTEXT = ContextNotFound([])

VALID = """role R { attr key: string }
instance I { roles [R] attr R.key "a" }
topic t { definition c::T topology { local I } }
"""


def span(line: int, start: int, end: int) -> Span:
    return Span(Position(line, start), Position(line, end))


def only(problems: list[Problem]) -> Problem:
    assert len(problems) == 1, problems
    return problems[0]


# ---- diagnostics ------------------------------------------------------------


def test_a_valid_topology_has_no_diagnostics() -> None:
    assert check(VALID, NO_CONTEXT) == []


def test_errors_cover_their_token_without_repeating_the_location() -> None:
    problem = only(check(VALID.replace("local I", "local Nowhere"), NO_CONTEXT))
    assert problem.severity is Severity.ERROR
    assert problem.message == "unknown name 'Nowhere'"
    assert problem.span == span(2, 43, 50)
    # A quoted name: the whole string.
    problem = only(check('topic "a/b" { definition c::T }', NO_CONTEXT))
    assert problem.span == span(0, 6, 11)


def test_a_parse_error_and_the_end_of_input() -> None:
    problem = only(check("role R { attr }", NO_CONTEXT))
    assert problem.message == "expected attribute name, found '}'"
    assert problem.span == span(0, 14, 15)
    problem = only(check("role R {\n", NO_CONTEXT))
    assert problem.message.startswith("unexpected end of input")
    assert problem.span == span(1, 0, 0)


def test_an_iteration_note_stays() -> None:
    problem = only(
        check(
            'for s in [a] { topic "t/{{s}}" { definition c::T topology { local X } } }',
            NO_CONTEXT,
        )
    )
    assert problem.message == (
        "unknown name 'X' (in topic 't/a', from the 'for' at line 1, column 1 with s = 'a')"
    )


CONTEXT_TOPO = """context { sites: string[] }
instance I { }
for s in @sites { topic "t/{{s}}" { definition c::T topology { local I } } }
"""


def test_without_context_data_the_declaration_is_still_checked() -> None:
    problem = only(check(CONTEXT_TOPO, ContextNotFound([Path("/x/a.context.json")])))
    assert problem.severity is Severity.INFORMATION
    assert problem.message == (
        "no context data found (looked for /x/a.context.json): loops over the context are not "
        "expanded or checked"
    )
    assert problem.span == span(0, 0, 7)
    problem = only(check("context { a: nope }", NO_CONTEXT))
    assert problem.severity is Severity.ERROR
    assert "nope" in problem.message


def test_context_data_problems_sit_on_the_context_block(tmp_path: Path) -> None:
    data = tmp_path / "a.context.json"
    data.write_text('{ "sites": [1] }')
    found = find_context(tmp_path / "a.topo", {})
    assert isinstance(found, ContextFound)
    problem = only(check(CONTEXT_TOPO, found))
    assert problem.message == f"{data}: context: sites[0]: expected 'string', got 1"
    assert problem.span == span(0, 0, 7)
    problem = only(check(CONTEXT_TOPO, ContextUnreadable(data, "not JSON")))
    assert problem.message == f"{data}: not JSON"


# ---- context discovery ------------------------------------------------------


def test_context_discovery_order(tmp_path: Path) -> None:
    topology = tmp_path / "system.topo"
    beside = tmp_path / "system.context.json"
    env = tmp_path / "env.yaml"
    configured = tmp_path / "elsewhere.json"

    assert find_context(topology, {}) == ContextNotFound([beside, env])
    assert find_context(None, {}) == ContextNotFound([])

    env.write_text("tier: test\ncontext:\n  sites: [b, a]\n")
    found = find_context(topology, {})
    assert isinstance(found, ContextFound)
    assert found.path == env
    assert found.context.members == [("sites", ["b", "a"])]

    beside.write_text('{ "sites": ["c"] }')
    found = find_context(topology, {})
    assert isinstance(found, ContextFound)
    assert found.path == beside

    configured.write_text('{ "sites": ["d"] }')
    found = find_context(topology, {topology: configured})
    assert isinstance(found, ContextFound)
    assert found.path == configured


def test_unreadable_context_files(tmp_path: Path) -> None:
    topology = tmp_path / "a.topo"
    (tmp_path / "env.yaml").write_text("tier: test\n")
    # An env.yaml without a context section is not a context.
    assert isinstance(find_context(topology, {}), ContextNotFound)
    (tmp_path / "env.yaml").write_text("context: [unclosed\n")
    assert isinstance(find_context(topology, {}), ContextUnreadable)
    (tmp_path / "a.context.json").write_text("{")
    found = find_context(topology, {})
    assert isinstance(found, ContextUnreadable)
    assert found.path == tmp_path / "a.context.json"


# ---- outline ----------------------------------------------------------------


def test_outline_lists_definitions_and_loop_bodies() -> None:
    source = """context {
  type site { workers: int }
  sites: site[string]
}
role R { attr key: string }
topology ha { local I }
for s in @sites {
  instance "I.{{s}}" { roles [R] attr R.key "{{s}}" }
  for w in s.workers { topic "t/{{s}}/{{w}}" { definition c::T topology { use ha } } }
}
"""
    symbols = outline(parse(source), source)
    assert [(s.name, s.kind) for s in symbols] == [
        ("context", SymbolKind.CONTEXT),
        ("R", SymbolKind.ROLE),
        ("ha", SymbolKind.TOPOLOGY),
        ("for s in @sites", SymbolKind.LOOP),
    ]
    context, role, _, loop = symbols
    assert [(c.name, c.kind) for c in context.children] == [
        ("site", SymbolKind.TYPE),
        ("sites", SymbolKind.FIELD),
    ]
    assert context.span == Span(Position(0, 0), Position(3, 1))
    assert [c.name for c in role.children] == ["key"]
    assert role.selection == span(4, 5, 6)
    assert role.span == span(4, 5, 27)
    instance, inner = loop.children
    assert instance.name == "I.{{s}}"
    assert inner.name == "for w in s.workers"
    assert [c.name for c in inner.children] == ["t/{{s}}/{{w}}"]
    assert loop.span == Span(Position(6, 0), Position(9, 1))


# ---- end to end -------------------------------------------------------------


class Client:
    """A minimal LSP client over the server's stdio."""

    def __init__(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-m", "topoc", "lsp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        self.next_id = 0
        self.capabilities: dict[str, Any] = {}

    def send(self, message: dict[str, Any]) -> None:
        body = json.dumps({"jsonrpc": "2.0", **message}).encode()
        assert self.process.stdin is not None
        self.process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.process.stdin.flush()

    def receive(self) -> dict[str, Any]:
        assert self.process.stdout is not None
        length = 0
        while (line := self.process.stdout.readline().strip()) != b"":
            name, _, value = line.partition(b":")
            if name.lower() == b"content-length":
                length = int(value)
        message: dict[str, Any] = json.loads(self.process.stdout.read(length))
        return message

    def request(self, method: str, params: dict[str, Any]) -> Any:
        self.next_id += 1
        self.send({"id": self.next_id, "method": method, "params": params})
        while True:
            message = self.receive()
            if message.get("id") == self.next_id:
                return message["result"]

    def notification(self, method: str) -> dict[str, Any]:
        while True:
            message = self.receive()
            if message.get("method") == method:
                params: dict[str, Any] = message["params"]
                return params


def test_topoc_lsp_publishes_diagnostics_and_an_outline(tmp_path: Path) -> None:
    topology = tmp_path / "a.topo"
    uri = topology.as_uri()
    client = Client()
    try:
        initialized = client.request(
            "initialize",
            {"processId": None, "rootUri": tmp_path.as_uri(), "capabilities": {}},
        )
        client.capabilities = initialized["capabilities"]
        client.send({"method": "initialized", "params": {}})
        source = VALID.replace("local I", "local Nowhere")
        client.send(
            {
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {"uri": uri, "languageId": "topo", "version": 1, "text": source}
                },
            }
        )
        published = client.notification("textDocument/publishDiagnostics")
        assert published["uri"] == uri
        [diagnostic] = published["diagnostics"]
        assert diagnostic["message"] == "unknown name 'Nowhere'"
        assert diagnostic["range"] == {
            "start": {"line": 2, "character": 43},
            "end": {"line": 2, "character": 50},
        }

        client.send(
            {
                "method": "textDocument/didChange",
                "params": {
                    "textDocument": {"uri": uri, "version": 2},
                    "contentChanges": [{"text": VALID}],
                },
            }
        )
        assert client.notification("textDocument/publishDiagnostics")["diagnostics"] == []

        symbols = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
        assert [(s["name"], s["detail"]) for s in symbols] == [
            ("R", "role"),
            ("I", "instance"),
            ("t", "topic"),
        ]
        # `R` in `roles [R]`, line 2.
        position = {"line": 1, "character": 20}
        document = {"uri": uri}
        [declaration] = client.request(
            "textDocument/definition", {"textDocument": document, "position": position}
        )
        assert declaration["range"]["start"] == {"line": 0, "character": 5}
        found = client.request(
            "textDocument/references",
            {
                "textDocument": document,
                "position": position,
                "context": {"includeDeclaration": False},
            },
        )
        assert [r["range"]["start"] for r in found] == [
            {"line": 1, "character": 20},
            {"line": 1, "character": 28},
        ]
        shown = client.request(
            "textDocument/hover", {"textDocument": document, "position": position}
        )
        assert shown["contents"]["value"].startswith("```topo\nrole R {")
        legend = client.capabilities["semanticTokensProvider"]["legend"]
        encoded = client.request("textDocument/semanticTokens/full", {"textDocument": document})
        # (delta line, delta start, length, type, modifiers) per token.
        decoded = []
        line = column = 0
        data = encoded["data"]
        for k in range(0, len(data), 5):
            delta_line, delta_start, length, kind, modifiers = data[k : k + 5]
            line += delta_line
            column = delta_start if delta_line else column + delta_start
            decoded.append((line, column, length, legend["tokenTypes"][kind], modifiers))
        assert decoded == [
            (0, 5, 1, "role", 1),
            (0, 14, 3, "attribute", 1),
            (1, 9, 1, "instance", 1),
            (1, 20, 1, "role", 0),
            (1, 28, 1, "role", 0),
            (1, 30, 3, "attribute", 0),
            (2, 6, 1, "topic", 1),
            (2, 43, 1, "instance", 0),
        ]
        # Completing `local` statements' names after editing in `local `.
        edited = VALID.replace("local I", "local ")
        client.send(
            {
                "method": "textDocument/didChange",
                "params": {
                    "textDocument": {"uri": uri, "version": 3},
                    "contentChanges": [{"text": edited}],
                },
            }
        )
        completed = client.request(
            "textDocument/completion",
            {"textDocument": document, "position": {"line": 2, "character": 43}},
        )
        assert [(item["label"], item["detail"]) for item in completed["items"]] == [
            ("R", "role"),
            ("I", "instance"),
        ]
        client.request("shutdown", {})
        client.send({"method": "exit"})
        assert client.process.wait(timeout=10) == 0
    finally:
        client.process.kill()
