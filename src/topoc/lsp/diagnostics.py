"""Diagnostics for one `.topo` source: what `topoc check` reports, located as
ranges for an editor.

The compiler stops at its first error, so there is at most one error. A
topology whose `context` block has no data found for it is still parsed and
its declaration checked, with a note that its loops over the context are not.
"""

from dataclasses import dataclass
from enum import Enum

from topoc.context.schema import Schema
from topoc.dsl import syntax
from topoc.dsl.parser import parse
from topoc.errors import CompileError, ContextError, SourceLocation
from topoc.lsp.context_discovery import (
    ContextFound,
    ContextLookup,
    ContextNotFound,
    ContextUnreadable,
)
from topoc.lsp.text import Span, end_of_text, token_span
from topoc.resolve.resolver import resolve


class Severity(Enum):
    ERROR = "error"
    INFORMATION = "information"


@dataclass(frozen=True, slots=True)
class Problem:
    severity: Severity
    message: str
    span: Span


def check(source: str, context: ContextLookup) -> list[Problem]:
    """Compile `source` against the context data `context` found for it."""
    lines = source.split("\n")
    try:
        file = parse(source)
    except CompileError as error:
        return [_compile_problem(error, lines)]
    declaration = next((d for d in file.definitions if isinstance(d, syntax.ContextDef)), None)
    at_context = (
        token_span(lines, declaration.location) if declaration is not None else _first(lines)
    )
    data = None
    match context:
        case ContextNotFound(searched=searched) if declaration is not None:
            try:
                Schema(declaration)
            except CompileError as error:
                return [_compile_problem(error, lines)]
            looked = ", ".join(str(path) for path in searched) or "no file on disk"
            return [
                Problem(
                    Severity.INFORMATION,
                    f"no context data found (looked for {looked}): loops over the context "
                    "are not expanded or checked",
                    at_context,
                )
            ]
        case ContextUnreadable(path=path, message=message):
            return [Problem(Severity.ERROR, f"{path}: {message}", at_context)]
        case ContextFound(context=found):
            data = found
        case ContextNotFound():
            pass
    try:
        resolve(file, data)
    except CompileError as error:
        return [_compile_problem(error, lines)]
    except ContextError as error:
        assert isinstance(context, ContextFound)
        return [Problem(Severity.ERROR, f"{context.path}: {error}", at_context)]
    return []


def _first(lines: list[str]) -> Span:
    return token_span(lines, SourceLocation(1, 1))


def _compile_problem(error: CompileError, lines: list[str]) -> Problem:
    """The error at its token, without the "at line L, column C" an editor
    shows by placing it."""
    message = str(error)
    if error.location is None:
        end = end_of_text(lines)
        return Problem(Severity.ERROR, message, Span(end, end))
    located = f" at line {error.location.line}, column {error.location.column}"
    return Problem(
        Severity.ERROR, message.replace(located, "", 1), token_span(lines, error.location)
    )
